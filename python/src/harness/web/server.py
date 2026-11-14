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
    GET    /api/admin/groups             存取群組與可以勾選的資源（管理者）
    POST   /api/admin/groups             新增存取群組（管理者）
    PATCH  /api/admin/groups/{id}        改名稱、說明、資源或成員（管理者）
    DELETE /api/admin/groups/{id}        刪除存取群組（管理者）

登入方式（見 auth_mode）：

    local     只聽本機、沒有設定密碼、也沒有任何帳號：不必登入，所有
              請求都視為一位本機管理者。只接受直接從本機連進來的請求：
              帶有反向代理標頭（X-Forwarded-For、Forwarded、
              X-Forwarded-Host、Via）或不是從 loopback 連進來的一律 403，
              避免不小心把沒有密碼的管理者權限透過反向代理開放出去
    accounts  要用帳號密碼登入。登入後瀏覽器拿到 HttpOnly、SameSite=Strict
              的 cookie（harness_session），沒有登入時 API 一律回 401

登入的限制見 accounts.LoginThrottle；另外同時最多 LOGIN_CONCURRENCY 個
登入在驗證密碼（scrypt 很吃 CPU）。

放在反向代理後面時，用 --trusted-proxy（或環境變數
HARNESS_TRUSTED_PROXIES）指定反向代理的 IP 或網段：只有從這些位址連進
來的請求，才用 X-Forwarded-For 最右邊不是反向代理的那一站當成用戶端 IP
（登入限制依它計算），並依 X-Forwarded-Proto 決定 cookie 要不要加上
Secure。沒有指定時這兩個標頭一律不採信。

SSE 每次送出心跳前重新確認登入狀態，登出、改密碼或帳號被停用後就關閉；
管理者停用成員或改變角色時，那個人正在執行的對話也會停下來。

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
import hmac
import importlib
import ipaddress
import json
import os
import pathlib
import secrets
import urllib.parse
from collections.abc import Iterable
from typing import Any

from aiohttp import BodyPartReader
from aiohttp import web

from harness import llm
from harness import project as project_lib
from harness.builtin import paths as paths_lib
from harness.web import accounts as accounts_lib
from harness.web import files as files_lib
from harness.web import folders as folders_lib
from harness.web import groups as groups_lib
from harness.web import office as office_lib
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
_FILE_CHUNK = 256 * 1024
COOKIE = "harness_session"
# 同時最多幾個登入在驗證密碼。
LOGIN_CONCURRENCY = 4
TRUSTED_PROXIES_ENV = "HARNESS_TRUSTED_PROXIES"
# 本機模式不接受的標頭：有這些標頭表示請求是經過反向代理轉來的。
_PROXY_HEADERS = ("X-Forwarded-For", "Forwarded", "X-Forwarded-Host", "Via")
# 本機模式啟動時產生的鑰匙：網址上的 ?key=…，打開後記在 cookie。
LOCAL_KEY_PARAM = "key"
# 少了鑰匙時 API 回應的 code（和 protocol.ts 的 LOCAL_KEY_REQUIRED 一致）。
LOCAL_KEY_REQUIRED = "local_key_required"
_LOCAL_COOKIE = "harness_local"
_LOCAL_KEY_NEEDED = (
    "請用啟動網頁版時終端機顯示的完整網址（含 ?key=…）打開。不必登入的"
    "網頁版每次啟動都會產生新的鑰匙，避免其他程式或透過轉送連進來的人"
    "直接使用。"
)
_LOCAL_ONLY = (
    "沒有設定密碼的網頁版只接受從這台電腦直接連線。要透過反向代理或從"
    "其他電腦使用，請設定環境變數 HARNESS_WEB_PASSWORD（或用 pyharness "
    "user add <名稱> --admin 建立管理者）後重新啟動。"
)
_Network = ipaddress.IPv4Network | ipaddress.IPv6Network
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
_LOGINS = web.AppKey("logins", asyncio.Semaphore)
_PROXIES = web.AppKey("proxies", tuple)
_LOCAL_KEY = web.AppKey("local_key", object)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_OFFICE = web.AppKey("office", office_lib.OfficeConverter)
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


