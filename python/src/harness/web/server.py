"""網頁版的 HTTP 伺服器（`pyharness web`）。

    /api/...       JSON API，和 TypeScript 版完全相同
    /api/events    SSE，推送登入的人自己對話的事件與整個專案的事件
    /api/uploads   上傳檔案（Python 版才有，multipart/form-data）
    其他路徑        打包好的前端（static/），不需要登入就能載入

以下 API 只有 Python 版才有（ProjectInfo.features 列出支援哪些）：

    PATCH  /api/sessions/{id}            改名，{"title": ...}
    DELETE /api/sessions/{id}            刪除對話
    GET    /api/sessions/search?q=       搜尋對話
    PUT    /api/sessions/{id}/options    對話自己的模型、工具、skills 與
                                         工作資料夾
    PUT    /api/sessions/{id}/feedback   對回覆按讚或倒讚
    GET    /api/files?folder=&path=&download=1
                                         預覽或下載工作資料夾裡的檔案
    GET    /api/files/list?folder=&dir=  列出工作資料夾裡的一個資料夾
    POST   /api/uploads?folder=          上傳到工作資料夾的 uploads/

    POST   /api/auth/login               登入，{"username", "password"}
    POST   /api/auth/logout              登出
    GET    /api/auth/me                  目前登入的人
    PUT    /api/auth/password            改自己的密碼，{"current", "next"}
    GET    /api/admin/users              成員列表（管理者）
    POST   /api/admin/users              新增成員（管理者）
    PATCH  /api/admin/users/{id}         改顯示名稱、角色或停用（管理者）
    POST   /api/admin/users/{id}/password
                                         重設成員的密碼（管理者）

登入方式（見 auth_mode）：

    local     只聽本機、沒有設定密碼、也沒有任何帳號：不必登入，所有
              請求都視為一位本機管理者
    accounts  要用帳號密碼登入。登入後瀏覽器拿到 HttpOnly、SameSite=Strict
              的 cookie（harness_session），沒有登入時 API 一律回 401

第一次以帳號模式啟動、還沒有管理者時，自動建立管理者 admin，密碼是
網頁密碼（HARNESS_WEB_PASSWORD，沒有時自動產生，見 cli 模組）。之後
密碼存在帳號資料庫裡，改密碼請用網頁或 `pyharness user passwd`。

每段對話都屬於建立它的人，別人的對話一律回 404；模型連線、全站權限與
成員管理只有管理者能用（成員回 403）。

伺服器可以執行專案的工具，所以預設只聽 127.0.0.1，並檢查 Host 與
Origin 標頭，避免其他網站透過瀏覽器呼叫這裡的 API。
"""

import asyncio
import contextlib
import importlib
import json
import pathlib
import urllib.parse
from collections.abc import Iterable
from typing import Any

from aiohttp import BodyPartReader
from aiohttp import web

from harness import llm
from harness import project as project_lib
from harness.web import accounts as accounts_lib
from harness.web import files as files_lib
from harness.web import uploads as uploads_lib
from harness.web import workspace as workspace_lib

DEFAULT_PORT = 4321
DEFAULT_HOST = "127.0.0.1"
STATIC_DIR = pathlib.Path(__file__).parent / "static"
_MAX_BODY = 1_000_000
_HEARTBEAT = 25
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")
UPLOAD_PATH = "/api/uploads"
# 上傳必須帶這個標頭：自訂標頭會觸發 CORS 預檢，其他網站的表單或
# 腳本因此無法借用使用者的登入狀態上傳檔案。
UPLOAD_HEADER = "X-Harness-Upload"
_UPLOAD_CHUNK = 256 * 1024
COOKIE = "harness_session"
ADMIN_USERNAME = "admin"
ADMIN_DISPLAY_NAME = "管理者"
# 不需要登入就能呼叫的 API。
_PUBLIC_API = ("/api/auth/login", "/api/auth/logout")
_LOCAL_NO_ACCOUNTS = (
    "本機模式沒有帳號。要讓其他人使用，請先用 pyharness user add <名稱> "
    "--admin 建立管理者，再重新啟動網頁版"
)
_WORKSPACE = web.AppKey("workspace", workspace_lib.Workspace)
_ACCOUNTS = web.AppKey("accounts", accounts_lib.AccountStore)
_THROTTLE = web.AppKey("throttle", accounts_lib.LoginThrottle)
_STREAMS = web.AppKey("streams", set)
# 登入的人（aiohttp 3.13 之後建議用 RequestKey，較舊的版本用字串）。
_USER: Any = (
    web.RequestKey("user", accounts_lib.User)
    if hasattr(web, "RequestKey")
    else "user"
)


