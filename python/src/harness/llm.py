"""模型呼叫層：透過 litellm 連接任何模型。

兩種連線方式：
    直接連供應商：模型名稱用 litellm 的格式（anthropic/claude-opus-5-5），
        金鑰用該供應商的環境變數（ANTHROPIC_API_KEY…）。
    連 OpenAI 相容端點：設定 HARNESS_BASE_URL（與 HARNESS_API_KEY），
        模型名稱照端點的寫法（LiteLLM Proxy 就是它設定檔裡的
        model_name），會原樣送出。這與 TypeScript 版使用相同的環境變數。

主迴圈只認得 ModelFn 這個介面，所以測試可以換成假的模型函式，完全
不需要連網。

使用者身分：連 OpenAI 相容端點時，acting_as() 設定的 Caller 會附在這一輪
的每個模型呼叫上（OpenAI 的 user 欄位與 LiteLLM 的 x-litellm-end-user-id
等標頭），模型中台才能依使用者記帳、套用額度與限流。直接連供應商時不送，
帳號名稱不會交給外部的模型供應商。
"""

import contextlib
import contextvars
import dataclasses
import json
import os
from collections.abc import AsyncIterator
from collections.abc import Callable
from collections.abc import Iterator
from typing import Any

from harness import datatypes
from harness import providers

FALLBACK_MODEL = "anthropic/claude-opus-5-5"
# litellm 文件裡連 LiteLLM Proxy 的寫法；送給端點前拿掉。
PROXY_PREFIX = "litellm_proxy/"
EMBED_BATCH_SIZE = 64
# 沒被 litellm 轉成 ContextWindowExceededError 時，用來辨識的錯誤訊息。
_OVERFLOW_PATTERNS = (
    "context_length_exceeded",
    "maximum context length",
    "context window",
    "prompt is too long",
    "input is too long",
    "exceeds the context",
)

# LiteLLM Proxy 認得的標頭：使用者（customer / end user）、同一段對話的
# 追蹤碼，以及另外記在花費紀錄的資訊。
END_USER_HEADER = "x-litellm-end-user-id"
TRACE_HEADER = "x-litellm-trace-id"
METADATA_HEADER = "x-litellm-spend-logs-metadata"


@dataclasses.dataclass(frozen=True)
class Caller:
    """發出這一輪請求的人，連模型中台時附在每個模型與 MCP 呼叫上。

    Attributes:
        user: 使用者識別（帳號名稱），中台依它記帳、套用額度與限流。
        session: 對話識別碼，中台用它把同一段對話的呼叫串起來。
        metadata: 另外記在中台花費紀錄的資訊（例如 agent 名稱）。
    """

    user: str
    session: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    def headers(self) -> dict[str, str]:
        """要附在 HTTP 請求上的標頭（值都是 ASCII）。"""
        headers = {END_USER_HEADER: _ascii(self.user)}
        if self.session:
            headers[TRACE_HEADER] = _ascii(self.session)
        if self.metadata:
            headers[METADATA_HEADER] = json.dumps(dict(self.metadata))
        return headers


_CALLER: contextvars.ContextVar[Caller | None] = contextvars.ContextVar(
    "harness_caller", default=None
)


def current_caller() -> Caller | None:
    """目前這個 asyncio task（與它開出的 task）的 Caller；沒有設定時為 None。"""
    return _CALLER.get()


def set_caller(caller: Caller | None) -> None:
    """設定目前這個 asyncio task 的 Caller，直到 task 結束。

    只在專門執行一輪對話的 task 裡使用（網頁版每一輪都是自己的 task）；
    其他地方用 acting_as，離開時會恢復原本的值。
    """
    _CALLER.set(caller)


@contextlib.contextmanager
def acting_as(caller: Caller | None) -> Iterator[None]:
    """在這段程式裡發出的模型與 MCP 呼叫都附上 caller 的身分。

    用 contextvars 傳遞：同一個 asyncio task 以及它開出的 task（subagent、
    背景工具）都看得到，其他人的對話不受影響。

    Args:
        caller: 發出請求的人；None 表示不附身分。
    """
    token = _CALLER.set(caller)
    try:
        yield
    finally:
        _CALLER.reset(token)


