"""網頁版的核心：管理一個專案裡所有對話的執行狀態。

每段對話可以同時各自執行。執行過程中的每一步都會變成事件推給訂閱者
（伺服器再用 SSE 轉給瀏覽器）；需要使用者同意的工具呼叫會停下來，等
瀏覽器回覆 resolve_approval。

送給瀏覽器的資料格式和 TypeScript 版完全相同（見根目錄的
src/web/protocol.ts），兩個版本共用同一份前端。所以這裡的 dict 鍵名
一律用 camelCase。

Python 版另外支援：對話改名、刪除與搜尋，每段對話自己選模型、工具、
skills 與工作資料夾，以及對回覆按讚或倒讚。這些資料存在對話紀錄旁邊的
<id>.meta.json，對話紀錄（JSONL）的格式不變；評價另外附加到
.harness/feedback.jsonl，方便管理者之後檢視。

多人使用時每段對話都有擁有者（見 accounts 模組）：對話紀錄存在
.harness/users/<識別碼>/sessions/，每個公開方法的第一個參數都是發出請求
的人，別人的對話一律當成不存在（WebError 404），事件也只送給對話的
擁有者。每一輪都在對話選的工作資料夾裡執行（見 folders 模組）：檔案
工具、執行指令與沙盒都只看得到那個資料夾；成員的 run_command 改在沙盒
裡執行，沒有沙盒時不提供。工作資料夾在對話開始後就固定，不能再換。

權限判斷每次都向帳號資料庫讀最新的角色（users），不用這一輪開始時的
快照；管理者停用成員或改變角色時，伺服器呼叫 stop_runs_of 停掉那個人
正在執行的對話。帳號模式下 web_fetch 不能讀內部網路（見
builtin.web.guarded_fetch_tool），管理者可以在 harness.yaml 用
web: {allowPrivateNetworks: true} 開放給自己。

本機模式（auth_mode 為 local）和加入帳號之前一樣：對話紀錄與記憶在
.harness/sessions/ 與 .harness/memory.json（和 CLI 共用），每一輪都在
專案根目錄執行，沙盒照 harness.yaml 掛載 sandbox.workspace。
"""

import asyncio
import contextlib
import dataclasses
import datetime
import functools
import hashlib
import json
import os
import pathlib
import re
import sys
import time
import uuid
from collections.abc import Callable
from typing import Any

from harness import agents as agents_lib
from harness import builtin
from harness import compaction as compaction_lib
from harness import credentials
from harness import datatypes
from harness import delegation
from harness import llm
from harness import loop
from harness import memory as memory_lib
from harness import model_catalog
from harness import permissions as permissions_lib
from harness import project as project_lib
from harness import providers
from harness import sandbox as sandbox_lib
from harness import sessions as sessions_lib
from harness import skills as skills_lib
from harness import tools as tools_lib
from harness.builtin import files
from harness.builtin import paths
from harness.builtin import shell
from harness.builtin import web as web_tools
from harness.web import accounts as accounts_lib
from harness.web import categories as categories_lib
from harness.web import config as config_lib
from harness.web import folders as folders_lib
from harness.web import migration
from harness.web import uploads as uploads_lib

Event = dict[str, Any]
Listener = Callable[[Event], None]

DECISIONS = ("once", "session", "always", "deny")
# 和 src/web/protocol.ts 的 INTERRUPTED_STOP_REASON 相同。
INTERRUPTED_STOP_REASON = "interrupted"
INTERRUPTED_NOTICE = "服務重新啟動，這一輪中斷了。按重試可以接著做。"
# 重試被中斷的一輪時，替沒有結果的工具呼叫補上的說明（給模型看的）。
_INTERRUPTED_RESULT = (
    "服務重新啟動，這次工具呼叫沒有執行，也沒有結果。還需要的話請再呼叫一次。"
)
# 從網頁執行時接在 agent 指示後面的說明：使用者只有瀏覽器，碰不到伺服器。
WEB_INSTRUCTIONS = "\n".join(
    (
        "## 網頁版說明",
        "- 使用者在瀏覽器裡和你對話，打不開伺服器上的檔案或資料夾，也不能在"
        "伺服器上執行指令。",
        "- 你只看得到這段對話的工作資料夾：檔案工具、執行指令與沙盒都在"
        "這個資料夾裡，路徑都相對於它。",
        "- 你寫入或產生的檔案會以檔案卡片出現在對話裡，使用者可以預覽與下載；"
        "提到檔案時用它相對於工作資料夾的路徑。",
        "- 不要叫使用者去某個資料夾找檔案、執行指令，或在自己的電腦上啟動"
        "伺服器。",
        "- 引用資料裡的數字時，只用你的程式實際印出來的值。",
    )
)
_UNTITLED = "新對話"
_TITLE_LENGTH = 40
_PREVIEW_LIMIT = 200_000
_MODEL_TEST_TIMEOUT = 20
_SESSION_ID = re.compile(r"^[\w-]+$")
# subagent 步驟的結果只是給人瀏覽的，太長的部分不保留。
_SUBAGENT_RESULT_LIMIT = 4000
_CUSTOM_TITLE_LIMIT = 100
# 批次刪除或移動時，一次最多幾段對話。
_BULK_LIMIT = 500
# 輸入框的模型選單最多列出連線上的幾個模型。
_CATALOG_LIMIT = 500
_SEARCH_LIMIT = 30
_RECENT_LIMIT = 20
_SNIPPET_LENGTH = 60
RATINGS = ("up", "down")
_COMMENT_LIMIT = 2000
FEEDBACK_LOG = project_lib.DATA_DIR / "feedback.jsonl"
ENDPOINT_PROVIDER = "OpenAI 相容端點"
# 成員「以後都允許」的工具，依工作資料夾分開記（見 resolve_approval）。
PERMISSIONS_FILE = "permissions.json"
AUTH_MODES = ("local", "accounts")
FOLDER_LOCKED = "資料夾只能在開始對話前選擇"
# 管理者停用成員或改變角色時，那個人正在執行的對話停下來的說明。
ACCOUNT_CHANGED_NOTICE = "管理者變更了你的帳號，這一輪已經停止。"
# 「舊資料」資料夾不能執行程式或指令：告訴模型為什麼沒有這些工具。
LEGACY_NOTE = (
    "## 舊資料資料夾\n"
    "- 這段對話在「舊資料（專案資料夾）」裡，為了安全只能讀檔案，不能"
    "寫入、修改，也不能執行程式或指令（沒有 run_python 與 run_command）。"
    "需要的話請使用者把檔案上傳到其他資料夾，開新的對話處理。"
)


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
    # 擁有這段對話的使用者識別碼。
    owner: str
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
    # 執行中各個 agent（套用過對話選項）用的模型，以及目前負責回覆的
    # agent，用來記下每則回覆是哪個模型寫的。
    run_models: dict[str, str] = dataclasses.field(default_factory=dict)
    replying: str | None = None
    # 這一輪是誰開始的、在哪個工作資料夾執行。
    run_user: accounts_lib.User | None = None
    run_folder: folders_lib.Folder | None = None
    # 以下存在 .meta.json，伺服器重新啟動後仍然保留。
    custom_title: str | None = None
    # 這段對話放在哪個專案（見 harness.web.categories）；None 表示未分類。
    category: str | None = None
    options: dict[str, Any] = dataclasses.field(
        default_factory=lambda: _default_options()
    )
    # options["tools"] 是依哪個 agent 的工具檢查的；只套用在這個 agent。
    tools_agent: str | None = None
    # 以 assistant 訊息的索引（字串）為鍵。
    feedback: dict[str, dict[str, Any]] = dataclasses.field(
        default_factory=dict
    )
    # 產生每則回覆的模型，以 assistant 訊息的索引（字串）為鍵。
    reply_models: dict[str, str] = dataclasses.field(default_factory=dict)
    # 使用者選了「這段對話都允許」的工具名稱。
    session_tools: set[str] = dataclasses.field(default_factory=set)
    # 使用者同意過的呼叫（工具名稱加參數的雜湊），一模一樣的呼叫不再問。
    approved_calls: set[str] = dataclasses.field(default_factory=set)
    # 被伺服器停下時（例如帳號被停用）顯示的說明。
    stop_notice: str | None = None