def auth_mode(
    store: accounts_lib.AccountStore,
    *,
    host: str = DEFAULT_HOST,
    password: str | None = None,
    allowed_hosts: Iterable[str] = (),
) -> str:
    """決定登入方式。

    Args:
        store: 專案的帳號資料庫。
        host: 伺服器聽的位址。
        password: 網頁密碼（HARNESS_WEB_PASSWORD 或自動產生的）。
        allowed_hosts: 另外接受的 Host（反向代理的網域）。

    Returns:
        有任何帳號、設定了密碼，或讓其他電腦連線時為 "accounts"；
        否則為 "local"。
    """
    public = host not in LOCAL_HOSTS or bool(list(allowed_hosts))
    if store.has_users() or password or public:
        return "accounts"
    return "local"


def ensure_admin(
    store: accounts_lib.AccountStore, password: str | None
) -> accounts_lib.User:
    """確保至少有一位可以使用的管理者，回傳最早建立的那一位。

    還沒有管理者時，建立 admin，密碼是 password。舊版只有一組網頁密碼，
    所以升級後用原本的密碼、帳號 admin 就能登入。

    Raises:
        ValueError: 需要建立管理者卻沒有密碼，或 admin 這個名稱已經
            被其他成員使用。
    """
    admins = [
        user
        for user in store.list_users()
        if user.is_admin and not user.disabled
    ]
    if admins:
        return admins[0]
    if not password:
        raise ValueError(
            "讓其他電腦連線（或已經建立成員）時一定要有管理者：請設定環境"
            "變數 HARNESS_WEB_PASSWORD，或用 pyharness user add <名稱> "
            "--admin 建立管理者。伺服器可以執行專案的工具，不能沒有保護"
            "就開放。"
        )
    if store.find_user(ADMIN_USERNAME) is not None:
        raise ValueError(
            f"沒有可以使用的管理者，而 {ADMIN_USERNAME} 這個名稱已經被成員"
            "使用：請用 pyharness user add <名稱> --admin 建立管理者"
        )
    user, _ = store.create_user(
        ADMIN_USERNAME, ADMIN_DISPLAY_NAME, "admin", password
    )
    return user