def identity_options(*, rerank: bool = False) -> dict[str, Any]:
    """連 OpenAI 相容端點時，要加進 litellm 參數的使用者身分。

    user 放在 extra_body：litellm 對它不認得的模型名稱（中台上自訂的
    名稱）會把 user 參數當成不支援而丟掉，extra_body 則原樣送出。

    Args:
        rerank: 給 rerank 用。rerank 沒有 user 欄位，標頭也要用 headers
            參數才會送出。

    Returns:
        litellm 的參數；沒有設定端點或沒有 Caller 時是空的。
    """
    caller = current_caller()
    if caller is None or not os.environ.get("HARNESS_BASE_URL"):
        return {}
    if rerank:
        return {"headers": caller.headers()}
    return {
        "extra_headers": caller.headers(),
        "extra_body": {"user": caller.user},
    }


def _ascii(value: str) -> str:
    """HTTP 標頭只能放 ASCII：其他字元換成 JSON 的 \\u 寫法。"""
    return json.dumps(value)[1:-1] if not value.isascii() else value


# 模型函式：依序產生文字片段（str），最後產生一個 ModelResponse。
ModelFn = Callable[
    [str, list[datatypes.Message], list[dict[str, Any]], dict[str, Any]],
    AsyncIterator[str | datatypes.ModelResponse],
]


def default_model() -> str:
    """取得預設模型名稱（litellm 格式，例如 openai/gpt-5）。

    Returns:
        環境變數 HARNESS_MODEL 的值。沒設定時，直接連供應商的話用第一家
        已經設定金鑰的供應商的預設模型（只設了 OPENAI_API_KEY 就用
        OpenAI 的模型）；都沒有時用 FALLBACK_MODEL。
    """
    configured = os.environ.get("HARNESS_MODEL")
    if configured:
        return configured
    if not providers.uses_endpoint():
        detected = providers.detect()
        if detected is not None:
            return detected.default_model
    return FALLBACK_MODEL


async def complete(
    model: str,
    messages: list[datatypes.Message],
    tools: list[dict[str, Any]],
    params: dict[str, Any],
) -> AsyncIterator[str | datatypes.ModelResponse]:
    """用串流方式呼叫模型。

    Args:
        model: litellm 的模型名稱。
        messages: 完整的請求訊息（含 system）。
        tools: OpenAI 格式的工具定義。
        params: 額外傳給 litellm 的參數（max_tokens、reasoning_effort…）。

    Yields:
        文字片段，最後是一個完整的 ModelResponse。
    """
    # litellm 載入很慢，延後到真的要呼叫模型時才 import。
    import litellm

    # 關掉 litellm 對不認識的模型名稱（本地模型、自架端點）印出的提示。
    litellm.suppress_debug_info = True

    kwargs: dict[str, Any] = {
        "model": _endpoint_model(model),
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        # 換模型時，自動略過該模型不支援的參數。
        "drop_params": True,
        **_endpoint_options(),
        **identity_options(),
        **params,
    }
    if tools:
        kwargs["tools"] = tools
    chunks = []
    stream = await litellm.acompletion(**kwargs)
    async for chunk in stream:
        chunks.append(chunk)
        text = _text_delta(chunk)
        if text:
            yield text
    full = litellm.stream_chunk_builder(chunks, messages=messages)
    yield to_model_response(full)


async def embed(model: str, texts: list[str]) -> list[list[float]]:
    """用 embedding 模型把文字轉成向量。

    和 complete 一樣，有設定 HARNESS_BASE_URL 時改連那個端點。

    Args:
        model: litellm 的 embedding 模型名稱（例如
            openai/text-embedding-3-small）。
        texts: 要轉換的文字。

    Returns:
        每段文字的向量，順序與 texts 相同。
    """
    import litellm

    litellm.suppress_debug_info = True
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[start : start + EMBED_BATCH_SIZE]
        response = await litellm.aembedding(
            model=_endpoint_model(model),
            input=batch,
            **_endpoint_options(),
            **identity_options(),
        )
        rows = sorted(response.data, key=lambda row: _field(row, "index"))
        vectors += [list(_field(row, "embedding")) for row in rows]
    return vectors


