"""內建的訊息防護：個資遮蔽、提示注入偵測、禁用詞。

這些都是掛在 pre_user_message / pre_reply 事件上的 hooks，在訊息交給
模型前、回覆送給使用者前檢查，模型無法繞過：

    hooks = harness.Hooks()
    guardrails.install(hooks, mask_pii=True, block_injection=True)

偵測方式是規則比對，能擋下常見的寫法，但不保證完整。需要更嚴格的
檢查時，可以在同樣的事件上掛自己的處理函式（例如呼叫內容安全服務）。
"""

import re

from harness import datatypes
from harness import hooks as hooks_lib

INJECTION_REASON = "這則訊息看起來想要改變助手的設定或取得內部指示，已被擋下。"
BLOCKED_WORD_REASON = "訊息包含不允許的內容，已被擋下。"

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# 台灣手機號碼：09 開頭共 10 碼，允許中間有連字號或空白。
_TW_MOBILE = re.compile(r"(?<!\d)09\d{2}[-\s]?\d{3}[-\s]?\d{3}(?!\d)")
# 台灣身分證字號與居留證號：1 個英文字母 + 1 或 2（新式居留證 8、9）
# + 8 碼數字。
_TW_ID = re.compile(r"(?<![A-Za-z0-9])[A-Z][1289]\d{8}(?!\d)")
# 信用卡號：13–19 碼，可用連字號或空白分組，另外以 Luhn 驗證過濾誤判。
_CARD = re.compile(r"(?<!\d)\d(?:[-\s]?\d){12,18}(?!\d)")

_INJECTION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"ignore\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above)"
        r"\s+(?:instructions|prompts?|rules)",
        r"disregard\s+(?:all\s+|the\s+)?(?:previous|prior|above|your)"
        r"\s+(?:instructions|prompts?|rules)",
        r"(?:reveal|show|print|repeat)\s+(?:me\s+)?(?:your|the)\s+"
        r"(?:system\s+prompt|instructions|hidden\s+prompt)",
        r"you\s+are\s+now\s+(?:in\s+)?(?:developer|dan|jailbreak)\s*mode",
        r"(?:忽略|無視|忘記|忘掉)(?:你|妳)?(?:之前|先前|以上|上面|前面|所有|全部)"
        r"(?:的|所有的)?(?:指示|指令|規則|設定|提示)",
        r"(?:顯示|告訴我|印出|輸出|列出|重複)(?:你|妳)?的?"
        r"(?:系統提示|系統指令|system\s*prompt|角色指令|內部指示)",
        r"(?:進入|切換到|啟用)(?:開發者|開發人員|越獄)模式",
    )
]

_MASKS = (
    (_EMAIL, "[已遮蔽 Email]"),
    (_TW_ID, "[已遮蔽身分證字號]"),
    (_TW_MOBILE, "[已遮蔽手機號碼]"),
)


def mask_pii(text: str) -> str:
    """把文字裡的個資換成遮蔽標記。

    涵蓋 Email、台灣手機號碼、身分證字號（含居留證號）與通過 Luhn
    驗證的信用卡號。

    Args:
        text: 要處理的文字。

    Returns:
        遮蔽後的文字；沒有個資時原樣回傳。
    """
    return _apply_masks(text)


def detect_injection(text: str) -> bool:
    """判斷文字是否像是提示注入（要求忽略指示、洩漏系統提示等）。

    Args:
        text: 使用者的訊息。

    Returns:
        符合任何一個已知寫法時為 True。
    """
    return any(pattern.search(text) for pattern in _INJECTION_PATTERNS)


def find_blocked_word(text: str, words: list[str]) -> str | None:
    """找出文字裡第一個出現的禁用詞（不分大小寫）。

    Args:
        text: 要檢查的文字。
        words: 禁用詞列表。

    Returns:
        找到的禁用詞，沒有則為 None。
    """
    lowered = text.lower()
    for word in words:
        if word and word.lower() in lowered:
            return word
    return None


def install(
    hooks: hooks_lib.Hooks,
    *,
    mask_pii: bool = False,
    block_injection: bool = False,
    blocked_words: list[str] | None = None,
) -> hooks_lib.Hooks:
    """把選用的防護掛到 hooks 上。

    - mask_pii：使用者訊息在交給模型前遮蔽個資（模型與對話紀錄都只會
      看到遮蔽後的內容），agent 的回覆在送出前也會遮蔽。
    - block_injection：擋下看起來是提示注入的使用者訊息。
    - blocked_words：使用者訊息或 agent 回覆包含這些詞時擋下。

    Args:
        hooks: 要註冊到哪一組 hooks。
        mask_pii: 是否遮蔽個資。
        block_injection: 是否擋下提示注入。
        blocked_words: 禁用詞。

    Returns:
        傳入的 hooks，方便串接。
    """
    words = list(blocked_words or [])

    def check_user_message(
        text: str, context: datatypes.RunContext
    ) -> str | hooks_lib.Decision:
        if block_injection and detect_injection(text):
            return hooks_lib.deny(INJECTION_REASON)
        if words and find_blocked_word(text, words):
            return hooks_lib.deny(BLOCKED_WORD_REASON)
        return _apply_masks(text) if mask_pii else text

    def check_reply(
        text: str, context: datatypes.RunContext
    ) -> str | hooks_lib.Decision:
        if words and find_blocked_word(text, words):
            return hooks_lib.deny(BLOCKED_WORD_REASON)
        return _apply_masks(text) if mask_pii else text

    if mask_pii or block_injection or words:
        hooks.add("pre_user_message", check_user_message)
    if mask_pii or words:
        hooks.add("pre_reply", check_reply)
    return hooks


def _apply_masks(text: str) -> str:
    """mask_pii 的本體（install 的參數與 mask_pii 同名，所以另外命名）。"""
    for pattern, label in _MASKS:
        text = pattern.sub(label, text)
    return _CARD.sub(_mask_card, text)


def _mask_card(match: re.Match[str]) -> str:
    """通過 Luhn 驗證的數字串才當成信用卡號遮蔽。"""
    digits = re.sub(r"\D", "", match.group(0))
    if _luhn_valid(digits):
        return "[已遮蔽卡號]"
    return match.group(0)


def _luhn_valid(digits: str) -> bool:
    """Luhn 檢查碼驗證。"""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0