def create_app(
    project: project_lib.Project,
    *,
    host: str = DEFAULT_HOST,
    model_fn: llm.ModelFn | None = None,
    static_dir: pathlib.Path | None = None,
    password: str | None = None,
    allowed_hosts: Iterable[str] = (),
    max_upload_bytes: int = uploads_lib.DEFAULT_MAX_BYTES,
) -> web.Application:
    """建立網頁版的 aiohttp 應用程式。

    帳號模式還沒有管理者時建立 admin（見 ensure_admin），並把多人帳號
    之前的資料交給第一位管理者（本機模式交給本機使用者，見 migration
    模組）。

    Args:
        project: 載入完成的專案。
        host: 伺服器聽的位址，用來決定登入方式與檢查 Host 標頭。
        model_fn: 模型函式，預設透過 litellm 呼叫；測試時可換成假的。
        static_dir: 前端檔案所在的資料夾，預設為套件裡的 static/。
        password: 網頁密碼：有設定時一定要登入，還沒有管理者時用它
            建立 admin。None 且只聽本機、沒有帳號時不需要登入。
        allowed_hosts: 另外接受的 Host（瀏覽器網址上的主機名稱，例如
            反向代理的網域）。帳號模式且沒有指定時接受任何 Host。
        max_upload_bytes: 上傳的單一檔案大小上限。

    Returns:
        aiohttp 的 Application。

    Raises:
        ValueError: 需要管理者卻沒有密碼，或 harness.yaml 的 workspaces
            寫錯。
    """
    allowed_hosts = list(allowed_hosts)
    store = accounts_lib.AccountStore.for_project(project.root)
    mode = auth_mode(
        store, host=host, password=password, allowed_hosts=allowed_hosts
    )
    owner = accounts_lib.LOCAL_ID
    if mode == "accounts":
        owner = ensure_admin(store, password).id
    workspace = workspace_lib.Workspace(
        project,
        model_fn=model_fn,
        max_upload_bytes=max_upload_bytes,
        auth_mode=mode,
    )
    workspace.migrate(owner)
    app = web.Application(
        client_max_size=_MAX_BODY,
        # 第一個 middleware 在最外層，安全檢查丟出的錯誤也要轉成 JSON。
        middlewares=[
            _error_middleware,
            _guard_middleware(host, allowed_hosts, mode == "accounts"),
            _auth_middleware(mode, store),
        ],
    )
    app[_WORKSPACE] = workspace
    app[_ACCOUNTS] = store
    app[_THROTTLE] = accounts_lib.LoginThrottle()
    app[_STREAMS] = set()
    routes = web.RouteTableDef()
    _session_routes(routes)
    _file_routes(routes)
    _account_routes(routes, mode)
    _settings_routes(routes)

    @routes.get("/api/events")
    async def events(request: web.Request) -> web.StreamResponse:
        return await _stream_events(request)

    @routes.route("*", "/api/{tail:.*}")
    async def unknown_api(request: web.Request) -> web.Response:
        raise workspace_lib.WebError(
            404, f"沒有這個 API：{request.method} {request.path}"
        )

    @routes.get("/{tail:.*}")
    async def static(request: web.Request) -> web.StreamResponse:
        return _serve_static(static_dir or STATIC_DIR, request.path)

    app.add_routes(routes)
    app.on_shutdown.append(_on_shutdown)
    return app


def _session_routes(routes: web.RouteTableDef) -> None:
    """對話相關的 API；每個都只能操作自己的對話（見 Workspace）。"""
    session_id = "{session_id:[\\w-]+}"

    @routes.get("/api/project")
    async def get_project(request: web.Request) -> web.Response:
        return _json(_ws(request).project_info(_user(request)))

    @routes.get("/api/sessions")
    async def list_sessions(request: web.Request) -> web.Response:
        return _json(_ws(request).list_sessions(_user(request)))

    @routes.post("/api/sessions")
    async def create_session(request: web.Request) -> web.Response:
        body = await _body(request)
        summary = _ws(request).start_run(
            _user(request),
            _string(body, "text"),
            agent=_optional_string(body, "agent"),
            attachments=_attachments(body),
            options=body.get("options"),
        )
        return _json(summary, status=201)

    # 要在 /api/sessions/{id} 之前註冊，否則 search 會被當成對話 id。
    @routes.get("/api/sessions/search")
    async def search_sessions(request: web.Request) -> web.Response:
        query = request.query.get("q", "")
        return _json(_ws(request).search(_user(request), query))

    @routes.get(f"/api/sessions/{session_id}")
    async def get_session(request: web.Request) -> web.Response:
        return _json(_ws(request).get_session(_user(request), _id(request)))

    @routes.patch(f"/api/sessions/{session_id}")
    async def rename_session(request: web.Request) -> web.Response:
        body = await _body(request)
        title = _string(body, "title")
        return _json(_ws(request).rename(_user(request), _id(request), title))

    @routes.delete(f"/api/sessions/{session_id}")
    async def delete_session(request: web.Request) -> web.Response:
        await _body(request)
        _ws(request).delete(_user(request), _id(request))
        return _json({"ok": True})

    @routes.put(f"/api/sessions/{session_id}/options")
    async def set_options(request: web.Request) -> web.Response:
        body = await _body(request)
        return _json(
            _ws(request).set_options(_user(request), _id(request), body)
        )

    @routes.put(f"/api/sessions/{session_id}/feedback")
    async def set_feedback(request: web.Request) -> web.Response:
        body = await _body(request)
        if "feedback" not in body:
            raise workspace_lib.WebError(400, "缺少 feedback")
        _ws(request).set_feedback(
            _user(request), _id(request), body.get("index"), body["feedback"]
        )
        return _json({"ok": True})

    @routes.post(f"/api/sessions/{session_id}/messages")
    async def send(request: web.Request) -> web.Response:
        body = await _body(request)
        summary = _ws(request).start_run(
            _user(request),
            _string(body, "text"),
            session_id=_id(request),
            attachments=_attachments(body),
        )
        return _json(summary, status=202)

    @routes.post(f"/api/sessions/{session_id}/retry")
    async def retry(request: web.Request) -> web.Response:
        await _body(request)
        summary = _ws(request).retry(_user(request), _id(request))
        return _json(summary, status=202)

    @routes.post(f"/api/sessions/{session_id}/stop")
    async def stop(request: web.Request) -> web.Response:
        await _body(request)
        _ws(request).stop(_user(request), _id(request))
        return _json({"ok": True})

    @routes.put(f"/api/sessions/{session_id}/mode")
    async def set_mode(request: web.Request) -> web.Response:
        body = await _body(request)
        mode = None if body.get("mode") is None else _string(body, "mode")
        _ws(request).set_mode(_user(request), _id(request), mode)
        return _json({"ok": True})

    @routes.post(
        f"/api/sessions/{session_id}/approvals/{{approval_id:[\\w-]+}}"
    )
    async def decide(request: web.Request) -> web.Response:
        body = await _body(request)
        _ws(request).resolve_approval(
            _user(request),
            _id(request),
            request.match_info["approval_id"],
            _string(body, "decision"),
        )
        return _json({"ok": True})