async def rerank(model: str, query: str, documents: list[str]) -> list[float]:
    """用 reranker 模型評估每段文件與查詢的相關度。

    有設定 HARNESS_BASE_URL 時改連那個端點的 /v1/rerank（LiteLLM Proxy
    提供這個端點）。

    Args:
        model: litellm 的 rerank 模型名稱（例如 cohere/rerank-v3.5）。
        query: 查詢。
        documents: 要評估的文件。

    Returns:
        每段文件的相關度，順序與 documents 相同，越大越相關。
    """
    import litellm

    litellm.suppress_debug_info = True
    if not documents:
        return []
    if os.environ.get("HARNESS_BASE_URL"):
        model = model.removeprefix(PROXY_PREFIX)
    response = await litellm.arerank(
        model=model,
        query=query,
        documents=documents,
        return_documents=False,
        **_rerank_endpoint_options(),
        **identity_options(rerank=True),
    )
    scores = [0.0] * len(documents)
    for row in response.results or []:
        scores[_field(row, "index")] = float(_field(row, "relevance_score"))
    return scores


def _endpoint_model(model: str) -> str:
    """連 OpenAI 相容端點時，讓模型名稱原樣送到端點。

    端點模式下 litellm 會把開頭的 openai/ 當成供應商拿掉（寫
    openai/text-embedding-3-small，端點收到的是 text-embedding-3-small），
    其他開頭則保留，兩種寫法的結果不一致。這裡固定在前面多加一層
    openai/，litellm 只拿掉這一層，端點收到的就是使用者寫的名稱。

    litellm_proxy/ 開頭是 litellm 文件裡連 LiteLLM Proxy 的寫法，Proxy
    的模型名稱不會這樣開頭，所以先拿掉。

    Args:
        model: 設定裡寫的模型名稱。

    Returns:
        交給 litellm 的模型名稱；沒有設定 HARNESS_BASE_URL 時原樣回傳。
    """
    if not os.environ.get("HARNESS_BASE_URL"):
        return model
    return f"openai/{model.removeprefix(PROXY_PREFIX)}"


def _rerank_endpoint_options() -> dict[str, Any]:
    """rerank 走 LiteLLM Proxy 的 /v1/rerank。

    litellm 會自己在網址後面加上 /v1/rerank，所以 HARNESS_BASE_URL 結尾
    的 /v1（OpenAI 相容端點常見的寫法）要先拿掉。
    """
    options = _endpoint_options()
    if not options:
        return {}
    base_url = options["api_base"].rstrip("/")
    base_url = base_url.removesuffix("/v1")
    return {
        **options,
        "api_base": base_url,
        "custom_llm_provider": "litellm_proxy",
    }


def _field(row: Any, name: str) -> Any:
    """litellm 的 embedding 結果可能是 dict 或物件，兩種都支援。"""
    if isinstance(row, dict):
        return row[name]
    return getattr(row, name)


def is_context_overflow(error: BaseException) -> bool:
    """判斷模型錯誤是不是「請求超過上下文上限」。

    litellm 會把多數供應商的這類錯誤轉成 ContextWindowExceededError；
    自架端點等沒被轉換的情況，再用錯誤訊息裡的常見寫法判斷。

    Args:
        error: 呼叫模型時發生的例外。

    Returns:
        是超過上下文上限的錯誤時為 True。
    """
    if type(error).__name__ == "ContextWindowExceededError":
        return True
    text = str(error).lower()
    return any(pattern in text for pattern in _OVERFLOW_PATTERNS)


# 模型中台（LiteLLM Proxy）回覆的錯誤裡辨識原因用的字樣。
_BUDGET_PATTERNS = (
    "budget has been exceeded",
    "over budget",
    "budget_exceeded",
)
_QUOTA_PATTERNS = ("insufficient_quota", "exceeded your current quota")
_MODEL_DENIED_PATTERNS = (
    "not allowed to access model",
    "not allowed to call model",
)
_UNKNOWN_MODEL_PATTERNS = (
    "invalid model name",
    "model not found",
    "no deployments",
)


