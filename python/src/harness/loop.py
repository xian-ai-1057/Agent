"""主迴圈：呼叫模型 → 執行工具 → 把結果送回，直到模型不再呼叫工具。"""

import asyncio
import dataclasses
import json
import pathlib
from collections.abc import AsyncIterator
from typing import Any

from harness import agents
from harness import compaction as compaction_lib
from harness import datatypes
from harness import delegation
from harness import hooks as hooks_lib
from harness import llm
from harness import memory as memory_lib
from harness import permissions as permissions_lib
from harness import sessions
from harness import skills as skills_lib
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
    memory: memory_lib.MemoryStore | None = None
    compaction: compaction_lib.Compaction | None = None
    # 0 是使用者直接對話的 agent，每進入一層 subagent 加 1。
    depth: int = 0

    @property
    def user_facing(self) -> bool:
        """這一層是不是直接和使用者對話（不是 subagent）。"""
        return self.depth == 0


@dataclasses.dataclass
class _Toolbox:
    """某個 agent 在這次 run 裡實際可用的工具。"""

    tools: dict[str, tools_lib.Tool]
    schemas: list[dict[str, Any]]
    handoffs: dict[str, agents.Agent]


async def run_stream(
    agent: agents.Agent,
    user_input: str | None,
    *,
    session: sessions.Session | None = None,
    hooks: hooks_lib.Hooks | None = None,
    permissions: permissions_lib.Permissions | None = None,
    deps: Any = None,
    workdir: pathlib.Path | None = None,
    model_fn: llm.ModelFn | None = None,
    memory: memory_lib.MemoryStore | None = None,
    compaction: compaction_lib.Compaction | None = None,
) -> AsyncIterator[datatypes.Event]:
    """執行 agent 並逐步產生事件，適合接到聊天介面。

    Args:
        agent: 起始的 agent。對話先前已轉交（handoff）給別的 agent 時，
            會自動由接手的那個 agent 繼續，所以每次都傳同一個即可。
        user_input: 使用者這一輪的訊息。傳 None 表示不加新訊息，直接從
            對話紀錄的最後一則接著跑（例如上一輪連不上模型，修好後重試）。
        session: 對話紀錄，傳入同一個 session 就能延續對話；不傳則是
            一次性的對話。
        hooks: 要套用的 hooks。
        permissions: 工具的權限設定，預設為 ask 模式。
        deps: 任意物件，工具可從 RunContext.deps 取得。
        workdir: 專案根目錄，預設為目前目錄。
        model_fn: 模型函式，預設透過 litellm 呼叫；測試時可換成假的。
        memory: 使用者的長期記憶。傳入時 agent 會看到記憶內容，並可以
            用 remember / forget 工具管理；不傳則不使用記憶。
        compaction: 對話壓縮的設定。傳入時，對話太長會自動把較舊的歷史
            濃縮成檢查點；不傳則不壓縮。

    Yields:
        Event，最後一個一定是 run_end。

    Raises:
        ValueError: user_input 為 None，但對話紀錄的最後一則不是使用者
            訊息或工具結果，沒有可以接續的地方。
    """
    options = _RunOptions(
        hooks=hooks or hooks_lib.Hooks(),
        permissions=permissions or permissions_lib.Permissions(),
        deps=deps,
        workdir=pathlib.Path(workdir or pathlib.Path.cwd()),
        model_fn=model_fn or llm.complete,
        memory=memory,
        compaction=compaction,
    )
    events = _run_loop(
        agent, user_input, session or sessions.Session(), options
    )
    async for event in events:
        yield event


