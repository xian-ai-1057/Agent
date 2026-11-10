"""主迴圈：呼叫模型 → 執行工具 → 把結果送回，直到模型不再呼叫工具。"""

import asyncio
import json
import pathlib
from collections.abc import AsyncIterator
from typing import Any

from harness import agents
from harness import datatypes
from harness import hooks as hooks_lib
from harness import llm
from harness import permissions as permissions_lib
from harness import sessions
from harness import tools as tools_lib

_INTERRUPTED = "工具執行被中斷，沒有結果"


async def run_stream(
    agent: agents.Agent,
    user_input: str,
    *,
    session: sessions.Session | None = None,
    hooks: hooks_lib.Hooks | None = None,
    permissions: permissions_lib.Permissions | None = None,
    deps: Any = None,
    workdir: pathlib.Path | None = None,
    model_fn: llm.ModelFn | None = None,
) -> AsyncIterator[datatypes.Event]:
    """執行 agent 並逐步產生事件，適合接到聊天介面。

    Args:
        agent: 要執行的 agent。
        user_input: 使用者這一輪的訊息。
        session: 對話紀錄，傳入同一個 session 就能延續對話；不傳則是
            一次性的對話。
        hooks: 要套用的 hooks。
        permissions: 工具的權限設定，預設為 ask 模式。
        deps: 任意物件，工具可從 RunContext.deps 取得。
        workdir: 專案根目錄，預設為目前目錄。
        model_fn: 模型函式，預設透過 litellm 呼叫；測試時可換成假的。

    Yields:
        Event，最後一個一定是 run_end。
    """
    session = session or sessions.Session()
    hooks = hooks or hooks_lib.Hooks()
    permissions = permissions or permissions_lib.Permissions()
    model_fn = model_fn or llm.complete
    context = datatypes.RunContext(
        agent_name=agent.name,
        workdir=pathlib.Path(workdir or pathlib.Path.cwd()),
        session_id=session.id,
        deps=deps,
    )
    tools_by_name = {item.name: item for item in agent.tools}
    schemas = [item.to_schema() for item in agent.tools]

    _close_dangling_tool_calls(session)
    await hooks.emit("run_start", user_input, context)
    session.append({"role": "user", "content": user_input})

    usage = datatypes.Usage()
    output = ""
    stop_reason = "max_turns"
    turns = 0
    while turns < agent.max_turns:
        turns += 1
        response = None
        request = _build_request(agent, session.messages)
        model = agent.model or llm.default_model()
        async for item in model_fn(model, request, schemas, agent.model_params):
            if isinstance(item, str):
                yield datatypes.Event("text_delta", {"text": item})
            else:
                response = item
        if response is None:
            raise RuntimeError("模型函式沒有回傳 ModelResponse")
        usage.add(response.usage)
        session.append(response.message)
        yield datatypes.Event("message", {"message": response.message})

        if not response.tool_calls:
            output = response.message.get("content") or ""
            stop_reason = _stop_reason(response.finish_reason)
            break

        for call in response.tool_calls:
            yield datatypes.Event("tool_call", {"call": call})
        results = await _execute_calls(
            response.tool_calls, tools_by_name, hooks, permissions, context
        )
        for result in results:
            session.append(_tool_message(result))
            yield datatypes.Event("tool_result", {"result": result})

    result = datatypes.RunResult(
        output=output,
        messages=session.messages,
        usage=usage,
        turns=turns,
        stop_reason=stop_reason,
    )
    await hooks.emit("run_end", result, context)
    yield datatypes.Event("run_end", {"result": result})


async def run(
    agent: agents.Agent, user_input: str, **kwargs: Any
) -> datatypes.RunResult:
    """執行 agent 直到結束，回傳最後結果。

    Args:
        agent: 要執行的 agent。
        user_input: 使用者這一輪的訊息。
        **kwargs: 其餘參數同 run_stream。

    Returns:
        這次執行的 RunResult。
    """
    result = None
    async for event in run_stream(agent, user_input, **kwargs):
        if event.type == "run_end":
            result = event.data["result"]
    return result


def run_sync(
    agent: agents.Agent, user_input: str, **kwargs: Any
) -> datatypes.RunResult:
    """run 的同步版本，給沒有使用 asyncio 的程式呼叫。

    Args:
        agent: 要執行的 agent。
        user_input: 使用者這一輪的訊息。
        **kwargs: 其餘參數同 run_stream。

    Returns:
        這次執行的 RunResult。
    """
    return asyncio.run(run(agent, user_input, **kwargs))


def _build_request(
    agent: agents.Agent, messages: list[datatypes.Message]
) -> list[datatypes.Message]:
    """在對話紀錄前面加上 system prompt。"""
    if not agent.instructions:
        return list(messages)
    return [{"role": "system", "content": agent.instructions}, *messages]


def _stop_reason(finish_reason: str) -> str:
    """把模型的 finish_reason 轉成 RunResult 的 stop_reason。"""
    if finish_reason in ("stop", "tool_calls"):
        return "end_turn"
    if finish_reason == "length":
        return "max_tokens"
    return finish_reason


