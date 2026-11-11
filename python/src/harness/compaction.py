"""對話壓縮：對話太長時，把較舊的歷史濃縮成檢查點。

做法參考 deepseek-harness 的 compaction：

1. 每次呼叫模型前估算請求的 token 數，超過門檻（模型上下文上限的
   threshold_ratio，預設 80%）時開始壓縮。
2. 先修剪超大的工具結果：只留開頭與結尾，中間換成省略標記。這一步
   不呼叫模型，常常修剪完就降到門檻以下，不必摘要。
3. 還是太長時，把較舊的歷史交給模型摘要成結構化的檢查點（需求、決定、
   檔案、錯誤、待辦、下一步），最近的訊息原文保留。
4. 模型回報超過上下文上限時，盡量壓縮後重試一次。

對話紀錄只往後附加、不改寫：壓縮與修剪都以 role 為 "harness" 的紀錄
附加在 JSONL 裡，原始訊息完整保留。模型實際看到的歷史由 model_history
從紀錄推算：最後一次壓縮的摘要，加上它保留的訊息與之後的訊息，並套用
修剪。TypeScript 版用同樣的規則讀取，所以兩個版本仍可接續同一段對話。
"""

import dataclasses
import functools
import json
from typing import Any

from harness import agents
from harness import datatypes
from harness import llm
from harness import sessions

RECORD_ROLE = "harness"
COMPACTION_TYPE = "compaction"
PRUNE_TYPE = "prune"
PRUNE_MARKER = "\n\n[... 工具結果中間已省略 ...]\n\n"

# 每則訊息的結構開銷（角色、分隔符號）的估計值。
_MESSAGE_OVERHEAD = 4

CHECKPOINT_PREAMBLE = (
    "以下是系統自動產生的檢查點，濃縮了較早的對話以節省上下文。把其中的"
    "內容當成已知的背景，直接延續後面的對話繼續工作，不要重述，也不要"
    "提到這個檢查點。"
)

# 摘要請求沿用 agent 的 system prompt 與原本的對話（請求的前段與平常的
# 請求相同，能命中 prompt cache），再接上這則指令。它和使用者訊息一樣以
# user 的身分送出，所以開頭要講明它來自框架，並界定「使用者」指的是誰；
# 否則模型常把這次壓縮要求寫成使用者的需求，或把 system prompt 裡 agent
# 的角色寫成使用者的身分。
SUMMARY_INSTRUCTION = """\
[harness 系統訊息：這則訊息由 harness 框架送出，不是使用者說的話。]

對話在這裡暫停。現在請你擔任對話壓縮器，把上面的對話濃縮成一份結構化的\
檢查點，讓另一個模型能接手繼續工作，而不遺漏任何必要的背景。

撰寫前先分清楚角色：
- 「使用者」只指對話中以使用者身分發言的人。
- system prompt 是 agent（也就是你）的角色設定與指示，不是使用者的身分、\
需求或偏好。之後它仍會照常提供給模型，不需要寫進檢查點。
- 這則壓縮要求和下面的格式說明不屬於對話內容，不是使用者的需求或決定，\
不要寫進任何段落。

完全照下面的 Markdown 結構輸出，每個段落都要保留且順序不變。用簡短的條列，\
不要寫成段落。沒有內容的段落寫「（無）」，不要省略段落。

## 使用者的需求與意圖
- [使用者最初與後續的目標；措辭重要時逐字引用]

## 重要背景與決定
- [做過的決定與理由、限制、使用者的偏好]

## 資料與檔案
- [查過的資料、讀寫過的檔案路徑與重點內容、工具回傳的關鍵數字]

## 錯誤與修正
- [遇到的錯誤與解決方式，以及使用者的相關回饋]

## 尚未完成的工作
- [使用者明確要求、但還沒完成的事]

## 目前進度
- [壓縮當下正在做的事]

## 下一步
- [緊接著最近一個要求的下一個動作，或「（無）」]

規則：
- 用對話中使用者所用的語言撰寫。
- 精確保留檔案路徑、指令、錯誤訊息、識別碼、數字與函式名稱。
- 忠實記錄使用者的回饋與明確指示，特別是更正。
- 不要提到這次壓縮要求，也不要提到對話被壓縮。
- 只輸出檢查點內容，不要呼叫任何工具，也不要做其他動作。
- 如果對話裡已經有 <compacted-summary> 區塊，那是先前的檢查點：不要原樣\
照抄，保留仍然成立的事實、刪除過時的內容，把新資訊合併成一份同樣結構的\
摘要。
"""