async def _run_loop(
    entry: agents.Agent,
    user_input: str | None,
    session: sessions.Session,
    options: _RunOptions,
) -> AsyncIterator[datatypes.Event]:
    """主迴圈本體，最上層的對話與 subagent 的對話都走這裡。"""
    _close_dangling_tool_calls(session)
    if user_input is None:
        _assert_resumable(session)
    agent = delegation.active_agent(entry, session.messages)
    usage = datatypes.Usage()
    context = _make_context(agent, session, options)
    toolbox = _build_toolbox(agent, session, options, usage)
    # run_start / run_end 只對使用者直接對話的那一層觸發；subagent 的
    # 執行屬於一次工具呼叫，由 pre_tool_use / post_tool_use 涵蓋。
    if options.user_facing:
        await options.hooks.emit("run_start", user_input or "", context)
    # 接著跑（user_input 為 None）時，訊息在上一輪就已經檢查並存進紀錄。
    if options.user_facing and user_input is not None:
        user_input, blocked = await options.hooks.filter_text(
            "pre_user_message", user_input, context
        )
        if blocked is not None:
            # 被擋下的訊息不寫進對話紀錄，模型完全不會看到它。
            async for event in _finish_blocked(
                agent, session, usage, blocked.reason, options, context
            ):
                yield event
            return
    if user_input is not None:
        session.append({"role": "user", "content": user_input})

    output = ""
    stop_reason = "max_turns"
    turns = 0
    # 有 pre_reply 時要整段檢查完才能送出，所以不逐字串流。
    hold_text = options.user_facing and options.hooks.has("pre_reply")
    compactor = None
    if options.compaction is not None:
        compactor = compaction_lib.Compactor(
            options.compaction, options.model_fn
        )
    while turns < agent.max_turns:
        turns += 1
        response = None
        if compactor is not None and compactor.settings.auto:
            event = await _auto_compact(
                agent, session, toolbox, options, compactor, usage
            )
            if event is not None:
                yield event
            if event is not None and event.type == "compaction":
                # 壓縮可能讓已載入的 skill 離開模型看得到的歷史。
                toolbox = _build_toolbox(agent, session, options, usage)
        steps = _request_model(
            agent, session, toolbox, options, compactor, usage, hold_text
        )
        async for item in steps:
            if isinstance(item, datatypes.ModelResponse):
                response = item
            else:
                yield item
        usage.add(response.usage)
        blocked_reason = None
        if hold_text:
            blocked_reason = await _check_reply(response, options, context)
            text = response.message.get("content")
            if text:
                yield datatypes.Event("text_delta", {"text": text})
        session.append(response.message)
        if compactor is not None:
            compactor.record_usage(session.messages, response.usage)
        yield datatypes.Event("message", {"message": response.message})

        if blocked_reason is not None:
            output = blocked_reason
            stop_reason = "blocked"
            break
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
        elif _loaded_skill(results):
            # 載入 skill 後，它的專屬工具從下一次呼叫模型開始可用。
            toolbox = _build_toolbox(agent, session, options, usage)

    result = datatypes.RunResult(
        output=output,
        messages=session.messages,
        usage=usage,
        turns=turns,
        stop_reason=stop_reason,
        agent_name=agent.name,
    )
    if options.user_facing:
        await options.hooks.emit("run_end", result, context)
    yield datatypes.Event("run_end", {"result": result})


async def _request_model(
    agent: agents.Agent,
    session: sessions.Session,
    toolbox: _Toolbox,
    options: _RunOptions,
    compactor: compaction_lib.Compactor | None,
    usage: datatypes.Usage,
    hold_text: bool,
) -> AsyncIterator[datatypes.Event | datatypes.ModelResponse]:
    """呼叫一次模型；超過上下文上限時壓縮後重試。

    Yields:
        事件（text_delta、compaction），最後一個是模型的 ModelResponse。
    """
    system = _system_prompt(agent, options)
    auto = compactor is not None and compactor.settings.auto
    model = agent.model or llm.default_model()
    retries = 0
    while True:
        request = _build_request(system, session)
        response = None
        streamed = False
        try:
            stream = options.model_fn(
                model, request, toolbox.schemas, agent.model_params
            )
            async for item in stream:
                if isinstance(item, str):
                    streamed = True
                    if not hold_text:
                        yield datatypes.Event("text_delta", {"text": item})
                else:
                    response = item
        except Exception as error:
            can_retry = (
                auto
                and not streamed
                and retries < compactor.settings.max_overflow_retries
                and llm.is_context_overflow(error)
            )
            if not can_retry:
                raise
            retries += 1
            try:
                result = await compactor.compact(
                    agent, session, system, toolbox.schemas, "overflow"
                )
            except compaction_lib.CompactionError as compaction_error:
                raise error from compaction_error
            if result is None:
                raise
            usage.add(result.usage)
            yield datatypes.Event("compaction", {"result": result})
            continue
        if response is None:
            raise RuntimeError("模型函式沒有回傳 ModelResponse")
        yield response
        return


async def _auto_compact(
    agent: agents.Agent,
    session: sessions.Session,
    toolbox: _Toolbox,
    options: _RunOptions,
    compactor: compaction_lib.Compactor,
    usage: datatypes.Usage,
) -> datatypes.Event | None:
    """請求超過門檻時壓縮，回傳要送出的事件（沒有壓縮時為 None）。"""
    try:
        result = await compactor.compact_if_needed(
            agent, session, _system_prompt(agent, options), toolbox.schemas
        )
    except compaction_lib.CompactionError as error:
        # 自動壓縮失敗不中斷對話；真的超過上限時還有壓縮後重試的機會。
        return datatypes.Event("compaction_failed", {"error": str(error)})
    if result is None:
        return None
    usage.add(result.usage)
    return datatypes.Event("compaction", {"result": result})


