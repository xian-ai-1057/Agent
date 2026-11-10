"""Hooks：在主迴圈的固定時間點插入自己的邏輯。

事件與處理函式的參數：
    run_start(user_input, context)       使用者訊息送出前。
    pre_user_message(text, context)      使用者訊息交給模型前，可回傳新的
                                         字串改寫內容（例如遮蔽個資），或
                                         回傳 deny() 擋下整則訊息。
    pre_reply(text, context)             agent 的回覆送給使用者前，可回傳
                                         新的字串改寫內容，或回傳 deny()
                                         換成拒絕說明並結束這一輪。註冊
                                         了這個事件後，回覆會整段檢查完
                                         才送出，不再逐字串流。
    pre_tool_use(call, context)          工具執行前，可回傳 deny() 攔截，
                                         或 allow() 略過使用者確認；也可以
                                         直接修改 call.arguments。
    post_tool_use(call, result, context) 工具執行後，可修改 result.output。
    run_end(result, context)             整次 run 結束後。

處理函式同步或 async 皆可。pre_user_message 與 pre_reply 只對使用者
直接對話的那一層觸發（subagent 的輸入輸出不會直接到使用者手上）。
"""

import dataclasses
import inspect
from collections.abc import Callable
from typing import Any

EVENTS = (
    "run_start",
    "pre_user_message",
    "pre_tool_use",
    "post_tool_use",
    "pre_reply",
    "run_end",
)


@dataclasses.dataclass
class Decision:
    """處理函式的決定（pre_tool_use、pre_user_message、pre_reply）。

    Attributes:
        action: "allow" 或 "deny"。
        reason: 拒絕的原因。pre_tool_use 會送回給模型，其餘事件會顯示
            給使用者。
    """

    action: str
    reason: str = ""


def allow() -> Decision:
    """允許這次工具呼叫，並略過使用者確認。

    Returns:
        action 為 allow 的 Decision。
    """
    return Decision("allow")


def deny(reason: str) -> Decision:
    """拒絕這次工具呼叫。

    Args:
        reason: 拒絕的原因，會送回給模型。

    Returns:
        action 為 deny 的 Decision。
    """
    return Decision("deny", reason)


class Hooks:
    """保存各事件的處理函式。"""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[..., Any]]] = {
            event: [] for event in EVENTS
        }

    def has(self, event: str) -> bool:
        """某個事件是否有註冊任何處理函式。

        Args:
            event: 事件名稱。

        Returns:
            有處理函式時為 True。
        """
        return bool(self._handlers.get(event))

    async def filter_text(
        self, event: str, text: str, context: Any
    ) -> tuple[str, Decision | None]:
        """依序讓 pre_user_message / pre_reply 的處理函式檢查一段文字。

        每個處理函式收到的是前一個改寫後的文字；任何一個回傳 deny()
        就停止。

        Args:
            event: pre_user_message 或 pre_reply。
            text: 要檢查的文字。
            context: 這次 run 的環境資訊。

        Returns:
            (改寫後的文字, 拒絕的 Decision)；沒有被拒絕時第二個值為 None。

        Raises:
            TypeError: 處理函式回傳了字串、Decision、None 以外的值。
        """
        for handler in self._handlers[event]:
            value = handler(text, context)
            if inspect.isawaitable(value):
                value = await value
            if value is None:
                continue
            if isinstance(value, str):
                text = value
            elif isinstance(value, Decision):
                if value.action == "deny":
                    return text, value
            else:
                raise TypeError(
                    f"{event} 的處理函式只能回傳字串、deny() 或 None，"
                    f"收到 {type(value).__name__}"
                )
        return text, None

    def add(self, event: str, handler: Callable[..., Any]) -> None:
        """註冊一個處理函式。

        Args:
            event: 事件名稱，必須是 EVENTS 之一。
            handler: 處理函式。

        Raises:
            ValueError: 事件名稱不存在。
        """
        if event not in self._handlers:
            raise ValueError(f"沒有 {event} 這個事件，可用事件：{EVENTS}")
        self._handlers[event].append(handler)

    def on(self, event: str) -> Callable[..., Any]:
        """用裝飾器註冊處理函式。

        Example:
            @hooks.on("pre_tool_use")
            def block_delete(call, context):
                if call.name == "delete_report":
                    return deny("不允許刪除報告")

        Args:
            event: 事件名稱。

        Returns:
            裝飾器。
        """

        def register(handler: Callable[..., Any]) -> Callable[..., Any]:
            self.add(event, handler)
            return handler

        return register

    async def emit(self, event: str, *args: Any) -> list[Any]:
        """依註冊順序呼叫某個事件的所有處理函式。

        Args:
            event: 事件名稱。
            *args: 傳給處理函式的參數。

        Returns:
            所有不是 None 的回傳值。
        """
        results = []
        for handler in self._handlers[event]:
            value = handler(*args)
            if inspect.isawaitable(value):
                value = await value
            if value is not None:
                results.append(value)
        return results