def parse_trusted_proxies(values: Iterable[str]) -> tuple[_Network, ...]:
    """把 --trusted-proxy 與 HARNESS_TRUSTED_PROXIES 的值轉成網段。

    Args:
        values: IP 或 CIDR 網段；每一項也可以是逗號分隔的多個。

    Returns:
        網段。

    Raises:
        ValueError: 有寫錯的 IP 或網段。
    """
    networks = []
    for value in values:
        for item in str(value).split(","):
            item = item.strip()
            if not item:
                continue
            try:
                networks.append(ipaddress.ip_network(item, strict=False))
            except ValueError as error:
                raise ValueError(
                    f"信任的反向代理不是 IP 或網段（例如 10.0.0.0/8）：{item}"
                ) from error
    return tuple(networks)


def create_app(
    project: project_lib.Project,
    *,
    host: str = DEFAULT_HOST,
    model_fn: llm.ModelFn | None = None,
    static_dir: pathlib.Path | None = None,
    password: str | None = None,
    allowed_hosts: Iterable[str] = (),
    max_upload_bytes: int = uploads_lib.DEFAULT_MAX_BYTES,
    trusted_proxies: Iterable[str] = (),
    local_key: str | None = None,
) -> web.Application:
    """建立網頁版的 aiohttp 應用程式。

    帳號模式還沒有管理者時建立 admin（見 ensure_admin），並把多人帳號
    之前的資料交給第一位管理者（見 migration 模組）。本機模式不搬任何
    資料，和 CLI 共用 .harness/sessions/ 與 .harness/memory.json。

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
        trusted_proxies: 信任的反向代理（IP 或網段），見模組說明。
        local_key: 本機模式要求的鑰匙（見 serve）；None 表示不要求，
            只給測試或自己另外把關的程式使用。帳號模式不使用。

    Returns:
        aiohttp 的 Application。

    Raises:
        ValueError: 需要管理者卻沒有密碼、信任的反向代理寫錯，或
            harness.yaml 的 workspaces 寫錯。
    """
    allowed_hosts = list(allowed_hosts)
    proxies = parse_trusted_proxies(trusted_proxies)
    store = accounts_lib.AccountStore.for_project(project.root)
    mode = auth_mode(
        store, host=host, password=password, allowed_hosts=allowed_hosts
    )
    owner = None
    if mode == "accounts":
        owner = ensure_admin(store, password).id
    workspace = workspace_lib.Workspace(
        project,
        model_fn=model_fn,
        max_upload_bytes=max_upload_bytes,
        auth_mode=mode,
        users=store.get_user if mode == "accounts" else None,
        groups=(
            groups_lib.GroupStore.for_project(project.root)
            if mode == "accounts"
            else None
        ),
    )
    if owner is not None:
        workspace.migrate(owner)
    app = web.Application(
        client_max_size=_MAX_BODY,
        # 第一個 middleware 在最外層，安全檢查丟出的錯誤也要轉成 JSON。
        middlewares=[
            _error_middleware,
            _guard_middleware(host, allowed_hosts, mode == "accounts"),
            *([_local_only_middleware] if mode == "local" else []),
            *(
                [_local_key_middleware(local_key)]
                if mode == "local" and local_key
                else []
            ),
            _auth_middleware(mode, store),
        ],
    )
    app[_WORKSPACE] = workspace
    app[_ACCOUNTS] = store
    app[_THROTTLE] = accounts_lib.LoginThrottle()
    app[_STREAMS] = set()
    app[_LOGINS] = asyncio.Semaphore(LOGIN_CONCURRENCY)
    app[_PROXIES] = proxies
    app[_LOCAL_KEY] = local_key if mode == "local" else None
    # 本機模式（只有自己）可以用這台電腦的 LibreOffice；要登入時一律在
    # 沙盒裡轉換，別人上傳的檔案不在伺服器上解析。
    app[_OFFICE] = office_lib.OfficeConverter(
        project.root, project.sandbox, allow_host=mode == "local"
    )
    routes = web.RouteTableDef()
    _session_routes(routes)
    _file_routes(routes)
    _account_routes(routes, mode)
    _group_routes(routes)
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
        info = _ws(request).project_info(_user(request))
        info["features"]["officePdf"] = request.app[_OFFICE].available
        return _json(info)

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
            category=_optional_string(body, "category"),
        )
        return _json(summary, status=201)

    # 要在 /api/sessions/{id} 之前註冊。
    @routes.post("/api/sessions/bulk")
    async def bulk_sessions(request: web.Request) -> web.Response:
        body = await _body(request)
        ids = body.get("ids")
        if not isinstance(ids, list) or not all(
            isinstance(item, str) for item in ids
        ):
            raise workspace_lib.WebError(400, "ids 必須是字串的陣列")
        category = body.get("category")
        if category is not None and not isinstance(category, str):
            raise workspace_lib.WebError(400, "category 必須是字串或 null")
        result = _ws(request).bulk(
            _user(request), _string(body, "action"), ids, category
        )
        return _json(result)

    @routes.get("/api/categories")
    async def list_categories(request: web.Request) -> web.Response:
        return _json(_ws(request).list_categories(_user(request)))

    @routes.post("/api/categories")
    async def create_category(request: web.Request) -> web.Response:
        body = await _body(request)
        category = _ws(request).create_category(
            _user(request), _string(body, "name")
        )
        return _json(category, status=201)

    @routes.patch("/api/categories/{category_id:[\\w-]+}")
    async def rename_category(request: web.Request) -> web.Response:
        body = await _body(request)
        category = _ws(request).rename_category(
            _user(request),
            request.match_info["category_id"],
            _string(body, "name"),
        )
        return _json(category)

    @routes.delete("/api/categories/{category_id:[\\w-]+}")
    async def delete_category(request: web.Request) -> web.Response:
        await _body(request)
        _ws(request).delete_category(
            _user(request), request.match_info["category_id"]
        )
        return _json({"ok": True})

    # 要在 /api/sessions/{id} 之前註冊，否則 search 會被當成對話 id。
    @routes.get("/api/sessions/search")
    async def search_sessions(request: web.Request) -> web.Response:
        query = request.query.get("q", "")
        return _json(_ws(request).search(_user(request), query))

    @routes.get(f"/api/sessions/{session_id}")
    async def get_session(request: web.Request) -> web.Response:
        return _json(_ws(request).get_session(_user(request), _id(request)))

    @routes.patch(f"/api/sessions/{session_id}")
    async def update_session(request: web.Request) -> web.Response:
        body = await _body(request)
        return _json(
            _ws(request).update_session(_user(request), _id(request), body)
        )

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
        folder = _request_folder(request)
        path = request.query.get("path", "")
        try:
            fd, info = files_lib.open_file(folder.path, path, folder.hidden)
        except files_lib.FileError as error:
            raise workspace_lib.WebError(error.status, str(error)) from error
        name = pathlib.PurePosixPath(path.replace("\\", "/")).name
        download = request.query.get("download") == "1"
        headers = files_lib.response_headers(name, download)
        return await _send_file(request, fd, info.st_size, headers)

    @routes.get("/api/files/pdf")
    async def get_office_pdf(request: web.Request) -> web.StreamResponse:
        folder = _request_folder(request)
        path = request.query.get("path", "")
        try:
            fd, info = files_lib.open_file(folder.path, path, folder.hidden)
        except files_lib.FileError as error:
            raise workspace_lib.WebError(error.status, str(error)) from error
        name = pathlib.PurePosixPath(path.replace("\\", "/")).name
        try:
            pdf = await request.app[_OFFICE].to_pdf(
                fd, info.st_size, pathlib.PurePosixPath(name).suffix.lower()
            )
        except office_lib.ConvertError as error:
            raise workspace_lib.WebError(error.status, str(error)) from error
        finally:
            os.close(fd)
        try:
            out = paths_lib.open_dir(pdf.parent, ())
            try:
                pdf_fd = os.open(pdf.name, os.O_RDONLY | _NOFOLLOW, dir_fd=out)
            finally:
                os.close(out)
        except OSError as error:
            raise workspace_lib.WebError(404, "找不到轉好的 PDF") from error
        size = os.fstat(pdf_fd).st_size
        headers = files_lib.response_headers(
            f"{pathlib.PurePosixPath(name).stem}.pdf", False
        )
        return await _send_file(request, pdf_fd, size, headers)

    @routes.get("/api/files/list")
    async def list_files(request: web.Request) -> web.Response:
        folder = _request_folder(request)
        try:
            entries = files_lib.list_dir(
                folder.path, request.query.get("dir", ""), folder.hidden
            )
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
        # 格式不對的名稱不可能有帳號：不算密碼，也不占登入限制的記錄。
        if not accounts_lib.valid_username(username):
            raise workspace_lib.WebError(401, "帳號或密碼不正確")
        store = request.app[_ACCOUNTS]
        user = await _checked_login(request, username, password)
        if user is None:
            raise workspace_lib.WebError(401, "帳號或密碼不正確")
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
        checked = await _checked_login(request, user.username, current)
        if checked is None or checked.id != user.id:
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
        store = request.app[_ACCOUNTS]
        user_id = request.match_info["user_id"]
        before = store.get_user(user_id)
        with _account_errors():
            user = store.update_user(
                user_id,
                display_name=body.get("displayName"),
                role=body.get("role"),
                disabled=disabled,
            )
        # 停用或改變角色：那個人正在執行的對話停下來（權限另外每次都
        # 讀最新的角色）。
        if before is not None and (
            user.disabled != before.disabled or user.role != before.role
        ):
            _ws(request).stop_runs_of(
                user.id, workspace_lib.ACCOUNT_CHANGED_NOTICE
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
        response = _json(
            {"user": accounts_lib.member_info(user), "password": password}
        )
        # 重設自己的密碼會登出所有地方，這個瀏覽器重新登入。重設別人的
        # 密碼通常是帳號可能外洩：那個人正在執行的對話也停下來。
        if user_id == _user(request).id:
            _set_cookie(request, response, store.create_login(user_id))
        else:
            _ws(request).stop_runs_of(
                user_id, workspace_lib.ACCOUNT_CHANGED_NOTICE
            )
        return response


def _group_routes(routes: web.RouteTableDef) -> None:
    """存取群組（帳號模式）：只有管理者能看與修改。"""

    @routes.get("/api/admin/groups")
    async def list_groups(request: web.Request) -> web.Response:
        _require_admin(request)
        return _json(await _ws(request).list_groups())

    @routes.post("/api/admin/groups")
    async def create_group(request: web.Request) -> web.Response:
        _require_admin(request)
        body = await _body(request)
        return _json(_ws(request).create_group(body), status=201)

    @routes.patch("/api/admin/groups/{group_id:[\\w-]+}")
    async def update_group(request: web.Request) -> web.Response:
        _require_admin(request)
        body = await _body(request)
        group_id = request.match_info["group_id"]
        return _json(_ws(request).update_group(group_id, body))

    @routes.delete("/api/admin/groups/{group_id:[\\w-]+}")
    async def delete_group(request: web.Request) -> web.Response:
        _require_admin(request)
        _ws(request).delete_group(request.match_info["group_id"])
        return _json({"ok": True})


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

    @routes.post("/api/settings/models/discover")
    async def discover_models(request: web.Request) -> web.Response:
        _require_admin(request)
        body = await _body(request)
        return _json(await _ws(request).discover_models(body))

    @routes.put("/api/settings/models")
    async def save_model_choices(request: web.Request) -> web.Response:
        _require_admin(request)
        body = await _body(request)
        names = body.get("models")
        if not isinstance(names, list) or not all(
            isinstance(item, str) for item in names
        ):
            raise workspace_lib.WebError(400, "models 必須是字串的陣列")
        _ws(request).update_model_choices(names)
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
    trusted_proxies: Iterable[str] = (),
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
        trusted_proxies: 信任的反向代理，見 create_app。

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
        trusted_proxies=trusted_proxies,
        # 不必登入時，光看連線來源分不出是本機的瀏覽器，還是沒有加轉送
        # 標頭的反向代理或 ssh 轉送：每次啟動產生一把鑰匙放在網址上。
        local_key=secrets.token_urlsafe(24),
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
    url = f"http://{shown}:{actual}"
    key = app[_LOCAL_KEY]
    if key:
        url += f"/?{urllib.parse.urlencode({LOCAL_KEY_PARAM: key})}"
    if on_ready is not None:
        on_ready(url)
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
    """設定登入的 cookie：HttpOnly、SameSite=Strict，HTTPS 時加上 Secure。

    X-Forwarded-Proto 只在請求來自信任的反向代理時採信。
    """
    secure = request.secure
    if _from_trusted_proxy(request):
        forwarded = request.headers.get("X-Forwarded-Proto", "")
        secure = secure or forwarded.split(",")[0].strip().lower() == "https"
    response.set_cookie(
        COOKIE,
        token,
        max_age=accounts_lib.SESSION_LIFETIME,
        path="/",
        httponly=True,
        samesite="Strict",
        secure=secure,
    )


def client_address(request: web.Request) -> str:
    """用戶端的 IP。

    請求來自信任的反向代理（--trusted-proxy）時，取 X-Forwarded-For
    最右邊不是信任的反向代理的那一站（最左邊的可以被用戶端自己偽造）；
    否則就是連線的位址。
    """
    remote = request.remote or ""
    if not _from_trusted_proxy(request):
        return remote
    hops = [
        hop.strip()
        for header in request.headers.getall("X-Forwarded-For", [])
        for hop in header.split(",")
        if hop.strip()
    ]
    proxies = request.app[_PROXIES]
    for hop in reversed(hops):
        if not _in_networks(hop, proxies):
            return hop
    return hops[0] if hops else remote


def _from_trusted_proxy(request: web.Request) -> bool:
    return _in_networks(request.remote or "", request.app[_PROXIES])


def _in_networks(address: str, networks: tuple[_Network, ...]) -> bool:
    if not networks:
        return False
    try:
        ip = ipaddress.ip_address(address.strip("[]").split("%", 1)[0])
    except ValueError:
        return False
    return any(ip in network for network in networks)


async def _checked_login(
    request: web.Request, username: str, password: str
) -> accounts_lib.User | None:
    """在登入限制之內確認帳號密碼。

    先在 LoginThrottle 占位（還沒有結果的也算一次），再排隊等驗證密碼
    的名額（同時最多 LOGIN_CONCURRENCY 個）。

    Raises:
        WebError: 失敗太多次，要等一下再試（429）。
    """
    throttle = request.app[_THROTTLE]
    address = client_address(request)
    wait = throttle.reserve(username, address)
    if wait:
        raise workspace_lib.WebError(429, f"登入失敗太多次，請 {wait} 秒後再試")
    result: bool | None = None
    user = None
    try:
        async with request.app[_LOGINS]:
            store = request.app[_ACCOUNTS]
            user = await asyncio.to_thread(
                store.authenticate, username, password
            )
            result = user is not None
    finally:
        throttle.release(username, address, result)
    return user


def _request_folder(request: web.Request) -> folders_lib.Folder:
    """?folder= 指定的工作資料夾；不能使用時回 404。"""
    return _ws(request).folder(_user(request), request.query.get("folder"))


async def _send_file(
    request: web.Request, fd: int, size: int, headers: dict[str, str]
) -> web.StreamResponse:
    """從已經打開的 fd 送出檔案（支援單一範圍的 Range），送完關閉 fd。

    不用 web.FileResponse：它依路徑重新打開檔案，那時路徑可能已經被換
    成捷徑（見 builtin.paths）。
    """
    try:
        start, stop, status = _byte_range(request, size)
        response = web.StreamResponse(status=status, headers=headers)
        response.headers["Accept-Ranges"] = "bytes"
        if status == 416:
            response.headers["Content-Range"] = f"bytes */{size}"
            response.content_length = 0
            await response.prepare(request)
            return response
        if status == 206:
            response.headers["Content-Range"] = (
                f"bytes {start}-{stop - 1}/{size}"
            )
        response.content_length = stop - start
        await response.prepare(request)
        if request.method == "HEAD":
            return response
        offset = start
        while offset < stop:
            length = min(_FILE_CHUNK, stop - offset)
            chunk = await asyncio.to_thread(os.pread, fd, length, offset)
            if not chunk:
                break
            await response.write(chunk)
            offset += len(chunk)
        await response.write_eof()
        return response
    finally:
        os.close(fd)


def _byte_range(request: web.Request, size: int) -> tuple[int, int, int]:
    """Range 標頭要的範圍，回傳 (開始, 結束（不含）, HTTP 狀態碼)。"""
    if "Range" not in request.headers:
        return 0, size, 200
    try:
        wanted = request.http_range
    except ValueError:
        return 0, 0, 416
    start, stop = wanted.start, wanted.stop
    if start is None and stop is None:
        return 0, size, 200
    if start is None:
        start = 0
    if start < 0:
        start = max(size + start, 0)
    stop = size if stop is None else min(stop, size)
    if start >= size or start >= stop:
        return 0, 0, 416
    return start, stop, 206


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


@web.middleware
async def _local_only_middleware(request: web.Request, handler):
    """本機模式（不必登入）只接受直接從本機連進來的請求。

    反向代理通常從 127.0.0.1 連進來、Host 也可能是 localhost，光看 Host
    擋不住；經過反向代理的請求一定會帶 X-Forwarded-For 這類標頭。
    """
    remote = request.remote or ""
    proxied = any(name in request.headers for name in _PROXY_HEADERS)
    if proxied or not _loopback(remote):
        raise workspace_lib.WebError(403, _LOCAL_ONLY)
    return await handler(request)


def _local_key_middleware(key: str):
    """本機模式要帶啟動時產生的鑰匙。

    打開 /?key=… 時記進 cookie（HttpOnly、SameSite=Strict），並轉到不含
    鑰匙的網址；之後的請求都看 cookie。cookie 名稱含埠號，同時開好幾個
    網頁版不會互相蓋掉。
    """

    @web.middleware
    async def check(request: web.Request, handler):
        cookie = f"{_LOCAL_COOKIE}_{request.url.port or 0}"
        given = request.query.get(LOCAL_KEY_PARAM)
        if (
            given is not None
            and request.method == "GET"
            and not request.path.startswith("/api/")
            and hmac.compare_digest(given.encode(), key.encode())
        ):
            rest = {
                name: value
                for name, value in request.query.items()
                if name != LOCAL_KEY_PARAM
            }
            target = request.rel_url.with_query(rest or None)
            response = web.Response(
                status=302, headers={"Location": str(target)}
            )
            response.set_cookie(
                cookie, key, path="/", httponly=True, samesite="Strict"
            )
            return response
        held = request.cookies.get(cookie, "")
        if hmac.compare_digest(held.encode(), key.encode()):
            return await handler(request)
        if request.path.startswith("/api/"):
            # code 讓已經打開的頁面顯示「請重新打開網址」而不是登入畫面。
            return _json(
                {"error": _LOCAL_KEY_NEEDED, "code": LOCAL_KEY_REQUIRED},
                status=401,
            )
        return web.Response(
            status=401,
            text=(
                '<!doctype html><meta charset="utf-8"><title>Harness</title>'
                f'<p style="font:16px/1.6 sans-serif;margin:3em auto;'
                f'max-width:36em;padding:0 16px">{_LOCAL_KEY_NEEDED}</p>'
            ),
            content_type="text/html",
        )

    return check


def _loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


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
    user = _user(request)
    unsubscribe = _ws(request).subscribe(user, queue.put_nowait)
    request.app[_STREAMS].add(response)
    try:
        queue.put_nowait({"type": "hello"})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), _HEARTBEAT)
            except TimeoutError:
                # 登出、改密碼或帳號被停用後就不再推送。
                if not _still_logged_in(request, user):
                    break
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


def _still_logged_in(request: web.Request, user: accounts_lib.User) -> bool:
    """SSE 的登入是否仍然有效（本機模式一律有效）。"""
    if user.id == accounts_lib.LOCAL_ID:
        return True
    store = request.app[_ACCOUNTS]
    current = store.resolve_login(request.cookies.get(COOKIE))
    return current is not None and current.id == user.id


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