def _tool_message(result: datatypes.ToolResult) -> datatypes.Message:
    """把工具結果轉成要存進對話紀錄的訊息。"""
    content = result.output
    if result.is_error:
        content = f"[錯誤] {content}"
    return {
        "role": "tool",
        "tool_call_id": result.call_id,
        "name": result.name,
        "content": content,
    }


def _close_dangling_tool_calls(session: sessions.Session) -> None:
    """替上次中斷時沒有結果的工具呼叫補上結果。

    模型要求每個 tool call 都要有對應的結果，否則下一次請求會失敗；
    使用者在工具執行途中按 Ctrl-C 就會留下這種狀態。
    """
    answered = {
        message.get("tool_call_id")
        for message in session.messages
        if message.get("role") == "tool"
    }
    for message in list(session.messages):
        for call in message.get("tool_calls") or []:
            if call["id"] in answered:
                continue
            result = datatypes.ToolResult(
                call["id"], call["function"]["name"], _INTERRUPTED, True
            )
            session.append(_tool_message(result))


async def _execute_calls(
    calls: list[datatypes.ToolCall],
    tools_by_name: dict[str, tools_lib.Tool],
    hooks: hooks_lib.Hooks,
    permissions: permissions_lib.Permissions,
    context: datatypes.RunContext,
) -> list[datatypes.ToolResult]:
    """執行同一輪的所有工具呼叫，結果順序與呼叫順序相同。

    權限確認一個一個來（避免同時跳出多個詢問），通過的工具再同時執行。
    """
    rejected: dict[str, datatypes.ToolResult] = {}
    for call in calls:
        rejection = await _authorize(
            call, tools_by_name, hooks, permissions, context
        )
        if rejection is not None:
            rejected[call.id] = rejection

    async def execute(call: datatypes.ToolCall) -> datatypes.ToolResult:
        if call.id in rejected:
            return rejected[call.id]
        return await _execute(call, tools_by_name[call.name], hooks, context)

    return list(await asyncio.gather(*(execute(call) for call in calls)))


async def _authorize(
    call: datatypes.ToolCall,
    tools_by_name: dict[str, tools_lib.Tool],
    hooks: hooks_lib.Hooks,
    permissions: permissions_lib.Permissions,
    context: datatypes.RunContext,
) -> datatypes.ToolResult | None:
    """檢查一次工具呼叫能不能執行。

    Returns:
        None 表示可以執行，否則是要直接送回模型的錯誤結果。
    """
    target = tools_by_name.get(call.name)
    if target is None:
        available = "、".join(tools_by_name) or "（沒有任何工具）"
        return _error(call, f"沒有 {call.name} 這個工具，可用的有：{available}")
    if call.parse_error:
        return _error(call, f"參數不是合法的 JSON：{call.parse_error}")

    decisions = [
        item
        for item in await hooks.emit("pre_tool_use", call, context)
        if isinstance(item, hooks_lib.Decision)
    ]
    for decision in decisions:
        if decision.action == "deny":
            return _error(call, f"這次呼叫被拒絕：{decision.reason}")
    hook_allowed = any(item.action == "allow" for item in decisions)

    verdict = permissions_lib.check(permissions, target)
    if verdict == "deny":
        return _error(call, "權限設定不允許使用這個工具，請改用其他做法")
    if verdict == "ask" and not hook_allowed:
        if not await permissions_lib.ask(permissions, call, target):
            return _error(call, "使用者沒有同意這次呼叫，請不要重試同樣的操作")
    return None


async def _execute(
    call: datatypes.ToolCall,
    target: tools_lib.Tool,
    hooks: hooks_lib.Hooks,
    context: datatypes.RunContext,
) -> datatypes.ToolResult:
    """執行工具，並把任何失敗轉成模型看得懂的錯誤結果。"""
    try:
        output = await tools_lib.invoke(target, call.arguments, context)
        result = datatypes.ToolResult(call.id, call.name, output)
    # 工具是任意的專案程式碼，任何例外都要回報給模型讓它自行修正，
    # 不能讓整個對話中斷。
    except Exception as error:  # noqa: BLE001
        result = _error(call, _describe_error(error))
    await hooks.emit("post_tool_use", call, result, context)
    return result


def _describe_error(error: Exception) -> str:
    """把例外轉成給模型看的訊息。"""
    if isinstance(error, tools_lib.ToolError):
        return str(error)
    return f"{type(error).__name__}: {error}"


def _error(call: datatypes.ToolCall, message: str) -> datatypes.ToolResult:
    """建立錯誤結果。"""
    return datatypes.ToolResult(call.id, call.name, message, is_error=True)


def format_arguments(call: datatypes.ToolCall, limit: int = 200) -> str:
    """把工具呼叫的參數轉成適合顯示給人看的短字串。

    Args:
        call: 工具呼叫。
        limit: 最多顯示幾個字元。

    Returns:
        JSON 字串，過長時截斷並加上省略號。
    """
    text = json.dumps(call.arguments, ensure_ascii=False, default=str)
    if len(text) > limit:
        return f"{text[:limit]}…"
    return text