def _file_routes(routes: web.RouteTableDef) -> None:
    """工作資料夾裡的檔案：先確認這個人可以使用那個資料夾。"""

    @routes.post(UPLOAD_PATH)
    async def upload(request: web.Request) -> web.Response:
        return _json({"files": await _receive_uploads(request)}, status=201)

    @routes.get("/api/files")
    async def get_file(request: web.Request) -> web.StreamResponse:
        root = _folder_path(request)
        target = _checked_file(root, request.query.get("path", ""))
        download = request.query.get("download") == "1"
        return web.FileResponse(
            target, headers=files_lib.response_headers(target, download)
        )

    @routes.get("/api/files/list")
    async def list_files(request: web.Request) -> web.Response:
        root = _folder_path(request)
        try:
            entries = files_lib.list_dir(root, request.query.get("dir", ""))
        except files_lib.FileError as error:
            raise workspace_lib.WebError(error.status, str(error)) from error
        return _json(entries)


def _account_routes(routes: web.RouteTableDef, mode: str) -> None:
    """登入、登出、改密碼與成員管理。"""
    local = mode == "local"

    @routes.post("/api/auth/login")
    async def login(request: web.Request) -> web.Response:
        body = await _body(request)
        username = body.get("username")
        password = body.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            raise workspace_lib.WebError(400, "請輸入帳號與密碼")
        if local:
            return _json(accounts_lib.current_user(accounts_lib.LOCAL_USER))
        throttle = request.app[_THROTTLE]
        address = request.remote or ""
        if throttle.locked(username, address):
            raise workspace_lib.WebError(
                429,
                f"登入失敗太多次，請 {accounts_lib.LOCK_SECONDS} 秒後再試",
            )
        store = request.app[_ACCOUNTS]
        user = await asyncio.to_thread(store.authenticate, username, password)
        if user is None:
            throttle.failed(username, address)
            raise workspace_lib.WebError(401, "帳號或密碼不正確")
        throttle.succeeded(username, address)
        response = _json(accounts_lib.current_user(user))
        _set_cookie(request, response, store.create_login(user.id))
        return response

    @routes.post("/api/auth/logout")
    async def logout(request: web.Request) -> web.Response:
        await _body(request)
        request.app[_ACCOUNTS].end_login(request.cookies.get(COOKIE))
        response = _json({"ok": True})
        response.del_cookie(COOKIE, path="/")
        return response

    @routes.get("/api/auth/me")
    async def me(request: web.Request) -> web.Response:
        return _json(accounts_lib.current_user(_user(request)))

    @routes.put("/api/auth/password")
    async def change_password(request: web.Request) -> web.Response:
        body = await _body(request)
        if local:
            raise workspace_lib.WebError(400, "本機模式不需要密碼")
        current = body.get("current")
        new = body.get("next")
        if not isinstance(current, str) or not isinstance(new, str):
            raise workspace_lib.WebError(400, "缺少 current 或 next")
        user = _user(request)
        store = request.app[_ACCOUNTS]
        throttle = request.app[_THROTTLE]
        address = request.remote or ""
        if throttle.locked(user.username, address):
            raise workspace_lib.WebError(
                429, f"失敗太多次，請 {accounts_lib.LOCK_SECONDS} 秒後再試"
            )
        checked = await asyncio.to_thread(
            store.authenticate, user.username, current
        )
        if checked is None or checked.id != user.id:
            throttle.failed(user.username, address)
            raise workspace_lib.WebError(400, "目前的密碼不正確")
        try:
            accounts_lib.check_new_password(new)
            store.set_password(user.id, new)
        except accounts_lib.AccountError as error:
            raise workspace_lib.WebError(error.status, str(error)) from error
        # 換密碼會登出所有地方，這個瀏覽器重新登入。
        response = _json({"ok": True})
        _set_cookie(request, response, store.create_login(user.id))
        return response

    @routes.get("/api/admin/users")
    async def list_users(request: web.Request) -> web.Response:
        _require_admin(request)
        if local:
            return _json([])
        users = request.app[_ACCOUNTS].list_users()
        return _json([accounts_lib.member_info(user) for user in users])

    @routes.post("/api/admin/users")
    async def add_user(request: web.Request) -> web.Response:
        _require_admin(request)
        body = await _body(request)
        if local:
            raise workspace_lib.WebError(400, _LOCAL_NO_ACCOUNTS)
        store = request.app[_ACCOUNTS]
        with _account_errors():
            user, password = store.create_user(
                body.get("username"),
                body.get("displayName"),
                body.get("role", "member"),
            )
        return _json(
            {"user": accounts_lib.member_info(user), "password": password},
            status=201,
        )

    @routes.patch("/api/admin/users/{user_id:[\\w-]+}")
    async def update_user(request: web.Request) -> web.Response:
        _require_admin(request)
        body = await _body(request)
        if local:
            raise workspace_lib.WebError(400, _LOCAL_NO_ACCOUNTS)
        disabled = body.get("disabled")
        if disabled is not None and not isinstance(disabled, bool):
            raise workspace_lib.WebError(400, "disabled 必須是 true 或 false")
        with _account_errors():
            user = request.app[_ACCOUNTS].update_user(
                request.match_info["user_id"],
                display_name=body.get("displayName"),
                role=body.get("role"),
                disabled=disabled,
            )
        return _json(accounts_lib.member_info(user))

    @routes.post("/api/admin/users/{user_id:[\\w-]+}/password")
    async def reset_password(request: web.Request) -> web.Response:
        _require_admin(request)
        await _body(request)
        if local:
            raise workspace_lib.WebError(400, _LOCAL_NO_ACCOUNTS)
        store = request.app[_ACCOUNTS]
        user_id = request.match_info["user_id"]
        password = accounts_lib.generate_password()
        with _account_errors():
            store.set_password(user_id, password)
        user = store.get_user(user_id)
        assert user is not None
        return _json(
            {"user": accounts_lib.member_info(user), "password": password}
        )


