"""Hooks：在主迴圈的固定時間點插入自己的邏輯。

事件與處理函式的參數：
    run_start(user_input, context)       使用者訊息送出前。
    pre_tool_use(call, context)          工具執行前，可回傳 deny() 攔截，
                                         或 allow() 略過使用者確認；也可以
                                         直接修改 call.arguments。
    post_tool_use(call, result, context) 工具執行後，可修改 result.output。
    run_end(result, context)             整次 run 結束後。

處理函式同步或 async 皆可。
"""

import dataclasses
import inspect
from collections.abc import Callable
from typing import Any

EVENTS = ("run_start", "pre_tool_use", "post_tool_use", "run_end")


@dataclasses.dataclass
class Decision:
    """pre_tool_use 處理函式對一次工具呼叫的決定。

    Attributes:
        action: "allow" 或 "deny"。
        reason: 拒絕的原因，會送回給模型。
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