def explain_error(error: BaseException) -> str | None:
    """把模型呼叫失敗的原因換成一般使用者看得懂的說明。

    主要對應模型中台（LiteLLM Proxy）的回覆：額度用完、限流（429）、沒有
    使用這個模型的權限、中台沒有這個模型、金鑰被拒絕。

    Args:
        error: 呼叫模型時發生的例外。

    Returns:
        說明文字；認不出原因時為 None（呼叫端顯示原始訊息）。
    """
    text = str(error).lower()
    status = getattr(error, "status_code", None)
    names = {item.__name__ for item in type(error).__mro__}
    if any(pattern in text for pattern in _BUDGET_PATTERNS) or (
        "BudgetExceededError" in names
    ):
        return (
            "你的模型使用額度已經用完，暫時不能使用模型。"
            "請聯絡管理者調整額度，或等額度重新計算後再試。"
        )
    if any(pattern in text for pattern in _QUOTA_PATTERNS):
        return "模型服務的額度已經用完，請聯絡管理者。"
    if status == 429 or "RateLimitError" in names:
        wait = _retry_after(error)
        when = f"等 {wait} 秒" if wait else "稍等一分鐘"
        return (
            "現在使用的人太多，或你短時間內送出太多請求，模型中台暫時限制了"
            f"請求。請{when}後再按「重試」。"
        )
    if any(pattern in text for pattern in _MODEL_DENIED_PATTERNS):
        return (
            "你沒有使用這個模型的權限。請在輸入框的模型選單換一個模型，"
            "或聯絡管理者開通。"
        )
    if any(pattern in text for pattern in _UNKNOWN_MODEL_PATTERNS):
        return "模型中台找不到這個模型，請換一個模型，或請管理者確認模型名稱。"
    if status == 401 or "AuthenticationError" in names:
        return "模型中台拒絕了連線金鑰，請聯絡管理者確認模型連線設定。"
    return None


def _retry_after(error: BaseException) -> int | None:
    """429 回覆的 Retry-After（秒）；沒有或看不懂時為 None。"""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        value = headers.get("retry-after")
    except Exception:  # noqa: BLE001 - 各家 response 物件不同，拿不到就算了
        return None
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return None
    return seconds if 0 < seconds <= 3600 else None


def _endpoint_options() -> dict[str, Any]:
    """有設定 HARNESS_BASE_URL 時，改連那個 OpenAI 相容端點。"""
    base_url = os.environ.get("HARNESS_BASE_URL")
    if not base_url:
        return {}
    return {
        "api_base": base_url,
        # 本機端點（Ollama 等）不需要金鑰，但 litellm 要求一定要有值。
        "api_key": os.environ.get("HARNESS_API_KEY") or "not-needed",
        "custom_llm_provider": "openai",
    }


def to_model_response(response: Any) -> datatypes.ModelResponse:
    """把 litellm 的回應轉成框架的 ModelResponse。

    Args:
        response: litellm 的 ModelResponse 物件。

    Returns:
        框架的 ModelResponse，其中 message 可以直接存進對話紀錄。
    """
    choice = response.choices[0]
    raw = choice.message.model_dump(exclude_none=True)
    raw_calls = raw.get("tool_calls") or []
    message: datatypes.Message = {
        "role": "assistant",
        "content": raw.get("content"),
    }
    if not raw_calls and message["content"] is None:
        message["content"] = ""
    if raw_calls:
        message["tool_calls"] = [_clean_call(call) for call in raw_calls]
    # Claude 的 thinking 區塊必須原樣傳回，否則下一次請求會被拒絕。
    if raw.get("thinking_blocks"):
        message["thinking_blocks"] = raw["thinking_blocks"]
    usage = getattr(response, "usage", None)
    return datatypes.ModelResponse(
        message=message,
        tool_calls=[_parse_call(call) for call in raw_calls],
        usage=datatypes.Usage(
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        ),
        finish_reason=choice.finish_reason or "stop",
    )


def _text_delta(chunk: Any) -> str:
    """取出一個串流片段裡的文字。"""
    if not chunk.choices:
        return ""
    return getattr(chunk.choices[0].delta, "content", None) or ""


def _clean_call(call: dict[str, Any]) -> dict[str, Any]:
    """只保留要送回模型的 tool call 欄位。"""
    function = call.get("function") or {}
    cleaned = {
        "id": call.get("id"),
        "type": "function",
        "function": {
            "name": function.get("name"),
            "arguments": function.get("arguments") or "{}",
        },
    }
    # Gemini 等模型把必須傳回的簽章放在這個欄位。
    if call.get("provider_specific_fields"):
        cleaned["provider_specific_fields"] = call["provider_specific_fields"]
    return cleaned


def _parse_call(call: dict[str, Any]) -> datatypes.ToolCall:
    """把 tool call 的參數字串解析成 dict。"""
    function = call.get("function") or {}
    name = function.get("name") or ""
    raw_arguments = function.get("arguments") or "{}"
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as error:
        return datatypes.ToolCall(call.get("id") or "", name, {}, str(error))
    if not isinstance(arguments, dict):
        return datatypes.ToolCall(
            call.get("id") or "", name, {}, "參數必須是 JSON 物件"
        )
    return datatypes.ToolCall(call.get("id") or "", name, arguments)