def _settings_routes(routes: web.RouteTableDef) -> None:
    """模型連線與全站權限：只有管理者能改。"""

    @routes.put("/api/settings/permissions")
    async def save_permissions(request: web.Request) -> web.Response:
        _require_admin(request)
        _ws(request).update_permissions(await _body(request))
        return _json({"ok": True})

    @routes.put("/api/settings/model")
    async def save_model(request: web.Request) -> web.Response:
        _require_admin(request)
        body = await _body(request)
        model = None if body.get("model") is None else _string(body, "model")
        max_turns = body.get("maxTurns")
        if isinstance(max_turns, float) and max_turns.is_integer():
            max_turns = int(max_turns)
        _ws(request).update_model_settings(model, max_turns)
        return _json({"ok": True})

    @routes.put("/api/settings/connection")
    async def save_connection(request: web.Request) -> web.Response:
        _require_admin(request)
        await _ws(request).update_connection(await _body(request))
        return _json({"ok": True})

    @routes.post("/api/settings/model/test")
    async def test_model(request: web.Request) -> web.Response:
        _require_admin(request)
        await _body(request)
        return _json(await _ws(request).test_model())


async def serve(
    project: project_lib.Project,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    model_fn: llm.ModelFn | None = None,
    on_ready=None,
    password: str | None = None,
    allowed_hosts: Iterable[str] = (),
    max_upload_bytes: int = uploads_lib.DEFAULT_MAX_BYTES,
) -> None:
    """啟動伺服器，直到被取消（例如使用者按 Ctrl-C）。

    Args:
        project: 載入完成的專案。
        host: 要聽的位址。
        port: 要聽的埠號，0 表示讓系統挑一個空的。
        model_fn: 模型函式，預設透過 litellm 呼叫。
        on_ready: 啟動後呼叫，參數是實際的網址。
        password: 網頁密碼，見 create_app。
        allowed_hosts: 另外接受的 Host，見 create_app。
        max_upload_bytes: 上傳的單一檔案大小上限。

    Raises:
        ValueError: 要讓其他電腦連線，卻沒有管理者也沒有設定密碼。
    """
    app = create_app(
        project,
        host=host,
        model_fn=model_fn,
        password=password,
        allowed_hosts=allowed_hosts,
        max_upload_bytes=max_upload_bytes,
    )
    runner = web.AppRunner(app, handle_signals=False)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    # litellm 載入要好幾秒，先在背景載入；列出模型時要用它判斷模型看不
    # 看得懂圖片。載入完成前先不判斷，完成後通知瀏覽器重新讀取專案資訊。
    workspace = app[_WORKSPACE]
    warm_up = asyncio.get_running_loop().run_in_executor(None, _warm_up)
    warm_up.add_done_callback(lambda _: workspace.refresh_project())
    actual = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
    shown = f"[{host}]" if ":" in host else host
    if on_ready is not None:
        on_ready(f"http://{shown}:{actual}")
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


