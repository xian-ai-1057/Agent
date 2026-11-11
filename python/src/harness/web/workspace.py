"""網頁版的核心：管理一個專案裡所有對話的執行狀態。

每段對話可以同時各自執行。執行過程中的每一步都會變成事件推給訂閱者
（伺服器再用 SSE 轉給瀏覽器）；需要使用者同意的工具呼叫會停下來，等
瀏覽器回覆 resolve_approval。

送給瀏覽器的資料格式和 TypeScript 版完全相同（見根目錄的
src/web/protocol.ts），兩個版本共用同一份前端。所以這裡的 dict 鍵名
一律用 camelCase。

Python 版另外支援：對話改名、刪除與搜尋，每段對話自己選模型、工具與
skills，以及對回覆按讚或倒讚。這些資料存在對話紀錄旁邊的
<id>.meta.json，對話紀錄（JSONL）的格式不變；評價另外附加到
.harness/feedback.jsonl，方便管理者之後檢視。
"""

import asyncio
import dataclasses
import datetime
import functools
import json
import os
import pathlib
import re
import time
import uuid
from collections.abc import Callable
from typing import Any

from harness import builtin
from harness import compaction as compaction_lib
from harness import credentials
from harness import datatypes
from harness import delegation
from harness import llm
from harness import loop
from harness import permissions as permissions_lib
from harness import project as project_lib
from harness import providers
from harness import sandbox as sandbox_lib
from harness import sessions as sessions_lib
from harness import skills as skills_lib
from harness import tools as tools_lib
from harness.builtin import files
from harness.web import config as config_lib
from harness.web import uploads as uploads_lib

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
_CUSTOM_TITLE_LIMIT = 100
_SEARCH_LIMIT = 30
_RECENT_LIMIT = 20
_SNIPPET_LENGTH = 60
RATINGS = ("up", "down")
_COMMENT_LIMIT = 2000
FEEDBACK_LOG = project_lib.DATA_DIR / "feedback.jsonl"
ENDPOINT_PROVIDER = "OpenAI 相容端點"


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
    # 以下存在 .meta.json，伺服器重新啟動後仍然保留。
    custom_title: str | None = None
    options: dict[str, Any] = dataclasses.field(
        default_factory=lambda: _default_options()
    )
    # 以 assistant 訊息的索引（字串）為鍵。
    feedback: dict[str, dict[str, Any]] = dataclasses.field(
        default_factory=dict
    )


