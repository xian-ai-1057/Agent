"""網頁版的核心：管理一個專案裡所有對話的執行狀態。

每段對話可以同時各自執行。執行過程中的每一步都會變成事件推給訂閱者
（伺服器再用 SSE 轉給瀏覽器）；需要使用者同意的工具呼叫會停下來，等
瀏覽器回覆 resolve_approval。

送給瀏覽器的資料格式和 TypeScript 版完全相同（見根目錄的
src/web/protocol.ts），兩個版本共用同一份前端。所以這裡的 dict 鍵名
一律用 camelCase。
"""

import asyncio
import dataclasses
import json
import os
import re
import time
import uuid
from collections.abc import Callable
from typing import Any

from harness import builtin
from harness import compaction as compaction_lib
from harness import datatypes
from harness import delegation
from harness import llm
from harness import loop
from harness import permissions as permissions_lib
from harness import project as project_lib
from harness import sessions as sessions_lib
from harness import tools as tools_lib
from harness.builtin import files
from harness.web import config as config_lib

Event = dict[str, Any]
Listener = Callable[[Event], None]

DECISIONS = ("once", "always", "deny")
_UNTITLED = "新對話"
_TITLE_LENGTH = 40
_PREVIEW_LIMIT = 200_000
_MODEL_TEST_TIMEOUT = 20
_SESSION_ID = re.compile(r"^[\w-]+$")
# subagent 步驟的結果只是給人瀏覽的，太長的部分不保留。
_SUBAGENT_RESULT_LIMIT = 4000


class WebError(Exception):
    """帶 HTTP 狀態碼的錯誤，伺服器會原樣回給瀏覽器。

    Attributes:
        status: HTTP 狀態碼。
    """

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclasses.dataclass
class _Pending:
    """一個等使用者決定的工具呼叫。"""

    approval: dict[str, Any]
    future: asyncio.Future


@dataclasses.dataclass
class _State:
    """一段對話的執行狀態。agent 是起始的 agent。"""

    session: sessions_lib.Session
    agent: str
    title: str
    updated_at: float
    status: str = "idle"
    activity: dict[str, Any] | None = None
    started_at: float | None = None
    ended_at: float | None = None
    error: str | None = None
    notice: str | None = None
    tool_errors: int = 0
    stop_reason: str | None = None
    partial: str = ""
    mode: str | None = None
    approvals: dict[str, _Pending] = dataclasses.field(default_factory=dict)
    task: asyncio.Task | None = None
    # subagent 內部呼叫的工具，依上層 ask_<名稱> 呼叫的 id 分組。
    # subagent 的對話不存檔，這裡只保留到伺服器重新啟動。
    subagent_steps: dict[str, list[dict[str, Any]]] = dataclasses.field(
        default_factory=dict
    )