def _user(request: web.Request) -> accounts_lib.User:
    return request[_USER]


def _require_admin(request: web.Request) -> None:
    if not _user(request).is_admin:
        raise workspace_lib.WebError(403, "只有管理者可以使用這個功能")


@contextlib.contextmanager
def _account_errors():
    """把 AccountError 轉成 WebError 的 with 區塊。"""
    try:
        yield
    except accounts_lib.AccountError as error:
        raise workspace_lib.WebError(error.status, str(error)) from error


def _set_cookie(
    request: web.Request, response: web.Response, token: str
) -> None:
    """設定登入的 cookie：HttpOnly、SameSite=Strict，HTTPS 時加上 Secure。"""
    forwarded = request.headers.get("X-Forwarded-Proto", "")
    secure = request.secure or (
        forwarded.split(",")[0].strip().lower() == "https"
    )
    response.set_cookie(
        COOKIE,
        token,
        max_age=accounts_lib.SESSION_LIFETIME,
        path="/",
        httponly=True,
        samesite="Strict",
        secure=secure,
    )


def _folder_path(request: web.Request) -> pathlib.Path:
    """?folder= 指定的工作資料夾；不能使用時回 404。"""
    folder = _ws(request).folder(_user(request), request.query.get("folder"))
    return folder.path


def _warm_up() -> None:
    try:
        importlib.import_module("litellm")
    except ImportError:
        pass


def _ws(request: web.Request) -> workspace_lib.Workspace:
    return request.app[_WORKSPACE]


def _id(request: web.Request) -> str:
    return request.match_info["session_id"]