class Workspace:
    """一個專案的所有對話。

    Attributes:
        project: 載入完成的專案。
        folders: 工作資料夾，以及誰可以使用哪些資料夾。
        auth_mode: local（本機使用，不必登入）或 accounts（要登入）。
        max_upload_bytes: 上傳的單一檔案大小上限。
    """

    def __init__(
        self,
        project: project_lib.Project,
        *,
        model_fn: llm.ModelFn | None = None,
        max_upload_bytes: int = uploads_lib.DEFAULT_MAX_BYTES,
        auth_mode: str = "local",
        folders: folders_lib.Folders | None = None,
        users: Callable[[str], accounts_lib.User | None] | None = None,
    ) -> None:
        """建立 Workspace。

        Args:
            project: 載入完成的專案。
            model_fn: 模型函式，預設透過 litellm 呼叫。
            max_upload_bytes: 上傳的單一檔案大小上限。
            auth_mode: local 或 accounts。
            folders: 工作資料夾；預設依 harness.yaml 建立。
            users: 依識別碼查目前的帳號（帳號資料庫的 get_user）；權限
                判斷用它讀最新的角色。None 時沿用這一輪開始時的帳號。

        Raises:
            ValueError: harness.yaml 的 workspaces 寫錯。
        """
        if auth_mode not in AUTH_MODES:
            raise ValueError(f"auth_mode 必須是 {AUTH_MODES} 之一")
        self.project = project
        self.auth_mode = auth_mode
        self.folders = folders or folders_lib.Folders.for_project(
            project,
            local=auth_mode == "local",
            legacy_owner=migration.legacy_owner(project.root.resolve()),
        )
        self.max_upload_bytes = max_upload_bytes
        self._users = users
        self._model_fn = model_fn or llm.complete
        self._states: dict[str, _State] = {}
        # close() 停下的對話保留「執行中」的記錄，下次啟動時標成中斷。
        self._closing = False
        # 每個訂閱者只收到自己對話的事件，以使用者識別碼記下是誰。
        self._listeners: dict[Listener, str] = {}
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
        # 沒有寫 models 時，輸入框列出目前連線的供應商或端點的所有模型
        # （見 refresh_catalog）。
        self._catalog: list[dict[str, str]] = []
        self._catalog_task: asyncio.Task | None = None
        # 只有真的啟動伺服器時（serve）才向外查詢，測試不會連網路。
        self.auto_catalog = False
        self._builtin_skills = project_lib.builtin_skills()

    def subscribe(
        self, user: accounts_lib.User, listener: Listener
    ) -> Callable[[], None]:
        """訂閱事件：這個人自己對話的事件，以及整個專案的事件。

        Args:
            user: 訂閱的人。
            listener: 收到事件時呼叫的函式。

        Returns:
            取消訂閱的函式。
        """
        self._listeners[listener] = user.id
        return lambda: self._listeners.pop(listener, None)

    def folder(
        self, user: accounts_lib.User, folder_id: str | None
    ) -> folders_lib.Folder:
        """這個人可以使用的工作資料夾，第一次使用時建立。

        Args:
            user: 發出請求的人。
            folder_id: FolderInfo.id；None 或空字串表示「我的檔案」。

        Raises:
            WebError: 沒有這個資料夾，或這個人不能使用（404）。
        """
        found = self.folders.find(user, folder_id)
        if found is None:
            raise WebError(404, f"找不到工作資料夾：{folder_id}")
        folders_lib.ensure(found)
        return found

    def uploads_for(
        self, user: accounts_lib.User, folder_id: str | None
    ) -> uploads_lib.Uploads:
        """上傳到某個工作資料夾的 uploads/。

        Raises:
            WebError: 不能使用這個資料夾（404），或它是唯讀的（403）。
        """
        found = self.folder(user, folder_id)
        if not found.writable:
            raise WebError(403, f"「{found.name}」是唯讀的資料夾，不能上傳檔案")
        return self._uploads(found)

    def migrate(self, owner_id: str) -> list[str]:
        """把多人帳號之前的資料交給 owner_id（見 migration 模組）。

        只在帳號模式呼叫；本機模式的資料留在原處，和 CLI 共用。
        """
        moved = migration.migrate(self.project.root, owner_id)
        self.folders.legacy_owner = migration.legacy_owner(
            self.project.root.resolve()
        )
        return moved

    def stop_runs_of(self, user_id: str, notice: str) -> None:
        """停掉這個人所有正在執行的對話（帳號被停用或角色改變時）。

        Args:
            user_id: 使用者識別碼。
            notice: 顯示在對話裡的說明。
        """
        for state in self._states.values():
            if state.owner == user_id and state.task is not None:
                state.stop_notice = notice
                state.task.cancel()

    def refresh_project(self) -> None:
        """通知瀏覽器重新讀取專案資訊（例如 litellm 在背景載入完成後）。"""
        self._emit({"type": "project"})

    def refresh_catalog(self) -> None:
        """在背景向目前連線的供應商或端點查詢有哪些模型。

        harness.yaml 沒有寫 models 時，輸入框的模型選單列出這些模型；
        查到後通知瀏覽器重新讀取專案資訊。查不到時（金鑰沒設定、端點
        不支援）選單只有預設模型，和以前一樣。伺服器啟動時與換了連線
        設定後呼叫。
        """
        if not self.auto_catalog:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._catalog_task is not None and not self._catalog_task.done():
            self._catalog_task.cancel()
        self._catalog_task = loop.create_task(self._load_catalog())

    async def _load_catalog(self) -> None:
        provider = self._connection_info()["provider"]
        found: list[model_catalog.ModelChoice] = []
        if provider is not None:
            try:
                found = await model_catalog.list_models(provider)
            except model_catalog.CatalogError:
                found = []
        self._catalog = [item.to_json() for item in found[:_CATALOG_LIMIT]]
        self._emit({"type": "project"})

    def project_info(self, user: accounts_lib.User) -> dict[str, Any]:
        """專案資訊、agent、toolset、權限、模型設定與登入的人。

        成員看不到伺服器上的路徑與連線細節（環境變數名稱、端點網址、
        供應商清單），工具清單也只列出他實際能用的。
        """
        project = self.project
        hidden = self._hidden_tools(user)
        info = {
            "name": project.name,
            "root": str(project.root) if user.is_admin else "",
            "runtime": "python",
            "uploads": {"maxBytes": self.max_upload_bytes},
            "defaultAgent": project.default_agent,
            "agents": [
                self._agent_info(name, hidden) for name in project.agents
            ],
            "toolsets": [
                {
                    "name": name,
                    "builtin": builtin.is_builtin_toolset(name, items)
                    or name == sandbox_lib.TOOLSET,
                    "tools": [
                        _tool_info(item)
                        for item in items
                        if id(item) not in hidden
                    ],
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
                "sessionApproval": True,
                "accounts": True,
                "folders": self.auth_mode == "accounts",
                "categories": True,
            },
            "auth": {
                "mode": self.auth_mode,
                "user": accounts_lib.current_user(user),
            },
            "folders": [item.info() for item in self.folders.for_user(user)],
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
        if not user.is_admin:
            info["model"] = self._member_model_info(info["model"])
        return info

    def _member_model_info(self, model: dict[str, Any]) -> dict[str, Any]:
        """成員看到的模型設定：只留畫面需要的部分（連線是否就緒）。"""
        return {
            "config": model["config"],
            "env": {
                "model": None,
                "baseURL": None,
                "apiKeySet": False,
                "keyVars": [],
                "fallbackModel": model["env"]["fallbackModel"],
            },
            "connection": {
                "ready": model["connection"]["ready"],
                "provider": None,
                "providers": [],
            },
        }

    def list_sessions(self, user: accounts_lib.User) -> list[dict[str, Any]]:
        """這個人所有對話的摘要，最近有動靜的排前面。"""
        return [self._summary(state) for state in self._own_states(user)]

    def _own_states(self, user: accounts_lib.User) -> list[_State]:
        """這個人所有的對話，最近有動靜的排前面。"""
        for session_id in sessions_lib.list_ids(self._sessions_dir(user.id)):
            self._find(user.id, session_id)
        own = [s for s in self._states.values() if s.owner == user.id]
        return sorted(own, key=lambda state: -state.updated_at)

    def get_session(
        self, user: accounts_lib.User, session_id: str
    ) -> dict[str, Any]:
        """一段對話的完整內容，含選項、使用者對回覆的評價與回覆的模型。

        Raises:
            WebError: 找不到這段對話（或它是別人的）。
        """
        state = self._require(user, session_id)
        return {
            "summary": self._summary(state),
            "messages": state.session.messages,
            "partial": state.partial,
            "approvals": [item.approval for item in state.approvals.values()],
            "mode": state.mode,
            "subagentSteps": state.subagent_steps,
            "options": self._visible_options(state),
            "feedback": state.feedback,
            "replyModels": state.reply_models,
        }

    def start_run(
        self,
        user: accounts_lib.User,
        text: str,
        session_id: str | None = None,
        agent: str | None = None,
        attachments: list[str] | None = None,
        options: dict[str, Any] | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        """送出一則訊息並在背景開始執行。沒給 session_id 時建立新的對話。

        Args:
            user: 發出請求的人，新對話的擁有者。
            text: 使用者的訊息。
            session_id: 要接續的對話；None 表示建立新的對話。
            agent: 新對話使用的 agent。
            attachments: 上傳時回傳的檔案路徑（相對於對話的工作資料夾），
                說明會接在訊息後面。
            options: 新對話的選項（見 set_options），只需要給要改的欄位。
            category: 新對話放進哪個專案（見 list_categories）。

        Raises:
            WebError: 訊息與附件都是空的、附件不是上傳的檔案、agent 不
                存在、選項不合法、專案不存在、工作資料夾不能使用，或這段
                對話還在執行。
        """
        text = text.strip()
        state = None
        if session_id:
            state = self._require(user, session_id)
            chosen = state.options
        else:
            agent = agent or self.project.default_agent
            if agent not in self.project.agents:
                raise WebError(400, f"沒有 {agent} 這個 agent")
            # 先檢查選項，不合法時不會留下空的對話。
            chosen = self._checked_options(
                user, options or {}, _default_options(), agent
            )
            if category is not None:
                self._require_category(user, category)
        folder = self._run_folder(user, chosen["folder"])
        try:
            notes = self._uploads(folder).describe(attachments or [])
        except uploads_lib.UploadError as error:
            raise WebError(error.status, str(error)) from error
        if not text and not notes:
            raise WebError(400, "訊息不可以是空的")
        if state is None:
            state = self._create(user, agent)
            state.category = category
            if chosen != state.options or category is not None:
                state.options = chosen
                if chosen["tools"] is not None:
                    state.tools_agent = agent
                self._save_meta(state)
        self._assert_idle(state)
        if state.stop_reason == INTERRUPTED_STOP_REASON:
            loop.close_dangling_tool_calls(state.session, _INTERRUPTED_RESULT)
        if state.title == _UNTITLED:
            first = pathlib.PurePosixPath(attachments[0]).name if notes else ""
            state.title = _title(text or first)
        message = "\n\n".join(part for part in (text, notes) if part)
        self._start(state, message, user, folder)
        return self._summary(state)

    def _run_folder(
        self, user: accounts_lib.User, folder_id: str | None
    ) -> folders_lib.Folder:
        """對話這一輪要用的工作資料夾。

        Raises:
            WebError: 這個人已經不能使用這個資料夾（400）。
        """
        found = self.folders.find(user, folder_id)
        if found is None:
            raise WebError(
                400, "這段對話的工作資料夾已經不能使用，請開始新的對話"
            )
        folders_lib.ensure(found)
        return found

    def _uploads(self, folder: folders_lib.Folder) -> uploads_lib.Uploads:
        """工作資料夾的上傳資料夾。

        專案根目錄的資料夾（本機模式、「舊資料」）照舊版存在
        workspace/uploads/（有沙盒時是 sandbox.workspace/uploads/），舊
        對話裡的附件路徑不變。
        """
        box = self.project.sandbox
        if not folder.project_root:
            return uploads_lib.Uploads.for_folder(
                folder.path,
                folder.path if box is not None else None,
                self.max_upload_bytes,
            )
        root = self.project.root.resolve()
        workspace = (
            box.workspace
            if box is not None
            else root / sandbox_lib.DEFAULT_WORKSPACE
        )
        subdir = workspace.relative_to(root) / uploads_lib.UPLOADS_DIR
        # 「舊資料」不能執行程式，附件不必說明容器裡的路徑。
        mounted = box.workspace if box and not folder.is_legacy else None
        return uploads_lib.Uploads.for_folder(
            folder.path, mounted, self.max_upload_bytes, subdir.as_posix()
        )

    def retry(self, user: accounts_lib.User, session_id: str) -> dict[str, Any]:
        """不加新訊息，從對話紀錄的最後一則接著跑。

        用在上一輪出錯，或伺服器重新啟動讓上一輪中斷之後。中斷時還沒有
        結果的工具呼叫會先補上「沒有執行」的結果（不是拒絕），模型會
        自己決定要不要再呼叫一次。

        Raises:
            WebError: 對話還在執行，或最後一則已經是 agent 的回覆。
        """
        state = self._require(user, session_id)
        self._assert_idle(state)
        if not _resumable(state.session.messages):
            raise WebError(400, "這段對話沒有需要重試的內容")
        folder = self._run_folder(user, state.options["folder"])
        if state.stop_reason == INTERRUPTED_STOP_REASON:
            loop.close_dangling_tool_calls(state.session, _INTERRUPTED_RESULT)
        self._start(state, None, user, folder)
        return self._summary(state)

    def rename(
        self, user: accounts_lib.User, session_id: str, title: str
    ) -> dict[str, Any]:
        """幫對話改名；名稱會存檔，伺服器重新啟動後仍然保留。

        Args:
            user: 發出請求的人。
            session_id: 要改名的對話。
            title: 新的名稱，前後的空白會去掉。

        Returns:
            改名後的摘要。

        Raises:
            WebError: 找不到對話，或名稱是空的、超過 100 個字。
        """
        state = self._require(user, session_id)
        title = title.strip()
        if not title or len(title) > _CUSTOM_TITLE_LIMIT:
            raise WebError(400, f"名稱要有 1 到 {_CUSTOM_TITLE_LIMIT} 個字")
        state.custom_title = title
        self._save_meta(state)
        summary = self._summary(state)
        self._send(state, {"type": "session", "summary": summary})
        return summary

    def delete(self, user: accounts_lib.User, session_id: str) -> None:
        """刪除對話的紀錄與設定，並通知瀏覽器（session_deleted）。

        Raises:
            WebError: 找不到對話，或它還在執行、等使用者確認。
        """
        state = self._require(user, session_id)
        if state.task is not None or state.status in ("running", "waiting"):
            raise WebError(409, "這段對話還在執行，請先停止再刪除")
        state.session.path.unlink(missing_ok=True)
        _meta_path(state.session.path.parent, session_id).unlink(
            missing_ok=True
        )
        del self._states[session_id]
        self._send(state, {"type": "session_deleted", "sessionId": session_id})

    # ── 專案（對話的分類）─────────────────────────────────

    def list_categories(self, user: accounts_lib.User) -> list[dict[str, Any]]:
        """這個人的專案，附上每個專案有幾段對話。"""
        counts: dict[str, int] = {}
        for state in self._own_states(user):
            if state.category is not None:
                counts[state.category] = counts.get(state.category, 0) + 1
        return [
            {**item.to_json(), "sessionCount": counts.get(item.id, 0)}
            for item in self._categories(user).all()
        ]

    def create_category(
        self, user: accounts_lib.User, name: str
    ) -> dict[str, Any]:
        """新增專案。

        Raises:
            WebError: 名稱是空的、太長、和別的專案重複，或專案太多。
        """
        with _category_errors():
            category = self._categories(user).create(name)
        self._emit({"type": "categories"}, user.id)
        return {**category.to_json(), "sessionCount": 0}

    def rename_category(
        self, user: accounts_lib.User, category_id: str, name: str
    ) -> dict[str, Any]:
        """幫專案改名。

        Raises:
            WebError: 找不到專案，或名稱不合法、重複。
        """
        with _category_errors():
            category = self._categories(user).rename(category_id, name)
        self._emit({"type": "categories"}, user.id)
        count = sum(
            1 for s in self._own_states(user) if s.category == category_id
        )
        return {**category.to_json(), "sessionCount": count}

    def delete_category(
        self, user: accounts_lib.User, category_id: str
    ) -> None:
        """刪除專案；裡面的對話不刪，改成未分類。

        Raises:
            WebError: 找不到專案。
        """
        with _category_errors():
            self._categories(user).delete(category_id)
        for state in self._own_states(user):
            if state.category == category_id:
                self._set_category(state, None)
        self._emit({"type": "categories"}, user.id)

    def update_session(
        self,
        user: accounts_lib.User,
        session_id: str,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        """改對話的名稱（title）或專案（category，null 表示移出專案）。

        Raises:
            WebError: 找不到對話、沒有要改的欄位、名稱不合法，或專案不存在。
        """
        state = self._require(user, session_id)
        if "title" not in changes and "category" not in changes:
            raise WebError(400, "要提供 title 或 category")
        if "category" in changes:
            category = changes["category"]
            if category is not None:
                if not isinstance(category, str):
                    raise WebError(400, "category 必須是字串或 null")
                self._require_category(user, category)
        if "title" in changes:
            title = changes["title"]
            if not isinstance(title, str):
                raise WebError(400, "title 必須是字串")
            summary = self.rename(user, session_id, title)
        if "category" in changes:
            summary = self._set_category(state, changes["category"])
        return summary

    def bulk(
        self,
        user: accounts_lib.User,
        action: str,
        session_ids: list[str],
        category: str | None = None,
    ) -> dict[str, Any]:
        """一次刪除或移動好幾段對話。

        還在執行的對話不能刪除，找不到的對話（或別人的）略過；都會列在
        skipped 裡說明原因，其他的照常處理。

        Args:
            user: 發出請求的人。
            action: delete（刪除）或 move（移到 category 這個專案）。
            session_ids: 要處理的對話。
            category: action 是 move 時的目標專案；None 表示移出專案。

        Returns:
            {"done": [處理好的對話], "skipped": [{"id", "reason"}]}

        Raises:
            WebError: action 不對、沒有給對話、一次太多，或專案不存在。
        """
        if action not in ("delete", "move"):
            raise WebError(400, "action 必須是 delete 或 move")
        if not session_ids:
            raise WebError(400, "沒有選擇對話")
        if len(session_ids) > _BULK_LIMIT:
            raise WebError(400, f"一次最多處理 {_BULK_LIMIT} 段對話")
        if action == "move" and category is not None:
            self._require_category(user, category)
        done: list[str] = []
        skipped: list[dict[str, str]] = []
        for session_id in dict.fromkeys(session_ids):
            try:
                if action == "delete":
                    self.delete(user, session_id)
                else:
                    state = self._require(user, session_id)
                    self._set_category(state, category)
            except WebError as error:
                skipped.append({"id": session_id, "reason": str(error)})
                continue
            done.append(session_id)
        if done:
            self._emit({"type": "categories"}, user.id)
        return {"done": done, "skipped": skipped}

    def _categories(
        self, user: accounts_lib.User
    ) -> categories_lib.CategoryStore:
        # 和對話紀錄放在同一個人的資料夾：本機模式是 .harness/。
        return categories_lib.CategoryStore(self._sessions_dir(user.id).parent)

    def _require_category(self, user: accounts_lib.User, category: str) -> None:
        if self._categories(user).get(category) is None:
            raise WebError(404, "找不到這個專案")

    def _set_category(
        self, state: _State, category: str | None
    ) -> dict[str, Any]:
        state.category = category
        self._save_meta(state)
        summary = self._summary(state)
        self._send(state, {"type": "session", "summary": summary})
        return summary

    def search(
        self, user: accounts_lib.User, query: str
    ) -> list[dict[str, Any]]:
        """依標題與對話內容搜尋對話，最近有動靜的排前面。

        不分大小寫，比對標題以及使用者與 agent 的訊息（不含工具結果與
        附件說明）。

        Args:
            user: 發出請求的人；只搜尋他自己的對話。
            query: 要找的文字；空的時回傳最近的 20 段對話。

        Returns:
            SearchResult（見 src/web/protocol.ts）的列表，最多 30 筆。
            命中訊息內容時附上含關鍵字的一小段文字。
        """
        states = self._own_states(user)
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
        self, user: accounts_lib.User, session_id: str, changes: dict[str, Any]
    ) -> dict[str, Any]:
        """設定對話自己的模型、工具、skills 與工作資料夾，下一輪開始生效。

        Args:
            user: 發出請求的人。
            session_id: 要設定的對話。
            changes: 要改的欄位：model（ProjectInfo.models 裡的名稱，null
                表示跟著 agent）、tools（agent 的工具名稱，null 表示全部）、
                skills（一定要用的 skill 名稱）、folder（FolderInfo.id，
                null 表示「我的檔案」）。沒給的欄位不變。

        Returns:
            設定後完整的選項。

        Raises:
            WebError: 找不到對話、選項不合法，或對話開始後要換工作資料夾
                （資料夾只能在開始對話前選擇：之前的檔案與附件路徑都相對
                於它）。
        """
        state = self._require(user, session_id)
        if (
            isinstance(changes, dict)
            and "folder" in changes
            and state.session.messages
            and (changes["folder"] or folders_lib.PRIVATE_ID)
            != state.options["folder"]
        ):
            raise WebError(400, FOLDER_LOCKED)
        agent = self._active_agent(state).name
        state.options = self._checked_options(
            user, changes, state.options, agent
        )
        if "tools" in changes:
            # 工具清單只對檢查時負責的 agent 有意義（見 _with_options）。
            tools = state.options["tools"]
            state.tools_agent = agent if tools is not None else None
        self._save_meta(state)
        self._send(state, {"type": "session", "summary": self._summary(state)})
        return self._visible_options(state)

    def _visible_options(self, state: _State) -> dict[str, Any]:
        """給網頁看的選項：工具清單只在設定它的 agent 負責對話時才回傳。

        轉交給其他 agent 之後，那個 agent 用的是自己全部的工具（見
        _with_options），所以回傳 None，畫面才不會顯示不存在的限制。
        """
        options = dict(state.options)
        if state.tools_agent != self._active_agent(state).name:
            options["tools"] = None
        return options

    def set_feedback(
        self,
        user: accounts_lib.User,
        session_id: str,
        index: Any,
        feedback: Any,
    ) -> None:
        """記下使用者對一則回覆的評價；None 表示取消。

        評價存在對話的 .meta.json；每次送出的評價另外附加一行到
        .harness/feedback.jsonl（含評價的人），給管理者之後檢視。

        Args:
            user: 發出請求的人。
            session_id: 對話。
            index: 這則 assistant 訊息在 messages 裡的索引。
            feedback: {"rating": "up" | "down", "reasons": [...],
                "comment": "..."}，或 None。

        Raises:
            WebError: 找不到對話、索引不是 assistant 訊息，或評價不合法。
        """
        state = self._require(user, session_id)
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
            "userId": user.id,
            "index": index,
            "rating": cleaned["rating"],
            "reasons": cleaned.get("reasons", []),
            "comment": cleaned.get("comment", ""),
        }
        # 只記實際寫出這則回覆的模型；這個功能加入前的回覆不知道，就不記。
        model = state.reply_models.get(key)
        if model is not None:
            record["model"] = model
        log = self.project.root / FEEDBACK_LOG
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    def stop(self, user: accounts_lib.User, session_id: str) -> None:
        """停止執行；等待中的確認一律視為拒絕。"""
        state = self._require(user, session_id)
        if state.task is not None:
            state.task.cancel()

    def set_mode(
        self, user: accounts_lib.User, session_id: str, mode: str | None
    ) -> None:
        """設定這段對話自己的權限模式；None 表示跟著專案設定。

        Raises:
            WebError: 找不到對話、模式不存在，或成員要用 bypass。
        """
        state = self._require(user, session_id)
        if mode is not None and mode not in permissions_lib.MODES:
            raise WebError(400, f"沒有 {mode} 這個權限模式")
        if mode == "bypass" and not user.is_admin:
            raise WebError(400, "只有管理者可以讓工具不經確認直接執行")
        state.mode = mode

    def resolve_approval(
        self,
        user: accounts_lib.User,
        session_id: str,
        approval_id: str,
        decision: str,
    ) -> None:
        """回覆一個等待中的確認。

        「以後都允許」：管理者寫進 harness.yaml（所有人都適用）；成員只
        記在自己的資料裡，只適用於他在同一個工作資料夾裡的對話。

        Raises:
            WebError: 決定不合法，或找不到這個等待中的確認。
        """
        state = self._require(user, session_id)
        if decision not in DECISIONS:
            raise WebError(400, f"decision 必須是 {'、'.join(DECISIONS)}")
        pending = state.approvals.get(approval_id)
        if pending is None or pending.future.done():
            raise WebError(404, "這個確認已經處理過，或已經失效")
        approval = pending.approval
        if decision == "always" and user.is_admin:
            self._allow_always(approval["tool"])
        elif decision == "always":
            folder = state.run_folder
            folder_id = folder.id if folder else state.options["folder"]
            self._allow_for_member(user, folder_id, approval["tool"])
        elif decision == "session":
            # 只記在這段對話的 .meta.json，不寫進 harness.yaml。
            state.session_tools.add(approval["tool"])
        if decision != "deny":
            state.approved_calls.add(
                _call_key(approval["tool"], approval["arguments"])
            )
            self._save_meta(state)
        self._send(
            state,
            {
                "type": "approval_resolved",
                "sessionId": session_id,
                "approvalId": approval_id,
                "decision": decision,
            },
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
        result, error = await self._try_model(own.get("model") or model)
        if not result["ok"]:
            _set_environment(before)
            raise WebError(400, _explain_failure(result["message"], error))
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
        self.refresh_catalog()

    async def test_model(self, model: str | None = None) -> dict[str, Any]:
        """送一個很短的請求，確認連得上模型。

        Args:
            model: 要測試的模型，預設是預設 agent 目前使用的模型。
        """
        result, _ = await self._try_model(model)
        return result

    async def _try_model(
        self, model: str | None = None
    ) -> tuple[dict[str, Any], BaseException | None]:
        """test_model 的本體，另外回傳失敗時的例外（逾時為 TimeoutError）。"""
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
        except TimeoutError as error:
            message = f"{_MODEL_TEST_TIMEOUT} 秒內沒有回應"
            return {"ok": False, "model": model, "message": message}, error
        # 錯誤種類取決於模型供應商，全部轉成訊息顯示在設定頁。
        except Exception as error:  # noqa: BLE001
            message = _describe(error)
            return {"ok": False, "model": model, "message": message}, error
        return {"ok": True, "model": model}, None

    async def close(self) -> None:
        """停止所有正在執行的對話，並等它們收尾。

        這些對話的 .meta.json 會留著「執行中」的記錄，下次啟動時顯示成
        被中斷，可以按重試接著做（見 _mark_interrupted）。
        """
        self._closing = True
        if self._catalog_task is not None:
            self._catalog_task.cancel()
        tasks = [s.task for s in self._states.values() if s.task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _start(
        self,
        state: _State,
        text: str | None,
        user: accounts_lib.User,
        folder: folders_lib.Folder,
    ) -> None:
        """設定好執行中的狀態，再把執行放進背景。"""
        state.run_user = user
        state.run_folder = folder
        state.stop_notice = None
        state.status = "running"
        state.activity = {"kind": "thinking"}
        state.started_at = _now()
        state.ended_at = None
        state.error = None
        state.notice = None
        state.tool_errors = 0
        state.stop_reason = None
        state.partial = ""
        # 記下「執行中」，伺服器在這一輪結束前停掉時，下次啟動才知道。
        self._save_meta(state)
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
        user = state.run_user or accounts_lib.LOCAL_USER
        folder = state.run_folder
        assert folder is not None
        try:
            agent = self._run_agent(state)
            state.run_models = {
                item.name: item.model or llm.default_model()
                for item in delegation.reachable_agents(agent)
            }
            state.replying = agent.name
            events = loop.run_stream(
                agent,
                text,
                session=state.session,
                permissions=self._permissions_for(state),
                workdir=folder.path,
                hooks=project.hooks,
                memory=self._memory_for(user),
                compaction=project.compaction,
                model_fn=self._model_fn,
                hidden=folder.hidden,
                hide_dotfiles=folder.hide_dotfiles,
            )
            async for event in events:
                # 帳號在執行中被停用（例如用 pyharness user disable）：
                # 不再執行任何工具，直接停下來。
                if event.type == "tool_call" and not self._live_active(user):
                    state.stop_notice = ACCOUNT_CHANGED_NOTICE
                    raise asyncio.CancelledError
                self._handle(state, event)
        except asyncio.CancelledError:
            state.status = "stopped"
            if state.stop_notice is not None:
                state.notice = state.stop_notice
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
            if not self._closing:
                self._save_meta(state)
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
            self._record_reply_model(state, data["message"])
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
            state.replying = data["to"]
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

    def _record_reply_model(
        self, state: _State, message: datatypes.Message
    ) -> None:
        """記下這則 assistant 訊息是哪個模型寫的，並存進 .meta.json。"""
        messages = state.session.messages
        model = state.run_models.get(state.replying or "")
        if message.get("role") != "assistant" or model is None:
            return
        # 主迴圈先把訊息存進紀錄才產生事件，所以最後一則就是它。
        if not messages or messages[-1] is not message:
            return
        state.reply_models[str(len(messages) - 1)] = model
        self._save_meta(state)

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
        規則，執行中的對話也會立刻套用。是不是管理者也每次向帳號資料庫
        確認（被降級的管理者不能再用 bypass）。成員另外加上自己在這個
        工作資料夾「以後都允許」的工具。
        """
        user = state.run_user or accounts_lib.LOCAL_USER
        folder = state.run_folder

        def is_admin() -> bool:
            return self._live_admin(user)

        def extra_allow() -> list[str]:
            if folder is None or is_admin():
                return []
            return self._member_allow(user.id).get(folder.id, [])

        return _LivePermissions(
            self.project.permissions,
            state,
            self._approver(state),
            extra_allow,
            is_admin,
            lambda: self._live_active(user),
        )

    def _live_active(self, user: accounts_lib.User) -> bool:
        """這個帳號現在還能用嗎（沒有被停用或刪除，包括從指令列改的）。"""
        if self._users is None:
            return True
        current = self._users(user.id)
        return current is not None and not current.disabled

    def _live_admin(self, user: accounts_lib.User) -> bool:
        """這個人現在是不是管理者（停用或刪除的帳號都不是）。"""
        if self._users is None:
            return user.is_admin
        current = self._users(user.id)
        return current is not None and current.is_admin and not current.disabled

    def _memory_for(
        self, user: accounts_lib.User
    ) -> memory_lib.MemoryStore | None:
        """這個人自己的長期記憶；專案沒有開啟記憶時為 None。"""
        if self.project.memory is None:
            return None
        if self.auth_mode == "local":
            # 本機模式和 CLI 共用 .harness/memory.json。
            return self.project.memory
        directory = migration.user_dir(self.project.root, user.id)
        return memory_lib.MemoryStore(directory / "memory.json")

    def _member_allow(self, user_id: str) -> dict[str, list[str]]:
        """成員「以後都允許」的工具，以工作資料夾的識別碼為鍵。"""
        path = migration.user_dir(self.project.root, user_id)
        try:
            data = json.loads((path / PERMISSIONS_FILE).read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        allow = data.get("allow") if isinstance(data, dict) else None
        if not isinstance(allow, dict):
            return {}
        return {
            key: value for key, value in allow.items() if _is_string_list(value)
        }

    def _allow_for_member(
        self, user: accounts_lib.User, folder_id: str, tool_name: str
    ) -> None:
        """記下成員在這個工作資料夾「以後都允許」的工具。"""
        allow = self._member_allow(user.id)
        current = allow.get(folder_id, [])
        if tool_name in current:
            return
        allow[folder_id] = [*current, tool_name]
        path = migration.user_dir(self.project.root, user.id) / PERMISSIONS_FILE
        _write_json(path, {"allow": allow})

    def _approver(self, state: _State) -> permissions_lib.Approver:
        async def approve(
            call: datatypes.ToolCall, target: tools_lib.Tool
        ) -> bool:
            # 這段對話已經允許這個工具，或同意過一模一樣的呼叫：不再問。
            key = _call_key(target.name, call.arguments)
            if (
                target.name in state.session_tools
                or key in state.approved_calls
            ):
                return True
            future = asyncio.get_running_loop().create_future()
            approval = {
                "id": uuid.uuid4().hex,
                "sessionId": state.session.id,
                "callId": call.id,
                "tool": target.name,
                "arguments": call.arguments,
            }
            preview = self._preview(state, call)
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

    def _preview(
        self, state: _State, call: datatypes.ToolCall
    ) -> dict[str, Any] | None:
        """寫檔類的呼叫附上檔案修改前後的內容。

        參數有 path 與 content 的（write_file）直接用 content；有 path、
        old_text 與 new_text 的（edit_file）照 edit_file 的規則算出修改
        後的內容，改不了時（找不到原文或不只一處）不附，交給工具自己
        回報錯誤。
        """
        arguments = call.arguments
        path = arguments.get("path")
        if not isinstance(path, str):
            return None
        folder = state.run_folder
        if folder is None:
            context = datatypes.RunContext("", self.project.root, "")
        else:
            context = datatypes.RunContext(
                "",
                folder.path,
                "",
                hidden=folder.hidden,
                hide_dotfiles=folder.hide_dotfiles,
            )
        content = arguments.get("content")
        if isinstance(content, str):
            try:
                paths.relative_parts(context, path)
            except ValueError:
                return None
            try:
                text = paths.read_text(context, path, errors="replace")
                before = text[:_PREVIEW_LIMIT]
            except FileNotFoundError:
                before = None
            except (OSError, ValueError):
                return None
            return {"path": path, "before": before, "after": content}
        return _edit_preview(context, path, arguments)

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

    async def discover_models(self, settings: dict[str, Any]) -> dict[str, Any]:
        """向供應商或端點查詢可以用的模型（見 harness.model_catalog）。

        Args:
            settings: {"provider", "baseURL", "apiKey"}，和連線設定相同；
                留空表示用目前存好的值。換成別的端點網址時不會送出存好的
                金鑰。

        Returns:
            {"models": [{"name", "label"}]}

        Raises:
            WebError: 設定不完整，或查不到模型清單。
        """
        provider_id = _clean(settings.get("provider")) or providers.ENDPOINT
        base_url = _clean(settings.get("baseURL"))
        if base_url:
            base_url = _checked_url(base_url)
        saved = (os.environ.get(providers.BASE_URL_VAR) or "").rstrip("/")
        same_endpoint = not base_url or base_url.rstrip("/") == saved
        try:
            found = await model_catalog.list_models(
                provider_id,
                base_url,
                _clean(settings.get("apiKey")),
                saved_key=same_endpoint,
            )
        except model_catalog.CatalogError as error:
            raise WebError(400, str(error)) from error
        return {"models": [item.to_json() for item in found]}

    def update_model_choices(self, names: list[str]) -> None:
        """設定對話中可以切換的模型（寫進 harness.yaml 的 models）。

        原本有寫顯示名稱（label）的模型保留它的名稱。

        Raises:
            WebError: 名稱不合法，或 harness.yaml 無法安全地修改。
        """
        cleaned = list(dict.fromkeys(name.strip() for name in names))
        if any(not name or len(name) > 200 for name in cleaned):
            raise WebError(400, "模型名稱不可以是空的，也不能超過 200 個字")
        if len(cleaned) > 100:
            raise WebError(400, "最多開放 100 個模型")
        labels = {
            item["name"]: item["label"]
            for item in self._model_choices
            if item.get("label")
        }
        choices = [
            {
                "name": name,
                **({"label": labels[name]} if name in labels else {}),
            }
            for name in cleaned
        ]
        try:
            config_lib.save_model_choices(self.project.root, choices)
        except ValueError as error:
            raise WebError(400, str(error)) from error
        self._model_choices = choices
        self._emit({"type": "project"})

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
            "canDiscover": True,
            "choices": [item["name"] for item in self._model_choices],
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

    def _hidden_tools(self, user: accounts_lib.User) -> set[int]:
        """用不到的工具（以 id() 表示）：沒有沙盒時成員不能用 run_command。"""
        if user.is_admin or self.project.sandbox is not None:
            return set()
        return {id(shell.run_command)}

    def _agent_info(
        self, name: str, hidden: set[int] | None = None
    ) -> dict[str, Any]:
        agent = self.project.agents[name]
        hidden = hidden or set()
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
            "tools": [
                item.name for item in agent.tools if id(item) not in hidden
            ],
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
        # 管理者勾選了要開放的模型時只列那些，否則列出連線上所有的模型。
        choices += self._model_choices or self._catalog
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
        self,
        user: accounts_lib.User,
        changes: Any,
        current: dict[str, Any],
        agent_name: str,
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
                hidden = self._hidden_tools(user)
                own = {
                    item.name for item in agent.tools if id(item) not in hidden
                }
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
        if "folder" in changes:
            folder_id = changes["folder"] or folders_lib.PRIVATE_ID
            if self.folders.find(user, folder_id) is None:
                raise WebError(400, f"沒有 {folder_id} 這個工作資料夾")
            options["folder"] = folder_id
        return options

    def _active_agent(self, state: _State):
        """目前負責這段對話的 agent（可能已經轉交給別的 agent）。"""
        entry = self.project.agents[state.agent]
        return delegation.active_agent(entry, state.session.messages)

    def _run_agent(self, state: _State) -> agents_lib.Agent:
        """這一輪要用的 agent：套用這段對話自己選的模型、工具與 skills。

        改的是副本，專案共用的 agent 不受影響。轉交（handoff）的對象也
        換成套用過選項的副本，執行到一半轉交時仍然用這段對話選的模型與
        skills。
        """
        agent = self._with_options(
            state, self._active_agent(state), self._chosen_model(state), {}
        )
        if self.auth_mode == "local":
            # 和加入帳號之前一樣：專案根目錄、專案自己的沙盒設定。
            return agent
        user = state.run_user or accounts_lib.LOCAL_USER
        assert state.run_folder is not None
        return self._scoped_agent(agent, user, state.run_folder)

    def _scoped_agent(
        self,
        agent: agents_lib.Agent,
        user: accounts_lib.User,
        folder: folders_lib.Folder,
    ) -> agents_lib.Agent:
        """把 agent（含 skills、subagents 與轉交對象）的工具換成這一輪的版本。

        - run_python：只掛載這段對話的工作資料夾（唯讀的資料夾唯讀掛載）。
        - run_command：成員改在沙盒裡執行；沒有沙盒時拿掉，指令絕不在
          伺服器上替成員執行。管理者維持原樣（在伺服器上、工作資料夾裡
          執行）。
        - write_file、edit_file：唯讀的資料夾一律拒絕。
        - 「舊資料」資料夾（專案根目錄）：拿掉 run_python 與 run_command，
          並在指示裡說明原因。
        - web_fetch：不能讀內部網路（見 _blocks_private_networks）。
        """
        project = self.project
        box_tools = project.toolsets.get(sandbox_lib.TOOLSET, [])
        box = None
        box_error = "這個專案沒有設定沙盒"
        legacy = folder.is_legacy
        fetch = None
        if self._blocks_private_networks(user):
            fetch = web_tools.guarded_fetch_tool()
        if project.sandbox is not None and not legacy:
            try:
                box = project.sandbox.for_folder(
                    project.root, folder.path, read_only=not folder.writable
                )
            except ValueError as error:
                box_error = str(error)
        made: dict[str, tools_lib.Tool] = {}
        # 指令在沙盒裡執行：成員一律如此；管理者在共用資料夾也是，因為
        # 成員可以在那裡放檔案（例如 .git/config、同名的 Python 模組），
        # 讓管理者看似無害的指令在伺服器上執行成員寫的程式。
        sandboxed = not user.is_admin or (
            self.auth_mode != "local" and folder.kind != "private"
        )

        def convert(item: tools_lib.Tool) -> tools_lib.Tool | None:
            if legacy and (
                item is shell.run_command
                or any(item is known for known in box_tools)
            ):
                return None
            if fetch is not None and item is web_tools.web_fetch:
                return fetch
            if any(item is known for known in box_tools):
                if box is None:
                    return _refusing(item, box_error)
                if "python" not in made:
                    made["python"] = box.tools()[0]
                return made["python"]
            if item is shell.run_command and sandboxed:
                if project.sandbox is None:
                    return None
                if box is None:
                    return _refusing(item, box_error)
                if "command" not in made:
                    made["command"] = box.command_tool()
                return made["command"]
            writes = (files.write_file, files.edit_file)
            if not folder.writable and any(item is w for w in writes):
                return _refusing(
                    item,
                    f"「{folder.name}」是唯讀的資料夾，不能寫入或修改檔案。"
                    "請把結果直接回覆給使用者，或請他換一個可以寫入的資料夾。",
                )
            return item

        scoped = _map_tools(agent, convert, {})
        if legacy:
            scoped.instructions = "\n\n".join(
                part for part in (scoped.instructions, LEGACY_NOTE) if part
            )
        return scoped

    def _blocks_private_networks(self, user: accounts_lib.User) -> bool:
        """web_fetch 要不要擋內部網路：帳號模式一律擋，除非管理者在
        harness.yaml 寫了 web: {allowPrivateNetworks: true}（只開放給
        管理者自己的對話）。"""
        if self.auth_mode == "local":
            return False
        if not self._live_admin(user):
            return True
        web = config_lib.read_config(self.project.root).get("web")
        allowed = isinstance(web, dict) and web.get("allowPrivateNetworks")
        return allowed is not True

    def _with_options(
        self,
        state: _State,
        agent: agents_lib.Agent,
        model: str | None,
        applied: dict[str, agents_lib.Agent],
    ) -> agents_lib.Agent:
        """agent 套用對話選項後的副本，轉交對象也一併換成副本。

        Args:
            state: 對話狀態。
            agent: 專案裡的 agent。
            model: 這段對話選的模型；None 表示用 agent 自己的。
            applied: 已經建立的副本，以 agent 名稱為鍵；agent 之間互相
                轉交形成循環時，靠它避免無限遞迴。

        Returns:
            套用過選項的副本。
        """
        if agent.name in applied:
            return applied[agent.name]
        copy = dataclasses.replace(agent)
        applied[agent.name] = copy
        options = state.options
        if model is not None:
            copy.model = model
        # 工具清單是依某個 agent 的工具檢查的，只套用在那個 agent；轉交
        # 給其他 agent 後用它自己的工具。
        allowed: set[str] | None = None
        if options["tools"] is not None and agent.name == state.tools_agent:
            allowed = set(options["tools"])
            copy.tools = [t for t in agent.tools if t.name in allowed]
            # skill 載入後才開放的工具也要在清單裡，不然載入 skill 就能
            # 繞過使用者關掉的工具。
            copy.skills = [_limit_tools(item, allowed) for item in agent.skills]
        available = self._available_skills()
        chosen = [available[n][0] for n in options["skills"] if n in available]
        if chosen:
            own = {item.name for item in agent.skills}
            # 使用者挑的 skill 不能帶進 agent 原本沒有的工具。
            tools = {item.name for item in copy.tools}
            copy.skills = [
                *copy.skills,
                *(
                    _limit_tools(item, tools)
                    for item in chosen
                    if item.name not in own
                ),
            ]
            names = "、".join(item.name for item in chosen)
            hint = (
                f"使用者指定這段對話要使用這些技能：{names}。處理工作前，"
                f"還沒載入的先用 {skills_lib.LOAD_TOOL} 載入，照著它的指示做。"
            )
        else:
            hint = ""
        copy.instructions = "\n\n".join(
            part
            for part in (agent.instructions, hint, WEB_INSTRUCTIONS)
            if part
        )
        copy.handoffs = [
            self._with_options(state, target, model, applied)
            for target in agent.handoffs
        ]
        return copy

    def _chosen_model(self, state: _State) -> str | None:
        """這段對話自己選的模型；已經不在可選清單裡時不使用。"""
        model = state.options["model"]
        if model is None:
            return None
        names = [item["name"] for item in self._model_options()]
        return model if model in names else None

    def _sessions_dir(self, owner: str) -> pathlib.Path:
        """一個人存放對話紀錄的資料夾；本機模式和 CLI 共用專案的。"""
        if self.auth_mode == "local":
            return self.project.sessions_dir
        return migration.user_dir(self.project.root, owner) / "sessions"

    def _create(self, user: accounts_lib.User, agent: str) -> _State:
        if agent not in self.project.agents:
            raise WebError(400, f"沒有 {agent} 這個 agent")
        session = sessions_lib.create(self._sessions_dir(user.id))
        state = self._track(session, agent, _now(), user.id)
        self._save_meta(state)
        return state

    def _find(self, owner: str, session_id: str) -> _State | None:
        """找出 owner 的一段對話；別人的對話一律當成不存在。"""
        if session_id in self._states:
            state = self._states[session_id]
            return state if state.owner == owner else None
        directory = self._sessions_dir(owner)
        path = directory / f"{session_id}.jsonl"
        if not _SESSION_ID.match(session_id) or not path.exists():
            return None
        session = sessions_lib.load(directory, session_id)
        meta = _read_meta(directory, session_id)
        agent = meta.get("agent")
        state = self._track(
            session,
            agent if isinstance(agent, str) else None,
            path.stat().st_mtime * 1000,
            owner,
        )
        _restore_meta(state, meta)
        self._mark_interrupted(state, meta)
        return state

    def _mark_interrupted(self, state: _State, meta: dict[str, Any]) -> None:
        """伺服器在這段對話執行到一半時停掉的話，把它標成被中斷。

        執行中的對話在 .meta.json 有 running 記錄（伺服器正常停止時
        也保留）；更早的版本沒有這個記錄，就看紀錄裡有沒有還沒有結果的
        工具呼叫。只在還有東西可以接著做時才標記。
        """
        messages = state.session.messages
        running = meta.get("running") is True
        if not running and not loop.dangling_tool_calls(messages):
            return
        if not _resumable(messages):
            return
        state.status = "stopped"
        state.stop_reason = INTERRUPTED_STOP_REASON
        state.notice = INTERRUPTED_NOTICE

    def _save_meta(self, state: _State) -> None:
        """把起始 agent、名稱、選項、評價、回覆的模型與允許過的工具存檔。"""
        meta: dict[str, Any] = {"agent": state.agent}
        if state.custom_title is not None:
            meta["title"] = state.custom_title
        if state.category is not None:
            meta["category"] = state.category
        if state.options != _default_options():
            meta["options"] = state.options
        if state.options["tools"] is not None and state.tools_agent:
            meta["toolsAgent"] = state.tools_agent
        if state.feedback:
            meta["feedback"] = state.feedback
        if state.reply_models:
            meta["replyModels"] = state.reply_models
        if state.session_tools:
            meta["sessionTools"] = sorted(state.session_tools)
        if state.approved_calls:
            meta["approvedCalls"] = sorted(state.approved_calls)
        if (
            state.status in ("running", "waiting")
            or state.stop_reason == INTERRUPTED_STOP_REASON
        ):
            meta["running"] = True
        _write_meta(state.session.path.parent, state.session.id, meta)

    def _title(self, state: _State) -> str:
        return state.custom_title or state.title

    def _require(self, user: accounts_lib.User, session_id: str) -> _State:
        """找出這個人的一段對話；別人的對話和不存在的一樣回 404。"""
        state = self._find(user.id, session_id)
        if state is None:
            raise WebError(404, f"找不到對話：{session_id}")
        return state

    def _track(
        self,
        session: sessions_lib.Session,
        agent: str | None,
        updated: float,
        owner: str,
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
            owner=owner,
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
            "category": state.category,
        }
        summary.update({k: v for k, v in optional.items() if v is not None})
        return summary

    def _send(self, state: _State, event: Event) -> None:
        """送出一段對話的事件：只有對話的擁有者收得到。"""
        self._emit(event, state.owner)

    def _emit(self, event: Event, owner: str | None = None) -> None:
        """送出事件。

        和某段對話有關的事件（帶 sessionId 或對話摘要）只送給那段對話
        的擁有者；找不到擁有者時不送給任何人。其他事件（例如 project）
        送給所有人。
        """
        if owner is None:
            summary = event.get("summary")
            session_id = event.get("sessionId") or (
                summary.get("id") if isinstance(summary, dict) else None
            )
            if session_id is not None:
                state = self._states.get(session_id)
                if state is None:
                    return
                owner = state.owner
        for listener, user_id in list(self._listeners.items()):
            if owner is None or user_id == owner:
                listener(event)


class _LivePermissions(permissions_lib.Permissions):
    """跟著專案權限即時變動、模式可以每段對話各自設定的權限。"""

    def __init__(
        self,
        shared: permissions_lib.Permissions,
        state: _State,
        approver: permissions_lib.Approver,
        extra_allow: Callable[[], list[str]] = list,
        is_admin: Callable[[], bool] = lambda: True,
        active: Callable[[], bool] = lambda: True,
    ) -> None:
        # 不呼叫父類別的 __init__：欄位都改由 property 即時讀取。
        self._shared = shared
        self._state = state
        self._extra_allow = extra_allow
        self._is_admin = is_admin
        self._active = active
        self.approver = approver

    @property
    def mode(self) -> str:
        # 成員不能自己改成 bypass（例如在對話中被降級的管理者）。
        mode = self._state.mode
        if mode == "bypass" and not self._is_admin():
            mode = None
        return mode or self._shared.mode

    @property
    def allow(self) -> list[str]:
        return [*self._shared.allow, *self._extra_allow()]

    @property
    def deny(self) -> list[str]:
        # 帳號被停用或刪除後，連 subagent 的工具也一律拒絕。
        if not self._active():
            return ["*"]
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


# 依例外的類別名稱分類連線失敗。litellm 的例外繼承 openai 的同名類別，
# 所以比對整個繼承鏈；只比對完整的類別名稱，ModuleNotFoundError 之類
# 名稱裡剛好有 NotFoundError 的例外不會被誤判。
_AUTH_ERRORS = frozenset({"AuthenticationError", "PermissionDeniedError"})
_NOT_FOUND_ERRORS = frozenset({"NotFoundError"})
_CONNECTION_ERRORS = frozenset(
    {
        "APIConnectionError",
        "APITimeoutError",
        "Timeout",
        "TimeoutError",
        "ConnectionError",
    }
)


def _explain_failure(message: str, error: BaseException | None) -> str:
    """連線測試失敗時，在原始錯誤前面加上一句看得懂的說明。

    Args:
        message: 原始的錯誤訊息，原樣附在說明後面。
        error: 失敗時的例外；依它的類別決定說明。
    """
    names = (
        {item.__name__ for item in type(error).__mro__}
        if error is not None
        else set()
    )
    hint = "連不上模型"
    if names & _AUTH_ERRORS:
        hint = "金鑰不正確，或這把金鑰不能使用這個模型"
    elif names & _NOT_FOUND_ERRORS:
        hint = "找不到這個模型，請確認模型名稱"
    elif names & _CONNECTION_ERRORS:
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


def _refusing(item: tools_lib.Tool, message: str) -> tools_lib.Tool:
    """名稱與參數相同、但一律回報 message 的工具（不需要使用者同意）。"""

    async def refuse(**_: Any) -> str:
        raise tools_lib.ToolError(message)

    return dataclasses.replace(item, func=refuse, needs_approval=False)


def _map_tools(
    agent: agents_lib.Agent,
    convert: Callable[[tools_lib.Tool], tools_lib.Tool | None],
    applied: dict[int, agents_lib.Agent],
) -> agents_lib.Agent:
    """複製 agent，把它與 skills、subagents、轉交對象的工具都換過一次。

    Args:
        agent: 原本的 agent。
        convert: 換工具的函式；回傳 None 表示拿掉這個工具。
        applied: 已經複製過的 agent，以 id() 為鍵，避免循環時無限遞迴。

    Returns:
        換過工具的副本。
    """
    if id(agent) in applied:
        return applied[id(agent)]
    copy = dataclasses.replace(agent)
    applied[id(agent)] = copy

    def mapped(items: list[tools_lib.Tool]) -> list[tools_lib.Tool]:
        return [new for item in items if (new := convert(item)) is not None]

    copy.tools = mapped(agent.tools)
    copy.skills = [
        dataclasses.replace(item, tools=mapped(item.tools))
        for item in agent.skills
    ]
    copy.subagents = [
        _map_tools(item, convert, applied) for item in agent.subagents
    ]
    copy.handoffs = [
        _map_tools(item, convert, applied) for item in agent.handoffs
    ]
    return copy


def _write_json(path: pathlib.Path, data: Any) -> None:
    """先寫到暫存檔再換掉，寫到一半當掉也不會留下壞掉的檔案。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    os.replace(temporary, path)


@contextlib.contextmanager
def _category_errors():
    """把 CategoryError 轉成 WebError。"""
    try:
        yield
    except categories_lib.CategoryError as error:
        raise WebError(error.status, str(error)) from error


def _meta_path(directory: pathlib.Path, session_id: str) -> pathlib.Path:
    return directory / f"{session_id}.meta.json"


def _read_meta(directory: pathlib.Path, session_id: str) -> dict[str, Any]:
    """讀取存在 session 旁邊的 .meta.json；沒有或壞掉時為空 dict。

    內容：agent（起始 agent）、title（使用者改過的名稱）、options、
    toolsAgent（options.tools 是依哪個 agent 檢查的）、feedback、
    replyModels（產生每則回覆的模型）、sessionTools（這段對話都允許的
    工具）、approvedCalls（同意過的呼叫的雜湊）、running（這一輪還沒
    結束），除了 agent 都可能沒有。
    """
    try:
        meta = json.loads(_meta_path(directory, session_id).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _write_meta(
    directory: pathlib.Path, session_id: str, meta: dict[str, Any]
) -> None:
    """先寫到暫存檔再換掉，寫到一半當掉也不會留下壞掉的檔案。"""
    _write_json(_meta_path(directory, session_id), meta)


def _restore_meta(state: _State, meta: dict[str, Any]) -> None:
    """把 .meta.json 裡的名稱、選項、評價與回覆的模型放回對話狀態。

    格式不對的部分略過。
    """
    title = meta.get("title")
    if isinstance(title, str) and title.strip():
        state.custom_title = title
    category = meta.get("category")
    if isinstance(category, str) and category:
        state.category = category
    options = meta.get("options")
    if isinstance(options, dict):
        model = options.get("model")
        tools = options.get("tools")
        chosen = options.get("skills")
        folder = options.get("folder")
        state.options = {
            "model": model if isinstance(model, str) else None,
            "tools": tools if _is_string_list(tools) else None,
            "skills": chosen if _is_string_list(chosen) else [],
            "folder": folder
            if isinstance(folder, str) and folder
            else folders_lib.PRIVATE_ID,
        }
    if state.options["tools"] is not None:
        # 沒有記錄的舊檔案：當時只能在起始 agent 上設定。
        tools_agent = meta.get("toolsAgent")
        state.tools_agent = (
            tools_agent if isinstance(tools_agent, str) else state.agent
        )
    feedback = meta.get("feedback")
    if isinstance(feedback, dict):
        state.feedback = {
            key: value
            for key, value in feedback.items()
            if isinstance(value, dict) and value.get("rating") in RATINGS
        }
    reply_models = meta.get("replyModels")
    if isinstance(reply_models, dict):
        state.reply_models = {
            key: value
            for key, value in reply_models.items()
            if isinstance(key, str) and isinstance(value, str)
        }
    for key, field in (
        ("sessionTools", "session_tools"),
        ("approvedCalls", "approved_calls"),
    ):
        value = meta.get(key)
        if _is_string_list(value):
            setattr(state, field, set(value))


def _limit_tools(
    skill: skills_lib.Skill, allowed: set[str]
) -> skills_lib.Skill:
    """只留下 allowed 裡的 skill 專屬工具；都在清單裡時回傳原本的 skill。"""
    if all(item.name in allowed for item in skill.tools):
        return skill
    tools = [item for item in skill.tools if item.name in allowed]
    return dataclasses.replace(skill, tools=tools)


def _call_key(tool: str, arguments: dict[str, Any]) -> str:
    """工具名稱加參數的雜湊；參數相同（不論鍵的順序）時結果相同。"""
    canonical = json.dumps(
        [tool, arguments], ensure_ascii=False, sort_keys=True, default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _resumable(messages: list[datatypes.Message]) -> bool:
    """對話是不是停在可以不加新訊息接著跑的地方。

    最後一則是使用者訊息或工具結果，或還有沒有結果的工具呼叫（主迴圈
    會先補上結果再接著跑）。
    """
    if loop.dangling_tool_calls(messages):
        return True
    history = [m for m in messages if not compaction_lib.is_record(m)]
    return bool(history) and history[-1].get("role") in ("user", "tool")


def _edit_preview(
    context: datatypes.RunContext, path: str, arguments: dict[str, Any]
) -> dict[str, Any] | None:
    """edit_file 的預覽：照 builtin.files.edit_file 的規則算出修改後的內容。

    Returns:
        {"path", "before", "after"}；參數不是 edit_file 的格式，或這次
        修改不會成功時為 None。
    """
    old_text = arguments.get("old_text")
    new_text = arguments.get("new_text")
    replace_all = arguments.get("replace_all", False)
    if (
        not isinstance(old_text, str)
        or not isinstance(new_text, str)
        or not isinstance(replace_all, bool)
        or not old_text
        or old_text == new_text
    ):
        return None
    try:
        # 和 edit_file 一樣保留原本的換行字元；不跟著捷徑（見 paths）。
        text = paths.read_text(context, path, newline="")
    except (tools_lib.ToolError, OSError, ValueError):
        return None
    count = text.count(old_text)
    if count == 0 or (count > 1 and not replace_all):
        return None
    after = text.replace(old_text, new_text, -1 if replace_all else 1)
    return {
        "path": path,
        "before": text[:_PREVIEW_LIMIT],
        "after": after[:_PREVIEW_LIMIT],
    }


def _default_options() -> dict[str, Any]:
    return {
        "model": None,
        "tools": None,
        "skills": [],
        "folder": folders_lib.PRIVATE_ID,
    }


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


def _vision(name: str) -> bool | None:
    """litellm 知道這個模型看不看得懂圖片時回傳答案，不知道時為 None。

    litellm 載入要好幾秒，這裡在伺服器的事件迴圈上執行，不能等它載入；
    還沒載入完成（伺服器啟動時在背景載入）就先當成不知道，載入完成後
    伺服器會通知瀏覽器重新讀取。
    """
    module = sys.modules.get("litellm")
    spec = getattr(module, "__spec__", None)
    if module is None or getattr(spec, "_initializing", False):
        return None
    return _lookup_vision(name)


@functools.cache
def _lookup_vision(name: str) -> bool | None:
    try:
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