class CompactionError(Exception):
    """無法完成壓縮（沒有模型可以摘要、摘要失敗或沒有變短）。"""


@dataclasses.dataclass
class Compaction:
    """壓縮的設定。

    Attributes:
        context_window: 模型的上下文上限（token）。None 表示從 litellm
            的模型資料查詢；查不到時不會主動壓縮，但模型回報超過上限時
            仍會壓縮後重試。
        threshold_ratio: 請求超過上限的這個比例時開始壓縮。
        retain_ratio: 壓縮後原文保留的近期對話，佔上限的比例。
        summary_model: 負責摘要的模型，None 表示用 agent 的模型。
        summary_max_tokens: 摘要的輸出上限。
        prune_chars: 工具結果超過這個字數時，壓縮會先把它修剪成頭尾。
        prune_head: 修剪後保留的開頭字數。
        prune_tail: 修剪後保留的結尾字數。
        auto: 是否自動壓縮（含超過上限時的重試）；False 時只能手動壓縮。
        max_overflow_retries: 模型回報超過上下文上限時，壓縮後最多重試
            幾次；0 表示不重試。
    """

    context_window: int | None = None
    threshold_ratio: float = 0.8
    retain_ratio: float = 0.15
    summary_model: str | None = None
    summary_max_tokens: int = 8000
    prune_chars: int = 8192
    prune_head: int = 4096
    prune_tail: int = 1024
    auto: bool = True
    max_overflow_retries: int = 1

    def __post_init__(self) -> None:
        """檢查設定值。"""
        if not 0 < self.retain_ratio < self.threshold_ratio <= 1:
            raise ValueError(
                "壓縮設定必須符合 0 < retain_ratio < threshold_ratio <= 1"
            )
        if self.context_window is not None and self.context_window <= 0:
            raise ValueError("context_window 必須是正整數")
        if self.summary_max_tokens <= 0:
            raise ValueError("summary_max_tokens 必須是正整數")
        if min(self.prune_head, self.prune_tail) < 0:
            raise ValueError("prune_head 與 prune_tail 不能是負數")
        pruned_size = self.prune_head + self.prune_tail + len(PRUNE_MARKER)
        if pruned_size >= self.prune_chars:
            raise ValueError(
                "prune_head + prune_tail 加上省略標記必須小於 prune_chars，"
                "否則修剪不會讓結果變短"
            )
        if self.max_overflow_retries < 0:
            raise ValueError("max_overflow_retries 不能是負數")


@dataclasses.dataclass
class CompactionResult:
    """一次壓縮的結果。

    Attributes:
        reason: threshold（超過門檻）、overflow（模型回報超過上限）或
            manual（手動）。
        pruned: 修剪了幾個工具結果。
        summarized: 被摘要取代的訊息數；只靠修剪就足夠時為 0。
        tokens_before: 壓縮前估計的 token 數。
        tokens_after: 壓縮後估計的 token 數。
        usage: 摘要這次模型呼叫的 token 用量。
    """

    reason: str
    pruned: int
    summarized: int
    tokens_before: int
    tokens_after: int
    usage: datatypes.Usage = dataclasses.field(default_factory=datatypes.Usage)


@dataclasses.dataclass
class _Item:
    """模型看得到的一則訊息，以及它在紀錄裡的位置（摘要為 None）。"""

    index: int | None
    message: datatypes.Message


def model_history(
    messages: list[datatypes.Message],
) -> list[datatypes.Message]:
    """從對話紀錄推算模型實際看到的歷史。

    Args:
        messages: session 裡的完整紀錄（含壓縮與修剪紀錄）。

    Returns:
        要送給模型的訊息（不含 system prompt）。
    """
    return [item.message for item in _view(messages)]