def _json(data: Any, status: int = 200) -> web.Response:
    return web.json_response(
        data,
        status=status,
        headers={"Cache-Control": "no-store"},
        dumps=lambda value: json.dumps(value, ensure_ascii=False, default=str),
    )


async def _body(request: web.Request) -> dict[str, Any]:
    """讀取 JSON 內容；會改變狀態的請求都要經過這裡。"""
    text = await request.text()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        raise workspace_lib.WebError(400, "請求內容不是合法的 JSON 物件")
    return parsed


def _string(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str):
        raise workspace_lib.WebError(400, f"缺少 {key}")
    return value


def _optional_string(body: dict[str, Any], key: str) -> str | None:
    value = body.get(key)
    return value if isinstance(value, str) else None


def _checked_file(root: pathlib.Path, path: str) -> pathlib.Path:
    """確認是可以送出的檔案（規則見 files 模組）。"""
    if not path:
        raise workspace_lib.WebError(400, "缺少 path")
    try:
        target = files_lib.resolve(root, path)
    except files_lib.FileError as error:
        raise workspace_lib.WebError(error.status, str(error)) from error
    if not target.is_file():
        raise workspace_lib.WebError(404, f"找不到檔案：{path}")
    return target


def _attachments(body: dict[str, Any]) -> list[str]:
    value = body.get("attachments") or []
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise workspace_lib.WebError(400, "attachments 必須是字串陣列")
    return value


async def _receive_uploads(request: web.Request) -> list[dict[str, Any]]:
    """讀取 multipart 表單裡的每個檔案並存檔，邊讀邊寫，不整個放進記憶體。

    檔案存在 ?folder= 指定的工作資料夾（預設「我的檔案」）的 uploads/。
    """
    uploads = _ws(request).uploads_for(
        _user(request), request.query.get("folder")
    )
    try:
        reader = await request.multipart()
    except (AssertionError, ValueError) as error:
        raise workspace_lib.WebError(400, "請求不是 multipart 表單") from error
    saved = []
    try:
        while (part := await reader.next()) is not None:
            if not isinstance(part, BodyPartReader) or not part.filename:
                continue

            async def chunks(part: BodyPartReader = part):
                while chunk := await part.read_chunk(_UPLOAD_CHUNK):
                    yield chunk

            saved.append(await uploads.save(part.filename, chunks()))
    except uploads_lib.UploadError as error:
        raise workspace_lib.WebError(error.status, str(error)) from error
    if not saved:
        raise workspace_lib.WebError(400, "沒有收到檔案")
    return saved


def _guard_middleware(host: str, allowed_hosts: list[str], needs_login: bool):
    """只接受本機（或指定的 host）的請求，並擋掉其他網站送來的請求。

    要登入且沒有指定 allowed_hosts 時接受任何 Host：Host 檢查是為了擋
    DNS rebinding，而那種請求拿不到登入的 cookie。
    """
    accepted = {*LOCAL_HOSTS, host, *(item.lower() for item in allowed_hosts)}
    any_host = needs_login and not allowed_hosts

    @web.middleware
    async def guard(request: web.Request, handler):
        raw_host = request.headers.get("Host") or ""
        hostname = raw_host.rsplit(":", 1)[0]
        if raw_host.startswith("["):
            hostname = raw_host.split("]")[0] + "]"
        if not any_host and hostname.lower() not in accepted:
            raise workspace_lib.WebError(403, "不接受這個 Host 的請求")
        if request.path == UPLOAD_PATH and request.method == "POST":
            _check_upload(request, raw_host)
            return await handler(request)
        if request.path.startswith("/api/") and request.method != "GET":
            origin = request.headers.get("Origin")
            # 只比對主機與埠號：放在 HTTPS 反向代理後面時，Origin 是
            # https://，伺服器收到的卻是 http。
            if origin and urllib.parse.urlsplit(origin).netloc != raw_host:
                raise workspace_lib.WebError(403, "不接受其他網站送來的請求")
            content_type = request.headers.get("Content-Type", "")
            # DELETE 沒有內容；其他網站要送 DELETE 一定得先經過 CORS 預檢，
            # 不需要靠 Content-Type 擋。
            bodiless = request.method == "DELETE" and not request.can_read_body
            if not bodiless and not content_type.startswith("application/json"):
                raise workspace_lib.WebError(415, "請求內容必須是 JSON")
        return await handler(request)

    return guard


