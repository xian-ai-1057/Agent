"""主迴圈：呼叫模型 → 執行工具 → 把結果送回，直到模型不再呼叫工具。"""

import asyncio
import dataclasses
import json
import pathlib
from collections.abc import AsyncIterator
from typing import Any

from harness import agents
from harness import datatypes
from harness import delegation
from harness import hooks as hooks_lib
from harness import llm
from harness import permissions as permissions_lib
from harness import sessions
from harness import tools as tools_lib

_INTERRUPTED = "工具執行被中斷，沒有結果"


@dataclasses.dataclass
class _RunOptions:
    """一次 run 裡各層 agent 共用的設定。"""

    hooks: hooks_lib.Hooks
    permissions: permissions_lib.Permissions
    deps: Any
    workdir: pathlib.Path
    model_fn: llm.ModelFn
    # 0 是使用者直接對話的 agent，每進入一層 subagent 加 1。
    depth: int = 0


@dataclasses.dataclass
class _Toolbox:
    """某個 agent 在這次 run 裡實際可用的工具。"""

    tools: dict[str, tools_lib.Tool]
    schemas: list[dict[str, Any]]
    handoffs: dict[str, agents.Agent]


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
        agent: 起始的 agent。對話先前已轉交（handoff）給別的 agent 時，
            會自動由接手的那個 agent 繼續，所以每次都傳同一個即可。
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
    options = _RunOptions(
        hooks=hooks or hooks_lib.Hooks(),
        permissions=permissions or permissions_lib.Permissions(),
        deps=deps,
        workdir=pathlib.Path(workdir or pathlib.Path.cwd()),
        model_fn=model_fn or llm.complete,
    )
    events = _run_loop(
        agent, user_input, session or sessions.Session(), options
    )
    async for event in events:
        yield event


async def _run_loop(
    entry: agents.Agent,
    user_input: str,
    session: sessions.Session,
    options: _RunOptions,
) -> AsyncIterator[datatypes.Event]:
    """主迴圈本體，最上層的對話與 subagent 的對話都走這裡。"""
    _close_dangling_tool_calls(session)
    agent = delegation.active_agent(entry, session.messages)
    usage = datatypes.Usage()
    context = _make_context(agent, session, options)
    toolbox = _build_toolbox(agent, session, options, usage)
    # run_start / run_end 只對使用者直接對話的那一層觸發；subagent 的
    # 執行屬於一次工具呼叫，由 pre_tool_use / post_tool_use 涵蓋。
    if options.depth == 0:
        await options.hooks.emit("run_start", user_input, context)
    session.append({"role": "user", "content": user_input})

    output = ""
    stop_reason = "max_turns"
    turns = 0
    while turns < agent.max_turns:
        turns += 1
        response = None
        request = _build_request(agent, session.messages)
        model = agent.model or llm.default_model()
        stream = options.model_fn(
            model, request, toolbox.schemas, agent.model_params
        )
        async for item in stream:
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
            response.tool_calls, toolbox, options, context
        )
        for result in results:
            session.append(_tool_message(result))
            yield datatypes.Event("tool_result", {"result": result})

        target = _handoff_target(results, toolbox)
        if target is not None:
            handoff = {"from": agent.name, "to": target.name}
            yield datatypes.Event("handoff", handoff)
            agent = target
            context = _make_context(agent, session, options)
            toolbox = _build_toolbox(agent, session, options, usage)

    result = datatypes.RunResult(
        output=output,
        messages=session.messages,
        usage=usage,
        turns=turns,
        stop_reason=stop_reason,
        agent_name=agent.name,
    )
    if options.depth == 0:
        await options.hooks.emit("run_end", result, context)
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


def _make_context(
    agent: agents.Agent, session: sessions.Session, options: _RunOptions
) -> datatypes.RunContext:
    """建立傳給工具與 hooks 的環境資訊。"""
    return datatypes.RunContext(
        agent_name=agent.name,
        workdir=options.workdir,
        session_id=session.id,
        deps=options.deps,
    )


def _build_toolbox(
    agent: agents.Agent,
    session: sessions.Session,
    options: _RunOptions,
    usage: datatypes.Usage,
) -> _Toolbox:
    """組合 agent 自己的工具，以及 subagents、handoffs 對應的工具。"""
    items = list(agent.tools)
    # 到達巢狀上限後不再提供 subagent 工具，避免 agent 互相呼叫沒完沒了。
    if options.depth < delegation.MAX_SUBAGENT_DEPTH:
        runner = _subagent_runner(session, options, usage)
        items += [
            delegation.make_subagent_tool(target, runner)
            for target in agent.subagents
        ]
    handoffs = {}
    for target in agent.handoffs:
        item = delegation.make_handoff_tool(target)
        handoffs[item.name] = target
        items.append(item)

    by_name: dict[str, tools_lib.Tool] = {}
    for item in items:
        if item.name in by_name:
            raise ValueError(
                f"agent {agent.name} 的工具名稱重複：{item.name}"
                "（subagents 與 handoffs 也各佔用一個工具名稱）"
            )
        by_name[item.name] = item
    schemas = [item.to_schema() for item in items]
    return _Toolbox(tools=by_name, schemas=schemas, handoffs=handoffs)


def _subagent_runner(
    session: sessions.Session, options: _RunOptions, usage: datatypes.Usage
) -> delegation.SubagentRunner:
    """建立執行 subagent 的函式，用量會累加到上一層。"""

    async def run_subagent(target: agents.Agent, task: str) -> str:
        # subagent 在全新的對話裡工作，紀錄不寫入檔案。
        child_session = sessions.Session(f"{session.id}.{target.name}")
        child_options = dataclasses.replace(options, depth=options.depth + 1)
        result = None
        events = _run_loop(target, task, child_session, child_options)
        async for event in events:
            if event.type == "run_end":
                result = event.data["result"]
        usage.add(result.usage)
        if result.stop_reason != "end_turn":
            raise tools_lib.ToolError(
                f"{target.name} 沒有完成工作（{result.stop_reason}）。"
                f"它最後的回覆：{result.output or '（無）'}"
            )
        return result.output

    return run_subagent


def _handoff_target(
    results: list[datatypes.ToolResult], toolbox: _Toolbox
) -> agents.Agent | None:
    """這一輪如果有成功的轉交，回傳接手的 agent。"""
    for result in results:
        if not result.is_error and result.name in toolbox.handoffs:
            return toolbox.handoffs[result.name]
    return None


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
        content = f"{delegation.ERROR_PREFIX}{content}"
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
    toolbox: _Toolbox,
    options: _RunOptions,
    context: datatypes.RunContext,
) -> list[datatypes.ToolResult]:
    """執行同一輪的所有工具呼叫，結果順序與呼叫順序相同。

    權限確認一個一個來（避免同時跳出多個詢問），通過的工具再同時執行。
    """
    rejected: dict[str, datatypes.ToolResult] = {}
    handoff_requested = False
    for call in calls:
        rejection = await _authorize(
            call, toolbox.tools, options.hooks, options.permissions, context
        )
        if rejection is None and call.name in toolbox.handoffs:
            if handoff_requested:
                rejection = _error(
                    call, "一次只能轉交給一個 agent，這次轉交沒有生效"
                )
            handoff_requested = True
        if rejection is not None:
            rejected[call.id] = rejection

    async def execute(call: datatypes.ToolCall) -> datatypes.ToolResult:
        if call.id in rejected:
            return rejected[call.id]
        target = toolbox.tools[call.name]
        return await _execute(call, target, options.hooks, context)

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