class Workspace:
    """一個專案的所有對話。

    Attributes:
        project: 載入完成的專案。
    """

    def __init__(
        self,
        project: project_lib.Project,
        *,
        model_fn: llm.ModelFn | None = None,
    ) -> None:
        self.project = project
        self._model_fn = model_fn or llm.complete
        self._states: dict[str, _State] = {}
        self._listeners: set[Listener] = set()
        self._settings = config_lib.read_model_settings(project.root)
        self._overrides = config_lib.read_agent_overrides(project.root)
        # 啟動時的模型設定；網頁上改過 harness.yaml 之後，沒有自己設定
        # 的 agent 都跟著換。
        self._base_agents = {
            name: (agent.model, agent.max_turns)
            for name, agent in project.agents.items()
        }

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """訂閱事件。

        Args:
            listener: 收到事件時呼叫的函式。

        Returns:
            取消訂閱的函式。
        """
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def project_info(self) -> dict[str, Any]:
        """專案資訊、agent、toolset、權限與模型設定。"""
        project = self.project
        return {
            "name": project.name,
            "root": str(project.root),
            "runtime": "python",
            "defaultAgent": project.default_agent,
            "agents": [self._agent_info(name) for name in project.agents],
            "toolsets": [
                {
                    "name": name,
                    "builtin": builtin.is_builtin_toolset(name, items),
                    "tools": [_tool_info(item) for item in items],
                }
                for name, items in project.toolsets.items()
            ],
            "permissions": self._permission_settings(),
            "model": {
                "config": dict(self._settings),
                "env": {
                    "model": os.environ.get("HARNESS_MODEL"),
                    "baseURL": os.environ.get("HARNESS_BASE_URL"),
                    "apiKeySet": bool(os.environ.get("HARNESS_API_KEY")),
                    # 只列名稱，不會送出金鑰的值。
                    "keyVars": sorted(
                        name
                        for name, value in os.environ.items()
                        if name.endswith("_API_KEY") and value
                    ),
                    "fallbackModel": llm.FALLBACK_MODEL,
                },
            },
        }

    def list_sessions(self) -> list[dict[str, Any]]:
        """所有對話的摘要，最近有動靜的排前面。"""
        for session_id in sessions_lib.list_ids(self.project.sessions_dir):
            self._find(session_id)
        summaries = [self._summary(state) for state in self._states.values()]
        return sorted(summaries, key=lambda item: -item["updatedAt"])

    def get_session(self, session_id: str) -> dict[str, Any]:
        """一段對話的完整內容。

        Raises:
            WebError: 找不到這段對話。
        """
        state = self._require(session_id)
        return {
            "summary": self._summary(state),
            "messages": state.session.messages,
            "partial": state.partial,
            "approvals": [item.approval for item in state.approvals.values()],
            "mode": state.mode,
            "subagentSteps": state.subagent_steps,
        }

    def start_run(
        self,
        text: str,
        session_id: str | None = None,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """送出一則訊息並在背景開始執行。沒給 session_id 時建立新的對話。

        Raises:
            WebError: 訊息是空的、agent 不存在，或這段對話還在執行。
        """
        text = text.strip()
        if not text:
            raise WebError(400, "訊息不可以是空的")
        if session_id:
            state = self._require(session_id)
        else:
            state = self._create(agent or self.project.default_agent)
        self._assert_idle(state)
        if state.title == _UNTITLED:
            state.title = _title(text)
        self._start(state, text)
        return self._summary(state)

    def retry(self, session_id: str) -> dict[str, Any]:
        """不加新訊息，從對話紀錄的最後一則接著跑（上一輪出錯後重試）。

        Raises:
            WebError: 對話還在執行，或最後一則已經是 agent 的回覆。
        """
        state = self._require(session_id)
        self._assert_idle(state)
        history = [
            m for m in state.session.messages if not compaction_lib.is_record(m)
        ]
        if not history or history[-1].get("role") not in ("user", "tool"):
            raise WebError(400, "這段對話沒有需要重試的內容")
        self._start(state, None)
        return self._summary(state)

    def stop(self, session_id: str) -> None:
        """停止執行；等待中的確認一律視為拒絕。"""
        state = self._require(session_id)
        if state.task is not None:
            state.task.cancel()

    def set_mode(self, session_id: str, mode: str | None) -> None:
        """設定這段對話自己的權限模式；None 表示跟著專案設定。"""
        if mode is not None and mode not in permissions_lib.MODES:
            raise WebError(400, f"沒有 {mode} 這個權限模式")
        self._require(session_id).mode = mode

    def resolve_approval(
        self, session_id: str, approval_id: str, decision: str
    ) -> None:
        """回覆一個等待中的確認。

        Raises:
            WebError: 決定不合法，或找不到這個等待中的確認。
        """
        if decision not in DECISIONS:
            raise WebError(400, f"decision 必須是 {'、'.join(DECISIONS)}")
        state = self._require(session_id)
        pending = state.approvals.get(approval_id)
        if pending is None or pending.future.done():
            raise WebError(404, "這個確認已經處理過，或已經失效")
        if decision == "always":
            self._allow_always(pending.approval["tool"])
        self._emit(
            {
                "type": "approval_resolved",
                "sessionId": session_id,
                "approvalId": approval_id,
                "decision": decision,
            }
        )
        pending.future.set_result(decision != "deny")

    def update_permissions(self, settings: dict[str, Any]) -> None:
        """更新權限設定並寫回 harness.yaml，下一次工具呼叫就生效。"""
        mode = settings.get("mode")
        if mode not in permissions_lib.MODES:
            raise WebError(400, f"沒有 {mode} 這個權限模式")
        cleaned = {"mode": mode}
        for key in ("allow", "ask", "deny"):
            cleaned[key] = _clean_patterns(settings.get(key))
        try:
            config_lib.save_permissions(self.project.root, cleaned)
        except ValueError as error:
            raise WebError(400, str(error)) from error
        shared = self.project.permissions
        shared.mode = cleaned["mode"]
        shared.allow = cleaned["allow"]
        shared.ask = cleaned["ask"]
        shared.deny = cleaned["deny"]
        self._emit({"type": "project"})

    def update_model_settings(
        self, model: str | None, max_turns: int | None
    ) -> None:
        """更新 harness.yaml 的 model 與 maxTurns，下一輪開始生效。"""
        model = (model or "").strip() or None
        if max_turns is not None and (
            isinstance(max_turns, bool)
            or not isinstance(max_turns, int)
            or max_turns <= 0
        ):
            raise WebError(400, "每次最多回合必須是正整數")
        try:
            config_lib.save_model_settings(self.project.root, model, max_turns)
        except ValueError as error:
            raise WebError(400, str(error)) from error
        self._settings = {"model": model, "maxTurns": max_turns}
        self._apply_settings()
        self._emit({"type": "project"})

    async def test_model(self) -> dict[str, Any]:
        """用預設 agent 的模型送一個很短的請求，確認連得上。"""
        agent = self.project.agents[self.project.default_agent]
        model = agent.model or llm.default_model()
        messages = [{"role": "user", "content": "ping"}]

        async def first_item() -> None:
            stream = self._model_fn(model, messages, [], agent.model_params)
            async for _ in stream:
                break
            await stream.aclose()

        try:
            await asyncio.wait_for(first_item(), _MODEL_TEST_TIMEOUT)
        except TimeoutError:
            return {
                "ok": False,
                "model": model,
                "message": f"{_MODEL_TEST_TIMEOUT} 秒內沒有回應",
            }
        # 錯誤種類取決於模型供應商，全部轉成訊息顯示在設定頁。
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "model": model, "message": _describe(error)}
        return {"ok": True, "model": model}

    async def close(self) -> None:
        """停止所有正在執行的對話，並等它們收尾。"""
        tasks = [s.task for s in self._states.values() if s.task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _start(self, state: _State, text: str | None) -> None:
        """設定好執行中的狀態，再把執行放進背景。"""
        state.status = "running"
        state.activity = {"kind": "thinking"}
        state.started_at = _now()
        state.ended_at = None
        state.error = None
        state.notice = None
        state.tool_errors = 0
        state.stop_reason = None
        state.partial = ""
        self._emit(
            {"type": "run_start", "sessionId": state.session.id, "text": text}
        )
        self._touch(state)
        state.task = asyncio.get_running_loop().create_task(
            self._execute(state, text)
        )

    async def _execute(self, state: _State, text: str | None) -> None:
        session_id = state.session.id
        project = self.project
        try:
            events = loop.run_stream(
                project.agents[state.agent],
                text,
                session=state.session,
                permissions=self._permissions_for(state),
                workdir=project.root,
                hooks=project.hooks,
                memory=project.memory,
                compaction=project.compaction,
                model_fn=self._model_fn,
            )
            async for event in events:
                self._handle(state, event)
        except asyncio.CancelledError:
            state.status = "stopped"
            self._emit({"type": "run_stopped", "sessionId": session_id})
        # 模型或網路出錯時停下來讓使用者重試；錯誤種類取決於供應商。
        except Exception as error:  # noqa: BLE001
            state.status = "error"
            state.error = _describe(error)
            self._emit(
                {
                    "type": "run_error",
                    "sessionId": session_id,
                    "message": state.error,
                }
            )
        finally:
            state.task = None
            for pending in list(state.approvals.values()):
                if not pending.future.done():
                    pending.future.set_result(False)
            state.approvals.clear()
            state.activity = None
            state.partial = ""
            state.ended_at = _now()
            self._touch(state)

    def _handle(self, state: _State, event: datatypes.Event) -> None:
        """把主迴圈的事件轉成給瀏覽器的事件。"""
        session_id = state.session.id
        data = event.data
        if event.type == "text_delta":
            state.partial += data["text"]
            self._emit(
                {
                    "type": "text_delta",
                    "sessionId": session_id,
                    "text": data["text"],
                }
            )
            self._set_activity(state, {"kind": "writing"})
        elif event.type == "message":
            state.partial = ""
            self._emit(
                {
                    "type": "message",
                    "sessionId": session_id,
                    "message": data["message"],
                }
            )
        elif event.type == "tool_call":
            self._set_activity(
                state, {"kind": "tool", "tool": data["call"].name}
            )
        elif event.type == "tool_result":
            result = data["result"]
            if result.is_error:
                state.tool_errors += 1
            # 主迴圈先把結果存進紀錄才產生事件，所以最後一則就是它。
            self._emit(
                {
                    "type": "tool_result",
                    "sessionId": session_id,
                    "result": {
                        "callId": result.call_id,
                        "name": result.name,
                        "output": result.output,
                        "isError": result.is_error,
                    },
                    "message": state.session.messages[-1],
                }
            )
            self._set_activity(state, {"kind": "thinking"})
        elif event.type == "handoff":
            # 摘要裡的 agent 由對話紀錄推算，轉交後自然換成接手的 agent。
            self._touch(state)
        elif event.type == "compaction":
            # 壓縮紀錄已經寫進 session，讓瀏覽器重新讀取。
            self._emit({"type": "session_reload", "sessionId": session_id})
        elif event.type == "subagent":
            self._handle_subagent(state, data)
        elif event.type == "run_end":
            result = data["result"]
            state.status = "done"
            state.stop_reason = result.stop_reason
            if result.stop_reason == "blocked":
                # 被防護擋下的內容不會存進紀錄，另外記下來顯示。
                state.notice = result.output
            self._emit(
                {
                    "type": "run_end",
                    "sessionId": session_id,
                    "stopReason": result.stop_reason,
                }
            )

    def _handle_subagent(self, state: _State, data: dict[str, Any]) -> None:
        """subagent 的進度：記下它呼叫的工具，並更新「正在做什麼」。"""
        inner = data["event"]
        call_id = data["call_id"]
        path = data["path"]
        steps = state.subagent_steps.setdefault(call_id, [])
        step = None
        if inner.type == "tool_call":
            call = inner.data["call"]
            step = {
                "id": f"{call_id}.{len(steps)}",
                "callId": call.id,
                "agent": data["agent"],
                "path": path,
                "name": call.name,
                "arguments": call.arguments,
                "status": "running",
            }
            steps.append(step)
            activity = {"kind": "tool", "tool": call.name}
        elif inner.type == "tool_result":
            result = inner.data["result"]
            step = next(
                (
                    item
                    for item in reversed(steps)
                    if item["callId"] == result.call_id
                    and item["path"] == path
                    and item["status"] == "running"
                ),
                None,
            )
            if step is not None:
                step["status"] = "error" if result.is_error else "done"
                step["result"] = _shorten(result.output)
            activity = {"kind": "thinking"}
        elif inner.type == "text_delta":
            activity = {"kind": "writing"}
        else:
            return
        if step is not None:
            self._emit(
                {
                    "type": "subagent_step",
                    "sessionId": state.session.id,
                    "callId": call_id,
                    "step": step,
                }
            )
        activity["agent"] = " › ".join(path)
        self._set_activity(state, activity)

    def _permissions_for(self, state: _State) -> permissions_lib.Permissions:
        """這段對話使用的權限設定。

        規則永遠讀專案目前的值，所以在設定頁或「以後都允許」改過的
        規則，執行中的對話也會立刻套用。
        """
        return _LivePermissions(
            self.project.permissions, state, self._approver(state)
        )

    def _approver(self, state: _State) -> permissions_lib.Approver:
        async def approve(
            call: datatypes.ToolCall, target: tools_lib.Tool
        ) -> bool:
            future = asyncio.get_running_loop().create_future()
            approval = {
                "id": uuid.uuid4().hex,
                "sessionId": state.session.id,
                "callId": call.id,
                "tool": target.name,
                "arguments": call.arguments,
            }
            preview = self._preview(call)
            if preview is not None:
                approval["preview"] = preview
            state.approvals[approval["id"]] = _Pending(approval, future)
            state.status = "waiting"
            state.activity = {"kind": "waiting", "tool": target.name}
            self._emit(
                {
                    "type": "approval",
                    "sessionId": state.session.id,
                    "approval": approval,
                }
            )
            self._touch(state)
            try:
                return await future
            finally:
                state.approvals.pop(approval["id"], None)
                if state.task is not None and not state.task.cancelling():
                    state.status = "running"
                    state.activity = {"kind": "tool", "tool": target.name}
                    self._touch(state)

        return approve

    def _preview(self, call: datatypes.ToolCall) -> dict[str, Any] | None:
        """寫檔類的呼叫（參數有 path 與 content）附上檔案原本的內容。"""
        path = call.arguments.get("path")
        content = call.arguments.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            return None
        context = datatypes.RunContext("", self.project.root, "")
        try:
            target = files.resolve_path(context, path)
            before = None
            if target.is_file():
                text = target.read_text(encoding="utf-8", errors="replace")
                before = text[:_PREVIEW_LIMIT]
        except (tools_lib.ToolError, OSError):
            return None
        return {"path": path, "before": before, "after": content}

    def _allow_always(self, tool_name: str) -> None:
        current = self._permission_settings()
        if tool_name in current["allow"]:
            return
        current["allow"] = [*current["allow"], tool_name]
        self.update_permissions(current)

    def _permission_settings(self) -> dict[str, Any]:
        shared = self.project.permissions
        return {
            "mode": shared.mode,
            "allow": list(shared.allow),
            "ask": list(shared.ask),
            "deny": list(shared.deny),
        }

    def _apply_settings(self) -> None:
        """把網頁上改過的 harness.yaml 套用到 agent；agent 檔案自己寫的優先。"""
        for name, agent in self.project.agents.items():
            own = self._overrides.get(name, {})
            if "model" not in own:
                agent.model = self._settings["model"]
            if "maxTurns" not in own:
                agent.max_turns = self._settings["maxTurns"] or 20

    def _agent_info(self, name: str) -> dict[str, Any]:
        agent = self.project.agents[name]
        own = self._overrides.get(name, {})
        env_model = os.environ.get("HARNESS_MODEL")
        if "model" in own:
            model_source = "agent"
        elif self._settings["model"]:
            model_source = "config"
        elif env_model:
            model_source = "env"
        else:
            model_source = "default"
        if "maxTurns" in own:
            turns_source = "agent"
        elif self._settings["maxTurns"]:
            turns_source = "config"
        else:
            turns_source = "default"
        return {
            "name": name,
            "description": agent.description,
            "tools": [item.name for item in agent.tools],
            "model": agent.model or llm.default_model(),
            "modelSource": model_source,
            "maxTurns": agent.max_turns,
            "maxTurnsSource": turns_source,
        }

    def _create(self, agent: str) -> _State:
        if agent not in self.project.agents:
            raise WebError(400, f"沒有 {agent} 這個 agent")
        session = sessions_lib.create(self.project.sessions_dir)
        self.project.sessions_dir.mkdir(parents=True, exist_ok=True)
        _meta_path(self.project, session.id).write_text(
            json.dumps({"agent": agent}), encoding="utf-8"
        )
        return self._track(session, agent, _now())

    def _find(self, session_id: str) -> _State | None:
        if session_id in self._states:
            return self._states[session_id]
        directory = self.project.sessions_dir
        path = directory / f"{session_id}.jsonl"
        if not _SESSION_ID.match(session_id) or not path.exists():
            return None
        session = sessions_lib.load(directory, session_id)
        entry = _read_meta_agent(self.project, session_id)
        return self._track(session, entry, path.stat().st_mtime * 1000)

    def _require(self, session_id: str) -> _State:
        state = self._find(session_id)
        if state is None:
            raise WebError(404, f"找不到對話：{session_id}")
        return state

    def _track(
        self, session: sessions_lib.Session, agent: str | None, updated: float
    ) -> _State:
        entry = self.project.agents.get(
            agent or "", self.project.agents[self.project.default_agent]
        )
        first = next(
            (m for m in session.messages if m.get("role") == "user"), None
        )
        state = _State(
            session=session,
            agent=entry.name,
            title=_title(first.get("content") or "") if first else _UNTITLED,
            updated_at=updated,
        )
        self._states[session.id] = state
        return state

    def _assert_idle(self, state: _State) -> None:
        if state.task is not None:
            raise WebError(409, "這段對話還在執行，請等它結束或先停止")

    def _set_activity(self, state: _State, activity: dict[str, Any]) -> None:
        """狀態變了才通知，避免每個文字片段都送一次摘要。"""
        if state.activity == activity:
            return
        state.activity = activity
        self._touch(state)

    def _touch(self, state: _State) -> None:
        state.updated_at = _now()
        self._emit({"type": "session", "summary": self._summary(state)})

    def _summary(self, state: _State) -> dict[str, Any]:
        # 對話可能已經轉交給別的 agent，顯示目前負責的那個。
        entry = self.project.agents[state.agent]
        active = delegation.active_agent(entry, state.session.messages)
        pending = next(iter(state.approvals.values()), None)
        summary = {
            "id": state.session.id,
            "title": state.title,
            "agent": active.name,
            "status": state.status,
            "updatedAt": state.updated_at,
            "toolErrors": state.tool_errors,
        }
        optional = {
            "activity": state.activity,
            "startedAt": state.started_at,
            "endedAt": state.ended_at,
            "error": state.error,
            "notice": state.notice,
            "stopReason": state.stop_reason,
            "approval": pending.approval if pending else None,
        }
        summary.update({k: v for k, v in optional.items() if v is not None})
        return summary

    def _emit(self, event: Event) -> None:
        for listener in list(self._listeners):
            listener(event)


class _LivePermissions(permissions_lib.Permissions):
    """跟著專案權限即時變動、模式可以每段對話各自設定的權限。"""

    def __init__(
        self,
        shared: permissions_lib.Permissions,
        state: _State,
        approver: permissions_lib.Approver,
    ) -> None:
        # 不呼叫父類別的 __init__：欄位都改由 property 即時讀取。
        self._shared = shared
        self._state = state
        self.approver = approver

    @property
    def mode(self) -> str:
        return self._state.mode or self._shared.mode

    @property
    def allow(self) -> list[str]:
        return self._shared.allow

    @property
    def deny(self) -> list[str]:
        return self._shared.deny

    @property
    def ask(self) -> list[str]:
        return self._shared.ask


def _tool_info(item: tools_lib.Tool) -> dict[str, Any]:
    return {
        "name": item.name,
        "description": item.description,
        "needsApproval": item.needs_approval,
    }


def _clean_patterns(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    cleaned = [item.strip() for item in items if isinstance(item, str)]
    return list(dict.fromkeys(item for item in cleaned if item))


def _shorten(text: str) -> str:
    """太長的 subagent 工具結果只留開頭。"""
    if len(text) <= _SUBAGENT_RESULT_LIMIT:
        return text
    return text[:_SUBAGENT_RESULT_LIMIT] + "\n…（以下省略）"


def _title(text: str) -> str:
    line = text.strip().split("\n")[0]
    if len(line) > _TITLE_LENGTH:
        return f"{line[:_TITLE_LENGTH]}…"
    return line


def _meta_path(project: project_lib.Project, session_id: str):
    return project.sessions_dir / f"{session_id}.meta.json"


def _read_meta_agent(
    project: project_lib.Project, session_id: str
) -> str | None:
    """讀取對話的起始 agent（存在 session 旁邊的 .meta.json）。"""
    try:
        meta = json.loads(_meta_path(project, session_id).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    agent = meta.get("agent") if isinstance(meta, dict) else None
    return agent if isinstance(agent, str) else None


def _describe(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def _now() -> float:
    """目前時間（毫秒），和瀏覽器的 Date.now() 同單位。"""
    return time.time() * 1000