class Workspace:
    """一個專案的所有對話。

    Attributes:
        project: 載入完成的專案。
        uploads: 上傳檔案的資料夾。
    """

    def __init__(
        self,
        project: project_lib.Project,
        *,
        model_fn: llm.ModelFn | None = None,
        max_upload_bytes: int = uploads_lib.DEFAULT_MAX_BYTES,
    ) -> None:
        self.project = project
        self.uploads = uploads_lib.Uploads.for_project(
            project, max_upload_bytes
        )
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
        # harness.yaml 的 models：管理者另外開放給使用者選的模型。
        self._model_choices = config_lib.read_model_choices(project.root)
        self._builtin_skills = project_lib.builtin_skills()

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
            "uploads": {"maxBytes": self.uploads.max_bytes},
            "defaultAgent": project.default_agent,
            "agents": [self._agent_info(name) for name in project.agents],
            "toolsets": [
                {
                    "name": name,
                    "builtin": builtin.is_builtin_toolset(name, items)
                    or name == sandbox_lib.TOOLSET,
                    "tools": [_tool_info(item) for item in items],
                }
                for name, items in project.toolsets.items()
            ],
            "permissions": self._permission_settings(),
            "features": {
                "files": True,
                "sessionAdmin": True,
                "search": True,
                "sessionOptions": True,
                "feedback": True,
            },
            "models": self._model_options(),
            "skills": [
                {
                    "name": item.name,
                    "description": item.description,
                    "builtin": builtin_skill,
                }
                for item, builtin_skill in self._available_skills().values()
            ],
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
                    "fallbackModel": llm.default_model(),
                },
                "connection": self._connection_info(),
            },
        }

    def list_sessions(self) -> list[dict[str, Any]]:
        """所有對話的摘要，最近有動靜的排前面。"""
        for session_id in sessions_lib.list_ids(self.project.sessions_dir):
            self._find(session_id)
        summaries = [self._summary(state) for state in self._states.values()]
        return sorted(summaries, key=lambda item: -item["updatedAt"])

    def get_session(self, session_id: str) -> dict[str, Any]:
        """一段對話的完整內容，含選項與使用者對回覆的評價。

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
            "options": dict(state.options),
            "feedback": state.feedback,
        }

    def start_run(
        self,
        text: str,
        session_id: str | None = None,
        agent: str | None = None,
        attachments: list[str] | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """送出一則訊息並在背景開始執行。沒給 session_id 時建立新的對話。

        Args:
            text: 使用者的訊息。
            session_id: 要接續的對話；None 表示建立新的對話。
            agent: 新對話使用的 agent。
            attachments: 上傳時回傳的檔案路徑，說明會接在訊息後面。
            options: 新對話的選項（見 set_options），只需要給要改的欄位。

        Raises:
            WebError: 訊息與附件都是空的、附件不是上傳的檔案、agent 不
                存在、選項不合法，或這段對話還在執行。
        """
        text = text.strip()
        try:
            notes = self.uploads.describe(attachments or [])
        except uploads_lib.UploadError as error:
            raise WebError(error.status, str(error)) from error
        if not text and not notes:
            raise WebError(400, "訊息不可以是空的")
        if session_id:
            state = self._require(session_id)
        else:
            agent = agent or self.project.default_agent
            if agent not in self.project.agents:
                raise WebError(400, f"沒有 {agent} 這個 agent")
            # 先檢查選項，不合法時不會留下空的對話。
            checked = self._checked_options(
                options or {}, _default_options(), agent
            )
            state = self._create(agent)
            if checked != state.options:
                state.options = checked
                self._save_meta(state)
        self._assert_idle(state)
        if state.title == _UNTITLED:
            first = pathlib.PurePosixPath(attachments[0]).name if notes else ""
            state.title = _title(text or first)
        self._start(state, "\n\n".join(part for part in (text, notes) if part))
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

    def rename(self, session_id: str, title: str) -> dict[str, Any]:
        """幫對話改名；名稱會存檔，伺服器重新啟動後仍然保留。

        Args:
            session_id: 要改名的對話。
            title: 新的名稱，前後的空白會去掉。

        Returns:
            改名後的摘要。

        Raises:
            WebError: 找不到對話，或名稱是空的、超過 100 個字。
        """
        state = self._require(session_id)
        title = title.strip()
        if not title or len(title) > _CUSTOM_TITLE_LIMIT:
            raise WebError(400, f"名稱要有 1 到 {_CUSTOM_TITLE_LIMIT} 個字")
        state.custom_title = title
        self._save_meta(state)
        summary = self._summary(state)
        self._emit({"type": "session", "summary": summary})
        return summary

    def delete(self, session_id: str) -> None:
        """刪除對話的紀錄與設定，並通知瀏覽器（session_deleted）。

        Raises:
            WebError: 找不到對話，或它還在執行、等使用者確認。
        """
        state = self._require(session_id)
        if state.task is not None or state.status in ("running", "waiting"):
            raise WebError(409, "這段對話還在執行，請先停止再刪除")
        state.session.path.unlink(missing_ok=True)
        _meta_path(self.project, session_id).unlink(missing_ok=True)
        del self._states[session_id]
        self._emit({"type": "session_deleted", "sessionId": session_id})

    def search(self, query: str) -> list[dict[str, Any]]:
        """依標題與對話內容搜尋對話，最近有動靜的排前面。

        不分大小寫，比對標題以及使用者與 agent 的訊息（不含工具結果與
        附件說明）。

        Args:
            query: 要找的文字；空的時回傳最近的 20 段對話。

        Returns:
            SearchResult（見 src/web/protocol.ts）的列表，最多 30 筆。
            命中訊息內容時附上含關鍵字的一小段文字。
        """
        self.list_sessions()
        states = sorted(self._states.values(), key=lambda s: -s.updated_at)
        query = query.strip()
        if not query:
            return [_search_result(s, self._title(s)) for s in states][
                :_RECENT_LIMIT
            ]
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        results = []
        for state in states:
            title = self._title(state)
            snippet = _find_snippet(state.session.messages, pattern)
            if snippet is None and not pattern.search(title):
                continue
            results.append(_search_result(state, title, snippet))
            if len(results) >= _SEARCH_LIMIT:
                break
        return results

    def set_options(
        self, session_id: str, changes: dict[str, Any]
    ) -> dict[str, Any]:
        """設定對話自己的模型、工具與 skills，下一輪開始生效。

        Args:
            session_id: 要設定的對話。
            changes: 要改的欄位：model（ProjectInfo.models 裡的名稱，null
                表示跟著 agent）、tools（agent 的工具名稱，null 表示全部）、
                skills（一定要用的 skill 名稱）。沒給的欄位不變。

        Returns:
            設定後完整的選項。

        Raises:
            WebError: 找不到對話，或選項不合法。
        """
        state = self._require(session_id)
        agent = self._active_agent(state).name
        state.options = self._checked_options(changes, state.options, agent)
        self._save_meta(state)
        self._emit({"type": "session", "summary": self._summary(state)})
        return dict(state.options)

    def set_feedback(self, session_id: str, index: Any, feedback: Any) -> None:
        """記下使用者對一則回覆的評價；None 表示取消。

        評價存在對話的 .meta.json；每次送出的評價另外附加一行到
        .harness/feedback.jsonl，給管理者之後檢視。

        Args:
            session_id: 對話。
            index: 這則 assistant 訊息在 messages 裡的索引。
            feedback: {"rating": "up" | "down", "reasons": [...],
                "comment": "..."}，或 None。

        Raises:
            WebError: 找不到對話、索引不是 assistant 訊息，或評價不合法。
        """
        state = self._require(session_id)
        messages = state.session.messages
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < len(messages)
            or messages[index].get("role") != "assistant"
        ):
            raise WebError(400, "index 要指向一則 agent 的回覆")
        key = str(index)
        if feedback is None:
            state.feedback.pop(key, None)
            self._save_meta(state)
            return
        cleaned = _checked_feedback(feedback)
        state.feedback[key] = cleaned
        self._save_meta(state)
        record = {
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "sessionId": session_id,
            "index": index,
            "rating": cleaned["rating"],
            "reasons": cleaned.get("reasons", []),
            "comment": cleaned.get("comment", ""),
            "model": self._run_model(state),
        }
        log = self.project.root / FEEDBACK_LOG
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

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

    async def update_connection(self, settings: dict[str, Any]) -> None:
        """設定要連哪家供應商（或哪個端點）、金鑰與模型。

        先用新的設定實際連一次模型，連得上才存檔；連不上時恢復原本的
        設定並回報原因，所以不會存下打錯的金鑰。金鑰存在專案的
        .harness/secrets/（見 credentials 模組），模型名稱寫進
        harness.yaml。

        Args:
            settings: {"provider": 供應商代號或 "endpoint", "apiKey": 金鑰,
                "baseURL": 端點或 Ollama 的網址, "model": 模型名稱}。
                apiKey 與 baseURL 留空表示沿用目前的值。

        Raises:
            WebError: 設定不完整，或用新的設定連不上模型。
        """
        values, model = self._connection_values(settings)
        own = self._overrides.get(self.project.default_agent, {})
        before = {name: os.environ.get(name) for name in values}
        _set_environment(values)
        result = await self.test_model(own.get("model") or model)
        if not result["ok"]:
            _set_environment(before)
            raise WebError(400, _explain_failure(result["message"]))
        try:
            config_lib.save_model_settings(
                self.project.root, model, self._settings["maxTurns"]
            )
        except ValueError as error:
            _set_environment(before)
            raise WebError(400, str(error)) from error
        credentials.update(self.project.root, values)
        self._settings = {**self._settings, "model": model}
        self._apply_settings()
        self._emit({"type": "project"})

    async def test_model(self, model: str | None = None) -> dict[str, Any]:
        """送一個很短的請求，確認連得上模型。

        Args:
            model: 要測試的模型，預設是預設 agent 目前使用的模型。
        """
        agent = self.project.agents[self.project.default_agent]
        model = model or agent.model or llm.default_model()
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
                self._run_agent(state),
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

    def _connection_values(
        self, settings: dict[str, Any]
    ) -> tuple[dict[str, str], str]:
        """檢查連線設定，回傳 (要改的環境變數, 模型名稱)。

        環境變數的值是空字串時表示不使用它。
        """
        provider_id = settings.get("provider")
        api_key = _clean(settings.get("apiKey"))
        base_url = _clean(settings.get("baseURL"))
        model = _clean(settings.get("model"))
        if not model:
            raise WebError(400, "請填寫模型名稱")
        if provider_id == providers.ENDPOINT:
            base_url = base_url or os.environ.get(providers.BASE_URL_VAR)
            if not base_url:
                raise WebError(400, "請填寫端點網址")
            values = {providers.BASE_URL_VAR: _checked_url(base_url)}
            if api_key:
                values[providers.API_KEY_VAR] = api_key
            return values, model
        provider = providers.find(provider_id or "")
        if provider is None:
            raise WebError(400, f"沒有 {provider_id} 這個供應商")
        if not provider.matches(model):
            raise WebError(
                400,
                f"{provider.label} 的模型名稱要以 {provider.id}/ 開頭，"
                f"例如 {provider.default_model}",
            )
        # 直接連供應商：不再使用端點。
        values = {providers.BASE_URL_VAR: ""}
        if provider.key_var:
            if api_key:
                values[provider.key_var] = api_key
            elif not provider.key_set():
                raise WebError(400, "請填寫金鑰")
        if provider.base_url_var and base_url:
            values[provider.base_url_var] = _checked_url(base_url)
        return values, model

    def _connection_info(self) -> dict[str, Any]:
        """連線設定的狀態：目前用哪家供應商、金鑰設定了沒。不含金鑰內容。"""
        agent = self.project.agents[self.project.default_agent]
        model = agent.model or llm.default_model()
        if providers.uses_endpoint():
            current = providers.ENDPOINT
        else:
            found = providers.of_model(model)
            current = found.id if found else None
        return {
            "ready": providers.is_ready(model),
            "provider": current,
            "providers": [
                {
                    "id": item.id,
                    "label": item.label,
                    "defaultModel": item.default_model,
                    "needsKey": item.key_var is not None,
                    "keyVar": item.key_var,
                    "keySet": item.key_var is not None and item.key_set(),
                    "baseURL": os.environ.get(item.base_url_var)
                    or item.default_base_url
                    if item.base_url_var
                    else None,
                }
                for item in providers.PROVIDERS
            ],
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

    def _model_options(self) -> list[dict[str, Any]]:
        """輸入框可以選的模型。

        依序是專案預設的模型、各 agent 的模型與 harness.yaml 的 models，
        名稱重複的只留第一個。
        """
        default = self._settings["model"] or llm.default_model()
        choices = [{"name": default, "isDefault": True}]
        choices += [
            {"name": agent.model or llm.default_model()}
            for agent in self.project.agents.values()
        ]
        choices += self._model_choices
        options: dict[str, dict[str, Any]] = {}
        for choice in choices:
            name = choice["name"]
            if name in options:
                continue
            option = {
                "name": name,
                "label": choice.get("label") or _model_label(name),
                "provider": _provider_label(name),
            }
            vision = None if providers.uses_endpoint() else _vision(name)
            if vision is not None:
                option["vision"] = vision
            if choice.get("isDefault"):
                option["isDefault"] = True
            options[name] = option
        return list(options.values())

    def _available_skills(self) -> dict[str, tuple[skills_lib.Skill, bool]]:
        """專案的 skills 加上內建 skills；同名時以專案的為準。

        Returns:
            以名稱為鍵，值為 (skill, 是否為內建)。
        """
        found = {
            name: (item, True) for name, item in self._builtin_skills.items()
        }
        found.update(
            {name: (item, False) for name, item in self.project.skills.items()}
        )
        return dict(sorted(found.items()))

    def _checked_options(
        self, changes: Any, current: dict[str, Any], agent_name: str
    ) -> dict[str, Any]:
        """檢查要改的選項，回傳合併後完整的選項。"""
        if not isinstance(changes, dict):
            raise WebError(400, "options 必須是物件")
        options = dict(current)
        if "model" in changes:
            model = changes["model"]
            if model is not None and not isinstance(model, str):
                raise WebError(400, "model 必須是字串或 null")
            model = (model or "").strip() or None
            names = [item["name"] for item in self._model_options()]
            if model is not None and model not in names:
                raise WebError(400, f"沒有 {model} 這個模型可以選")
            options["model"] = model
        if "tools" in changes:
            tools = changes["tools"]
            if tools is not None:
                tools = _string_list(tools, "tools")
                agent = self.project.agents[agent_name]
                own = {item.name for item in agent.tools}
                unknown = [name for name in tools if name not in own]
                if unknown:
                    raise WebError(
                        400,
                        f"agent {agent_name} 沒有這些工具："
                        f"{'、'.join(unknown)}",
                    )
            options["tools"] = tools
        if "skills" in changes:
            chosen = _string_list(changes["skills"] or [], "skills")
            available = self._available_skills()
            unknown = [name for name in chosen if name not in available]
            if unknown:
                raise WebError(400, f"沒有這些 skill：{'、'.join(unknown)}")
            options["skills"] = chosen
        return options

    def _active_agent(self, state: _State):
        """目前負責這段對話的 agent（可能已經轉交給別的 agent）。"""
        entry = self.project.agents[state.agent]
        return delegation.active_agent(entry, state.session.messages)

    def _run_agent(self, state: _State):
        """這一輪要用的 agent：套用這段對話自己選的模型、工具與 skills。

        改的是副本，專案共用的 agent 不受影響。
        """
        agent = self._active_agent(state)
        options = state.options
        changes: dict[str, Any] = {}
        model = self._chosen_model(state)
        if model is not None:
            changes["model"] = model
        if options["tools"] is not None:
            allowed = set(options["tools"])
            changes["tools"] = [t for t in agent.tools if t.name in allowed]
        available = self._available_skills()
        chosen = [available[n][0] for n in options["skills"] if n in available]
        if chosen:
            own = {item.name: item for item in agent.skills}
            changes["skills"] = [
                *agent.skills,
                *(item for item in chosen if item.name not in own),
            ]
            names = "、".join(item.name for item in chosen)
            hint = (
                f"使用者指定這段對話要使用這些技能：{names}。處理工作前，"
                f"還沒載入的先用 {skills_lib.LOAD_TOOL} 載入，照著它的指示做。"
            )
            changes["instructions"] = "\n\n".join(
                part for part in (agent.instructions, hint) if part
            )
        return dataclasses.replace(agent, **changes) if changes else agent

    def _chosen_model(self, state: _State) -> str | None:
        """這段對話自己選的模型；已經不在可選清單裡時不使用。"""
        model = state.options["model"]
        if model is None:
            return None
        names = [item["name"] for item in self._model_options()]
        return model if model in names else None

    def _run_model(self, state: _State) -> str:
        """這段對話下一輪實際會用的模型。"""
        agent = self._active_agent(state)
        return self._chosen_model(state) or agent.model or llm.default_model()

    def _create(self, agent: str) -> _State:
        if agent not in self.project.agents:
            raise WebError(400, f"沒有 {agent} 這個 agent")
        session = sessions_lib.create(self.project.sessions_dir)
        state = self._track(session, agent, _now())
        self._save_meta(state)
        return state

    def _find(self, session_id: str) -> _State | None:
        if session_id in self._states:
            return self._states[session_id]
        directory = self.project.sessions_dir
        path = directory / f"{session_id}.jsonl"
        if not _SESSION_ID.match(session_id) or not path.exists():
            return None
        session = sessions_lib.load(directory, session_id)
        meta = _read_meta(self.project, session_id)
        agent = meta.get("agent")
        state = self._track(
            session,
            agent if isinstance(agent, str) else None,
            path.stat().st_mtime * 1000,
        )
        _restore_meta(state, meta)
        return state

    def _save_meta(self, state: _State) -> None:
        """把起始 agent、改過的名稱、選項與評價寫進 .meta.json。"""
        meta: dict[str, Any] = {"agent": state.agent}
        if state.custom_title is not None:
            meta["title"] = state.custom_title
        if state.options != _default_options():
            meta["options"] = state.options
        if state.feedback:
            meta["feedback"] = state.feedback
        _write_meta(self.project, state.session.id, meta)

    def _title(self, state: _State) -> str:
        return state.custom_title or state.title

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
            "title": self._title(state),
            "agent": active.name,
            "model": state.options["model"],
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


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _checked_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        raise WebError(400, "網址要以 http:// 或 https:// 開頭")
    return url


def _explain_failure(message: str) -> str:
    """連線測試失敗時，在原始錯誤前面加上一句看得懂的說明。"""
    hint = "連不上模型"
    if "AuthenticationError" in message:
        hint = "金鑰不正確，或這把金鑰不能使用這個模型"
    elif "NotFoundError" in message:
        hint = "找不到這個模型，請確認模型名稱"
    elif "APIConnectionError" in message or "Timeout" in message:
        hint = "連不到模型的伺服器，請確認網址與網路（需要 proxy 時要先設定）"
    return f"{hint}。詳細訊息：{message}"


def _set_environment(values: dict[str, str | None]) -> None:
    """套用環境變數；空字串或 None 表示拿掉。"""
    for name, value in values.items():
        if value:
            os.environ[name] = value
        else:
            os.environ.pop(name, None)


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


def _read_meta(project: project_lib.Project, session_id: str) -> dict[str, Any]:
    """讀取存在 session 旁邊的 .meta.json；沒有或壞掉時為空 dict。

    內容：agent（起始 agent）、title（使用者改過的名稱）、options、
    feedback，除了 agent 都可能沒有。
    """
    try:
        meta = json.loads(_meta_path(project, session_id).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _write_meta(
    project: project_lib.Project, session_id: str, meta: dict[str, Any]
) -> None:
    """先寫到暫存檔再換掉，寫到一半當掉也不會留下壞掉的檔案。"""
    path = _meta_path(project, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(meta, ensure_ascii=False), "utf-8")
    os.replace(temporary, path)


def _restore_meta(state: _State, meta: dict[str, Any]) -> None:
    """把 .meta.json 裡的名稱、選項與評價放回對話狀態；格式不對的略過。"""
    title = meta.get("title")
    if isinstance(title, str) and title.strip():
        state.custom_title = title
    options = meta.get("options")
    if isinstance(options, dict):
        model = options.get("model")
        tools = options.get("tools")
        chosen = options.get("skills")
        state.options = {
            "model": model if isinstance(model, str) else None,
            "tools": tools if _is_string_list(tools) else None,
            "skills": chosen if _is_string_list(chosen) else [],
        }
    feedback = meta.get("feedback")
    if isinstance(feedback, dict):
        state.feedback = {
            key: value
            for key, value in feedback.items()
            if isinstance(value, dict) and value.get("rating") in RATINGS
        }


def _default_options() -> dict[str, Any]:
    return {"model": None, "tools": None, "skills": []}


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(i, str) for i in value)


def _string_list(value: Any, key: str) -> list[str]:
    """檢查是字串陣列，去掉重複的項目。"""
    if not _is_string_list(value):
        raise WebError(400, f"{key} 必須是字串陣列")
    return list(dict.fromkeys(value))


def _checked_feedback(feedback: Any) -> dict[str, Any]:
    """檢查使用者送來的評價，只留認得的欄位。"""
    if not isinstance(feedback, dict):
        raise WebError(400, "feedback 必須是物件或 null")
    rating = feedback.get("rating")
    if rating not in RATINGS:
        raise WebError(400, f"rating 必須是 {'、'.join(RATINGS)}")
    cleaned: dict[str, Any] = {"rating": rating}
    reasons = feedback.get("reasons")
    if reasons is not None:
        cleaned["reasons"] = _string_list(reasons, "reasons")
    comment = feedback.get("comment")
    if comment is not None:
        if not isinstance(comment, str):
            raise WebError(400, "comment 必須是字串")
        if len(comment) > _COMMENT_LIMIT:
            raise WebError(400, f"意見最多 {_COMMENT_LIMIT} 個字")
        cleaned["comment"] = comment
    return cleaned


def _search_result(
    state: _State, title: str, snippet: str | None = None
) -> dict[str, Any]:
    result = {
        "id": state.session.id,
        "title": title,
        "updatedAt": state.updated_at,
    }
    if snippet is not None:
        result["snippet"] = snippet
    return result


def _find_snippet(
    messages: list[datatypes.Message], pattern: re.Pattern
) -> str | None:
    """在使用者與 agent 的訊息裡找關鍵字，回傳它附近的一小段文字。"""
    for message in messages:
        if message.get("role") not in ("user", "assistant"):
            continue
        text = _message_text(message)
        match = pattern.search(text)
        if match is None:
            continue
        # 讓關鍵字落在片段的中間。
        room = max(_SNIPPET_LENGTH - len(match.group()), 0)
        start = max(match.start() - room // 2, 0)
        end = min(start + _SNIPPET_LENGTH, len(text))
        start = max(min(start, end - _SNIPPET_LENGTH), 0)
        snippet = " ".join(text[start:end].split())
        if start > 0:
            snippet = f"…{snippet}"
        if end < len(text):
            snippet = f"{snippet}…"
        return snippet
    return None


def _message_text(message: datatypes.Message) -> str:
    """訊息的文字內容，不含附件說明的那幾行。"""
    content = message.get("content")
    if isinstance(content, list):
        content = "\n".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    if not isinstance(content, str):
        return ""
    return "\n".join(
        line
        for line in content.split("\n")
        if not line.startswith(uploads_lib.MARKER)
    )


def _model_label(name: str) -> str:
    """顯示用的模型名稱：去掉開頭的供應商（anthropic/claude-… → claude-…）。"""
    return name.split("/", 1)[-1]


def _provider_label(name: str) -> str:
    """模型的供應商名稱，給使用者看的。"""
    if providers.uses_endpoint():
        return ENDPOINT_PROVIDER
    provider = providers.of_model(name)
    if provider is not None:
        return provider.label
    prefix, slash, _ = name.partition("/")
    return prefix if slash else "其他"


@functools.cache
def _vision(name: str) -> bool | None:
    """litellm 知道這個模型看不看得懂圖片時回傳答案，不知道時為 None。"""
    try:
        # litellm 載入很慢，延後到真的要用時才 import。
        import litellm

        litellm.suppress_debug_info = True
        info = litellm.get_model_info(model=name)
    # 不認識的模型名稱會丟出各種錯誤，一律當成不知道。
    except Exception:  # noqa: BLE001
        return None
    value = info.get("supports_vision")
    return value if isinstance(value, bool) else None


def _describe(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def _now() -> float:
    """目前時間（毫秒），和瀏覽器的 Date.now() 同單位。"""
    return time.time() * 1000