def is_record(message: datatypes.Message) -> bool:
    """是不是框架自己的紀錄（壓縮、修剪），而不是對話訊息。

    Args:
        message: 對話紀錄裡的一則。

    Returns:
        role 為 harness 時為 True。
    """
    return message.get("role") == RECORD_ROLE


def estimate_tokens(
    messages: list[datatypes.Message],
    tools: list[dict[str, Any]] | None = None,
) -> int:
    """粗估一組訊息（與工具定義）的 token 數。

    沒有可用的 tokenizer，所以用字元數估計：英數字約 4 個字元一個
    token，中日韓等文字一個字約一個 token，每則訊息另加結構開銷。

    Args:
        messages: 要估計的訊息。
        tools: 工具定義。

    Returns:
        估計的 token 數。
    """
    total = 0
    for message in messages:
        total += _MESSAGE_OVERHEAD + _text_tokens(_message_text(message))
    if tools:
        total += _text_tokens(json.dumps(tools, ensure_ascii=False))
    return total


def context_window(settings: Compaction, model: str) -> int | None:
    """取得模型的上下文上限。

    Args:
        settings: 壓縮設定。
        model: litellm 的模型名稱。

    Returns:
        token 數；設定沒寫、litellm 也查不到時為 None。
    """
    if settings.context_window:
        return settings.context_window
    return _lookup_context_window(model)