async def compact(
    agent: agents.Agent,
    session: sessions.Session,
    *,
    settings: compaction_lib.Compaction | None = None,
    model_fn: llm.ModelFn | None = None,
    memory: memory_lib.MemoryStore | None = None,
) -> compaction_lib.CompactionResult | None:
    """立刻壓縮一段對話，不管有沒有超過門檻（例如聊天中的 /compact）。

    會盡量壓縮：除了最近一段（最後一則使用者訊息或 assistant 回覆，
    與它的工具結果）之外都交給摘要。

    Args:
        agent: 起始的 agent，和呼叫 run 時傳的相同。
        session: 要壓縮的對話。
        settings: 壓縮設定，預設使用 Compaction()。
        model_fn: 模型函式，預設透過 litellm 呼叫。
        memory: 使用者的長期記憶（讓摘要時的 system prompt 與平常一致）。

    Returns:
        壓縮結果；對話太短、沒有東西可以壓縮時為 None。

    Raises:
        compaction.CompactionError: 摘要失敗。
    """
    options = _RunOptions(
        hooks=hooks_lib.Hooks(),
        permissions=permissions_lib.Permissions(),
        deps=None,
        workdir=pathlib.Path.cwd(),
        model_fn=model_fn or llm.complete,
        memory=memory,
        compaction=settings or compaction_lib.Compaction(),
    )
    current = delegation.active_agent(agent, session.messages)
    toolbox = _build_toolbox(current, session, options, datatypes.Usage())
    compactor = compaction_lib.Compactor(options.compaction, options.model_fn)
    return await compactor.compact(
        current,
        session,
        _system_prompt(current, options),
        toolbox.schemas,
        "manual",
    )


async def _finish_blocked(
    agent: agents.Agent,
    session: sessions.Session,
    usage: datatypes.Usage,
    reason: str,
    options: _RunOptions,
    context: datatypes.RunContext,
) -> AsyncIterator[datatypes.Event]:
    """使用者訊息被 pre_user_message 擋下時，不呼叫模型直接結束。"""
    yield datatypes.Event("text_delta", {"text": reason})
    result = datatypes.RunResult(
        output=reason,
        messages=session.messages,
        usage=usage,
        turns=0,
        stop_reason="blocked",
        agent_name=agent.name,
    )
    await options.hooks.emit("run_end", result, context)
    yield datatypes.Event("run_end", {"result": result})


async def _check_reply(
    response: datatypes.ModelResponse,
    options: _RunOptions,
    context: datatypes.RunContext,
) -> str | None:
    """讓 pre_reply 檢查模型的回覆，必要時直接改寫 response。

    Returns:
        被拒絕時的原因，否則為 None。
    """
    text = response.message.get("content")
    if not text:
        return None
    text, blocked = await options.hooks.filter_text("pre_reply", text, context)
    if blocked is None:
        response.message["content"] = text
        return None
    # 換掉整則回覆，也取消這次的工具呼叫：被擋下的回覆不該再有後續動作。
    response.message = {"role": "assistant", "content": blocked.reason}
    response.tool_calls = []
    return blocked.reason


def _loaded_skill(results: list[datatypes.ToolResult]) -> bool:
    """這一輪是否有成功載入 skill。"""
    return any(
        result.name == skills_lib.LOAD_TOOL and not result.is_error
        for result in results
    )


async def run(
    agent: agents.Agent, user_input: str | None, **kwargs: Any
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
    agent: agents.Agent, user_input: str | None, **kwargs: Any
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
    """組合 agent 可用的所有工具。

    包含 agent 自己的工具、skill 相關工具（含已載入 skill 的專屬工具）、
    記憶工具，以及 subagents、handoffs 對應的工具。
    """
    items = list(agent.tools)
    if agent.skills:
        items += skills_lib.make_tools(agent.skills)
        history = compaction_lib.model_history(session.messages)
        for skill in skills_lib.loaded_skills(agent.skills, history):
            known = {item.name for item in items}
            items += [item for item in skill.tools if item.name not in known]
    if options.memory is not None and options.user_facing:
        items += memory_lib.make_tools(options.memory)
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
                "（subagents、handoffs、skills 與記憶也會佔用工具名稱）"
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


def _system_prompt(agent: agents.Agent, options: _RunOptions) -> str:
    """組合 system prompt：agent 的指示、可用的 skill 清單與長期記憶。

    每次呼叫模型都重新組合，所以剛存下的記憶下一次就看得到。
    """
    parts = [agent.instructions, skills_lib.prompt_section(agent.skills)]
    if options.memory is not None and options.user_facing:
        parts.append(memory_lib.prompt_section(options.memory))
    return "\n\n".join(part for part in parts if part)


def _build_request(
    system: str, session: sessions.Session
) -> list[datatypes.Message]:
    """在模型看得到的歷史前面加上 system prompt。

    歷史由對話紀錄推算（見 compaction.model_history）：壓縮過的部分以
    摘要取代，修剪過的工具結果以修剪後的內容取代。
    """
    history = compaction_lib.model_history(session.messages)
    if not system:
        return history
    return [{"role": "system", "content": system}, *history]


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


def _assert_resumable(session: sessions.Session) -> None:
    """不加新訊息接著跑時，最後一則必須是使用者訊息或工具結果。"""
    history = [m for m in session.messages if not compaction_lib.is_record(m)]
    last = history[-1] if history else {}
    if last.get("role") not in ("user", "tool"):
        raise ValueError(
            "對話紀錄的最後一則不是使用者訊息或工具結果，沒有可以接續的地方"
        )


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
