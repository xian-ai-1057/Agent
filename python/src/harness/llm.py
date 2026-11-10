"""模型呼叫層：透過 litellm 連接任何模型。

兩種連線方式：
    直接連供應商：模型名稱用 litellm 的格式（anthropic/claude-opus-5-5），
        金鑰用該供應商的環境變數（ANTHROPIC_API_KEY…）。
    連 OpenAI 相容端點：設定 HARNESS_BASE_URL（與 HARNESS_API_KEY），
        模型名稱照端點的寫法（LiteLLM Proxy 就是它設定檔裡的
        model_name），會原樣送出。這與 TypeScript 版使用相同的環境變數。

主迴圈只認得 ModelFn 這個介面，所以測試可以換成假的模型函式，完全
不需要連網。
"""

import json
import os
from collections.abc import AsyncIterator
from collections.abc import Callable
from typing import Any

from harness import datatypes

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

# 模型函式：依序產生文字片段（str），最後產生一個 ModelResponse。
ModelFn = Callable[
    [str, list[datatypes.Message], list[dict[str, Any]], dict[str, Any]],
    AsyncIterator[str | datatypes.ModelResponse],
]


def default_model() -> str:
    """取得預設模型名稱（litellm 格式，例如 openai/gpt-5）。

    Returns:
        環境變數 HARNESS_MODEL 的值，沒設定時用 FALLBACK_MODEL。
    """
    return os.environ.get("HARNESS_MODEL") or FALLBACK_MODEL


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
            model=_endpoint_model(model), input=batch, **_endpoint_options()
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