class Compactor:
    """一次 run 裡負責壓縮的物件，記住最近一次模型回報的實際用量。

    模型回報的 input + output token 是最準確的量測，所以估算時以它為
    基準，只對之後新增的訊息用字元數估計；沒有回報時才全部用估計。
    """

    def __init__(self, settings: Compaction, model_fn: llm.ModelFn) -> None:
        self.settings = settings
        self._model_fn = model_fn
        # (當時模型看到的歷史長度, 實際 token 數)
        self._measured: tuple[int, int] | None = None

    def record_usage(
        self, session_messages: list[datatypes.Message], usage: datatypes.Usage
    ) -> None:
        """在模型回應存進紀錄後呼叫，記下實際用量。

        Args:
            session_messages: 目前的完整紀錄。
            usage: 剛剛那次回應的用量。
        """
        tokens = usage.input_tokens + usage.output_tokens
        if tokens <= 0:
            return
        self._measured = (len(model_history(session_messages)), tokens)

    def estimate(
        self,
        system: str,
        history: list[datatypes.Message],
        tools: list[dict[str, Any]],
    ) -> int:
        """估計下一次請求的 token 數。

        Args:
            system: system prompt。
            history: 模型看得到的歷史。
            tools: 工具定義。

        Returns:
            估計的 token 數。
        """
        if self._measured is not None and self._measured[0] <= len(history):
            length, tokens = self._measured
            return tokens + estimate_tokens(history[length:])
        return estimate_tokens(_with_system(system, history), tools)

    async def compact_if_needed(
        self,
        agent: agents.Agent,
        session: sessions.Session,
        system: str,
        tools: list[dict[str, Any]],
    ) -> CompactionResult | None:
        """請求超過門檻時壓縮。

        Args:
            agent: 目前負責對話的 agent。
            session: 對話紀錄。
            system: system prompt。
            tools: 工具定義。

        Returns:
            壓縮結果；不需要壓縮或沒有東西可以壓縮時為 None。

        Raises:
            CompactionError: 摘要失敗。
        """
        model = agent.model or llm.default_model()
        limit = self._threshold(agent, model)
        if limit is None:
            return None
        history = model_history(session.messages)
        before = self.estimate(system, history, tools)
        if before <= limit:
            return None
        return await self._compact(
            agent, session, system, tools, "threshold", before, limit
        )

    async def compact(
        self,
        agent: agents.Agent,
        session: sessions.Session,
        system: str,
        tools: list[dict[str, Any]],
        reason: str,
    ) -> CompactionResult | None:
        """不管門檻直接壓縮（手動，或模型回報超過上限時）。

        會盡量壓縮，只保留最近一段（最後一則使用者訊息或 assistant
        回覆，與它的工具結果）。

        Args:
            agent: 目前負責對話的 agent。
            session: 對話紀錄。
            system: system prompt。
            tools: 工具定義。
            reason: manual 或 overflow。

        Returns:
            壓縮結果；沒有東西可以壓縮時為 None。

        Raises:
            CompactionError: 摘要失敗。
        """
        history = model_history(session.messages)
        before = self.estimate(system, history, tools)
        return await self._compact(
            agent, session, system, tools, reason, before, None
        )

    def _threshold(self, agent: agents.Agent, model: str) -> int | None:
        """開始壓縮的 token 數：上限的比例，再扣掉預留的輸出。"""
        window = context_window(self.settings, model)
        if window is None:
            return None
        reserved = int(agent.model_params.get("max_tokens") or 0)
        return int(window * self.settings.threshold_ratio) - reserved

    async def _compact(
        self,
        agent: agents.Agent,
        session: sessions.Session,
        system: str,
        tools: list[dict[str, Any]],
        reason: str,
        before: int,
        limit: int | None,
    ) -> CompactionResult | None:
        """先修剪、不夠再摘要。limit 為 None 表示一定要摘要。"""
        pruned = self._prune(session)
        if pruned:
            self._measured = None
        history = model_history(session.messages)
        after_prune = estimate_tokens(_with_system(system, history), tools)
        if limit is not None and after_prune <= limit:
            return CompactionResult(reason, pruned, 0, before, after_prune)

        items = _view(session.messages)
        split = self._choose_split(agent, items, maximal=limit is None)
        if split is None:
            if pruned:
                return CompactionResult(reason, pruned, 0, before, after_prune)
            return None
        region = [item.message for item in items[:split]]
        summary, usage = await self._summarize(agent, system, region, tools)
        checkpoint = _checkpoint_message(summary)
        if estimate_tokens([checkpoint]) >= estimate_tokens(region):
            raise CompactionError("摘要沒有比原本的對話短，放棄這次壓縮")
        session.append(
            {
                "role": RECORD_ROLE,
                "type": COMPACTION_TYPE,
                "keep_from": items[split].index,
                "summary": summary,
                "reason": reason,
            }
        )
        self._measured = None
        history = model_history(session.messages)
        after = estimate_tokens(_with_system(system, history), tools)
        summarized = sum(1 for item in items[:split] if item.index is not None)
        return CompactionResult(
            reason, pruned, summarized, before, after, usage
        )

    def _prune(self, session: sessions.Session) -> int:
        """修剪模型已經看過的超大工具結果，回傳修剪的數量。

        最後一次模型回應之後的工具結果模型還沒看過，不修剪。
        """
        history = model_history(session.messages)
        last_assistant = max(
            (i for i, m in enumerate(history) if m.get("role") == "assistant"),
            default=-1,
        )
        replacements: dict[str, str] = {}
        for message in history[:last_assistant]:
            content = message.get("content")
            if message.get("role") != "tool" or not isinstance(content, str):
                continue
            if len(content) <= self.settings.prune_chars:
                continue
            head = content[: self.settings.prune_head]
            tail = content[len(content) - self.settings.prune_tail :]
            if not self.settings.prune_tail:
                tail = ""
            replacements[message["tool_call_id"]] = head + PRUNE_MARKER + tail
        if replacements:
            session.append(
                {
                    "role": RECORD_ROLE,
                    "type": PRUNE_TYPE,
                    "results": replacements,
                }
            )
        return len(replacements)

    def _choose_split(
        self, agent: agents.Agent, items: list[_Item], maximal: bool
    ) -> int | None:
        """選擇保留的起點：items[split:] 原文保留，前面的交給摘要。

        起點只能是使用者訊息或 assistant 訊息（工具結果一定跟著它的
        呼叫），而且前面至少要有一則真正的訊息可以摘要。一般情況保留
        不超過 retain_ratio 的最多訊息；maximal 時只保留最後一段。
        """
        candidates = [
            i
            for i in range(1, len(items))
            if items[i].index is not None
            and items[i].message.get("role") in ("user", "assistant")
            and any(item.index is not None for item in items[:i])
        ]
        if not candidates:
            return None
        if maximal:
            return candidates[-1]
        model = agent.model or llm.default_model()
        window = context_window(self.settings, model)
        if window is None:
            return candidates[-1]
        budget = int(window * self.settings.retain_ratio)
        for split in candidates:
            tail = [item.message for item in items[split:]]
            if estimate_tokens(tail) <= budget:
                return split
        return candidates[-1]

    async def _summarize(
        self,
        agent: agents.Agent,
        system: str,
        region: list[datatypes.Message],
        tools: list[dict[str, Any]],
    ) -> tuple[str, datatypes.Usage]:
        """請模型把 region 摘要成檢查點。"""
        model = (
            self.settings.summary_model or agent.model or llm.default_model()
        )
        request = [
            *_with_system(system, region),
            {"role": "user", "content": SUMMARY_INSTRUCTION},
        ]
        params = {
            **agent.model_params,
            "max_tokens": self.settings.summary_max_tokens,
        }
        response = None
        try:
            async for item in self._model_fn(model, request, tools, params):
                if not isinstance(item, str):
                    response = item
        except Exception as error:
            raise CompactionError(
                f"摘要時模型出錯：{type(error).__name__}: {error}"
            ) from error
        text = ""
        if response is not None:
            text = str(response.message.get("content") or "").strip()
        if not text:
            raise CompactionError("摘要模型沒有回傳任何文字")
        usage = response.usage if response is not None else datatypes.Usage()
        return text, usage