def _check_upload(request: web.Request, raw_host: str) -> None:
    """上傳是 multipart 而不是 JSON，改用自訂標頭與 Origin 擋跨站請求。"""
    if request.headers.get(UPLOAD_HEADER) != "1":
        raise workspace_lib.WebError(403, f"上傳需要 {UPLOAD_HEADER} 標頭")
    origin = request.headers.get("Origin")
    if origin and urllib.parse.urlsplit(origin).netloc != raw_host:
        raise workspace_lib.WebError(403, "不接受其他網站送來的請求")
    content_type = request.headers.get("Content-Type", "")
    if not content_type.startswith("multipart/form-data"):
        raise workspace_lib.WebError(415, "上傳必須是 multipart/form-data")


def _auth_middleware(mode: str, store: accounts_lib.AccountStore):
    """確認登入的人，放進 request[_USER]。

    本機模式所有請求都是本機管理者。帳號模式下，前端檔案（登入頁也是
    它）與登入、登出不需要登入，其他 API 都要帶有效的登入 cookie，
    否則回 401。
    """

    @web.middleware
    async def auth(request: web.Request, handler):
        if mode == "local":
            request[_USER] = accounts_lib.LOCAL_USER
            return await handler(request)
        path = request.path
        if not path.startswith("/api/") or path in _PUBLIC_API:
            return await handler(request)
        user = store.resolve_login(request.cookies.get(COOKIE))
        if user is None:
            raise workspace_lib.WebError(401, "需要登入")
        request[_USER] = user
        return await handler(request)

    return auth


@web.middleware
async def _error_middleware(request: web.Request, handler):
    """把錯誤轉成 {"error": "..."}，前端統一這樣顯示。"""
    try:
        return await handler(request)
    except workspace_lib.WebError as error:
        return _json({"error": str(error)}, status=error.status)
    except web.HTTPRequestEntityTooLarge:
        return _json({"error": "請求內容太大"}, status=413)


async def _stream_events(request: web.Request) -> web.StreamResponse:
    """用 SSE 推送事件，直到瀏覽器斷線。"""
    response = web.StreamResponse(
        headers={
            "Content-Type": "text/event-stream; charset=utf-8",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )
    await response.prepare(request)
    queue: asyncio.Queue = asyncio.Queue()
    unsubscribe = _ws(request).subscribe(_user(request), queue.put_nowait)
    request.app[_STREAMS].add(response)
    try:
        queue.put_nowait({"type": "hello"})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), _HEARTBEAT)
            except TimeoutError:
                await response.write(b": ping\n\n")
                continue
            if event is None:
                break
            text = json.dumps(event, ensure_ascii=False, default=str)
            await response.write(f"data: {text}\n\n".encode())
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        unsubscribe()
        request.app[_STREAMS].discard(response)
    return response


def _serve_static(directory: pathlib.Path, path: str) -> web.StreamResponse:
    """送出前端檔案；找不到的路徑都回 index.html，交給前端的路由處理。"""
    root = directory.resolve()
    index = root / "index.html"
    if not index.exists():
        return web.Response(
            status=503,
            text="找不到網頁檔案。從原始碼安裝時，請在 repo 根目錄執行 "
            "npm install && npm run build",
            content_type="text/plain",
        )
    target = (root / path.lstrip("/")).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        target = index
    # 只有 Vite 產生、檔名帶雜湊的 assets/ 可以長期快取。
    cache = "no-cache"
    if path.startswith("/assets/"):
        cache = "public, max-age=31536000, immutable"
    return web.FileResponse(target, headers={"Cache-Control": cache})


async def _on_shutdown(app: web.Application) -> None:
    await app[_WORKSPACE].close()
    for response in list(app[_STREAMS]):
        response.force_close()