def _view(messages: list[datatypes.Message]) -> list[_Item]:
    """推算模型看得到的訊息，並記住每則在紀錄裡的位置。"""
    last = None
    replacements: dict[str, str] = {}
    for index, message in enumerate(messages):
        if not is_record(message):
            continue
        if message.get("type") == COMPACTION_TYPE:
            last = index
        elif message.get("type") == PRUNE_TYPE:
            replacements.update(message.get("results") or {})

    items: list[_Item] = []
    start = 0
    if last is not None:
        record = messages[last]
        items.append(_Item(None, _checkpoint_message(record["summary"])))
        start = int(record.get("keep_from", last))
    for index in range(start, len(messages)):
        message = messages[index]
        if is_record(message):
            continue
        call_id = message.get("tool_call_id")
        if message.get("role") == "tool" and call_id in replacements:
            message = {**message, "content": replacements[call_id]}
        items.append(_Item(index, message))
    return _drop_orphan_results(items)


def _drop_orphan_results(items: list[_Item]) -> list[_Item]:
    """拿掉找不到對應呼叫的工具結果，模型端點不接受這種訊息。"""
    called = {
        call.get("id")
        for item in items
        for call in item.message.get("tool_calls") or []
    }
    return [
        item
        for item in items
        if item.message.get("role") != "tool"
        or item.message.get("tool_call_id") in called
    ]


def _checkpoint_message(summary: str) -> datatypes.Message:
    """把摘要包成取代舊歷史的使用者訊息。"""
    return {
        "role": "user",
        "content": (
            f"{CHECKPOINT_PREAMBLE}\n\n"
            f"<compacted-summary>\n{summary}\n</compacted-summary>"
        ),
    }


def _with_system(
    system: str, messages: list[datatypes.Message]
) -> list[datatypes.Message]:
    """有 system prompt 時加在最前面。"""
    if not system:
        return list(messages)
    return [{"role": "system", "content": system}, *messages]


def _message_text(message: datatypes.Message) -> str:
    """取出一則訊息裡會送給模型的文字（含工具呼叫的名稱與參數）。"""
    parts = []
    content = message.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        parts.append(str(function.get("name") or ""))
        parts.append(str(function.get("arguments") or ""))
    return "\n".join(parts)


def _text_tokens(text: str) -> int:
    """英數字約 4 個字元一個 token，其他文字一個字約一個 token。"""
    ascii_chars = sum(1 for char in text if char.isascii())
    return (ascii_chars + 3) // 4 + (len(text) - ascii_chars)


@functools.lru_cache(maxsize=64)
def _lookup_context_window(model: str) -> int | None:
    """向 litellm 查詢模型的上下文上限，查不到時為 None。"""
    import litellm

    try:
        info = litellm.get_model_info(model)
    # litellm 對不認識的模型會丟出各種例外，一律當成查不到。
    except Exception:  # noqa: BLE001
        return None
    value = info.get("max_input_tokens") or info.get("max_tokens")
    return int(value) if value else None
