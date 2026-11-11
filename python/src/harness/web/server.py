"""網頁版的 HTTP 伺服器（`pyharness web`）。

    /api/...       JSON API，和 TypeScript 版完全相同
    /api/events    SSE，推送 Workspace 的所有事件
    其他路徑        打包好的前端（static/）

伺服器可以執行專案的工具，所以預設只聽 127.0.0.1，並檢查 Host 與
Origin 標頭，避免其他網站透過瀏覽器呼叫這裡的 API。

架在伺服器上給別人用時（聽 127.0.0.1 以外的位址，或用 allowed_hosts 接受
反向代理轉來的網址），一定要設定密碼：瀏覽器會跳出登入視窗（HTTP Basic
Auth），使用者名稱不限，密碼要正確才能使用。
"""

import asyncio
import base64
import binascii
import hmac
import json
import pathlib
import urllib.parse
from collections.abc import Iterable
from typing import Any

from aiohttp import web

from harness import llm
from harness import project as project_lib
from harness.web import workspace as workspace_lib

DEFAULT_PORT = 4321
DEFAULT_HOST = "127.0.0.1"
STATIC_DIR = pathlib.Path(__file__).parent / "static"
_MAX_BODY = 1_000_000
_HEARTBEAT = 25
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")
# 密碼錯誤時多等一下，拖慢暴力猜密碼。
_FAILED_LOGIN_DELAY = 1.0
_REALM = 'Basic realm="Harness", charset="UTF-8"'
_WORKSPACE = web.AppKey("workspace", workspace_lib.Workspace)
_STREAMS = web.AppKey("streams", set)


def create_app(
    project: project_lib.Project,
    *,
    host: str = DEFAULT_HOST,
    model_fn: llm.ModelFn | None = None,
    static_dir: pathlib.Path | None = None,
    password: str | None = None,
    allowed_hosts: Iterable[str] = (),
) -> web.Application:
    """建立網頁版的 aiohttp 應用程式。

    Args:
        project: 載入完成的專案。
        host: 伺服器聽的位址，用來檢查 Host 標頭。
        model_fn: 模型函式，預設透過 litellm 呼叫；測試時可換成假的。
        static_dir: 前端檔案所在的資料夾，預設為套件裡的 static/。
        password: 登入密碼；None 表示不需要登入（只適合本機使用）。
        allowed_hosts: 另外接受的 Host（瀏覽器網址上的主機名稱，例如
            反向代理的網域）。有密碼且沒有指定時接受任何 Host。

    Returns:
        aiohttp 的 Application。
    """
    middlewares = [_error_middleware]
    if password:
        middlewares.append(_auth_middleware(password))
    middlewares.append(
        _guard_middleware(host, list(allowed_hosts), bool(password))
    )
    app = web.Application(
        client_max_size=_MAX_BODY,
        # 第一個 middleware 在最外層，安全檢查丟出的錯誤也要轉成 JSON。
        middlewares=middlewares,
    )
    app[_WORKSPACE] = workspace_lib.Workspace(project, model_fn=model_fn)
    app[_STREAMS] = set()
    routes = web.RouteTableDef()
    session_id = "{session_id:[\\w-]+}"

    @routes.get("/api/project")
    async def get_project(request: web.Request) -> web.Response:
        return _json(_ws(request).project_info())

    @routes.get("/api/sessions")
    async def list_sessions(request: web.Request) -> web.Response:
        return _json(_ws(request).list_sessions())

    @routes.post("/api/sessions")
    async def create_session(request: web.Request) -> web.Response:
        body = await _body(request)
        summary = _ws(request).start_run(
            _string(body, "text"), agent=_optional_string(body, "agent")
        )
        return _json(summary, status=201)

    @routes.get(f"/api/sessions/{session_id}")
    async def get_session(request: web.Request) -> web.Response:
        return _json(_ws(request).get_session(_id(request)))

    @routes.post(f"/api/sessions/{session_id}/messages")
    async def send(request: web.Request) -> web.Response:
        body = await _body(request)
        summary = _ws(request).start_run(
            _string(body, "text"), session_id=_id(request)
        )
        return _json(summary, status=202)

    @routes.post(f"/api/sessions/{session_id}/retry")
    async def retry(request: web.Request) -> web.Response:
        await _body(request)
        return _json(_ws(request).retry(_id(request)), status=202)

    @routes.post(f"/api/sessions/{session_id}/stop")
    async def stop(request: web.Request) -> web.Response:
        await _body(request)
        _ws(request).stop(_id(request))
        return _json({"ok": True})

    @routes.put(f"/api/sessions/{session_id}/mode")
    async def set_mode(request: web.Request) -> web.Response:
        body = await _body(request)
        mode = None if body.get("mode") is None else _string(body, "mode")
        _ws(request).set_mode(_id(request), mode)
        return _json({"ok": True})

    @routes.post(
        f"/api/sessions/{session_id}/approvals/{{approval_id:[\\w-]+}}"
    )
    async def decide(request: web.Request) -> web.Response:
        body = await _body(request)
        _ws(request).resolve_approval(
            _id(request),
            request.match_info["approval_id"],
            _string(body, "decision"),
        )
        return _json({"ok": True})

    @routes.put("/api/settings/permissions")
    async def save_permissions(request: web.Request) -> web.Response:
        _ws(request).update_permissions(await _body(request))
        return _json({"ok": True})

    @routes.put("/api/settings/model")
    async def save_model(request: web.Request) -> web.Response:
        body = await _body(request)
        model = None if body.get("model") is None else _string(body, "model")
        max_turns = body.get("maxTurns")
        if isinstance(max_turns, float) and max_turns.is_integer():
            max_turns = int(max_turns)
        _ws(request).update_model_settings(model, max_turns)
        return _json({"ok": True})

    @routes.post("/api/settings/model/test")
    async def test_model(request: web.Request) -> web.Response:
        await _body(request)
        return _json(await _ws(request).test_model())

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


async def serve(
    project: project_lib.Project,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    model_fn: llm.ModelFn | None = None,
    on_ready=None,
    password: str | None = None,
    allowed_hosts: Iterable[str] = (),
) -> None:
    """啟動伺服器，直到被取消（例如使用者按 Ctrl-C）。

    Args:
        project: 載入完成的專案。
        host: 要聽的位址。
        port: 要聽的埠號，0 表示讓系統挑一個空的。
        model_fn: 模型函式，預設透過 litellm 呼叫。
        on_ready: 啟動後呼叫，參數是實際的網址。
        password: 登入密碼，見 create_app。
        allowed_hosts: 另外接受的 Host，見 create_app。

    Raises:
        ValueError: 要讓其他電腦連線，卻沒有設定密碼。
    """
    allowed_hosts = list(allowed_hosts)
    if not password and (host not in _LOCAL_HOSTS or allowed_hosts):
        raise ValueError(
            "讓其他電腦連線時一定要設定登入密碼：請設定環境變數 "
            "HARNESS_WEB_PASSWORD。伺服器可以執行專案的工具，不能沒有保護"
            "就開放。"
        )
    app = create_app(
        project,
        host=host,
        model_fn=model_fn,
        password=password,
        allowed_hosts=allowed_hosts,
    )
    runner = web.AppRunner(app, handle_signals=False)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    actual = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
    shown = f"[{host}]" if ":" in host else host
    if on_ready is not None:
        on_ready(f"http://{shown}:{actual}")
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


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


def _guard_middleware(host: str, allowed_hosts: list[str], has_password: bool):
    """只接受本機（或指定的 host）的請求，並擋掉其他網站送來的請求。

    有密碼且沒有指定 allowed_hosts 時接受任何 Host：Host 檢查是為了擋
    DNS rebinding，而那種請求拿不到登入密碼。
    """
    accepted = {*_LOCAL_HOSTS, host, *(item.lower() for item in allowed_hosts)}
    any_host = has_password and not allowed_hosts

    @web.middleware
    async def guard(request: web.Request, handler):
        raw_host = request.headers.get("Host") or ""
        hostname = raw_host.rsplit(":", 1)[0]
        if raw_host.startswith("["):
            hostname = raw_host.split("]")[0] + "]"
        if not any_host and hostname.lower() not in accepted:
            raise workspace_lib.WebError(403, "不接受這個 Host 的請求")
        if request.path.startswith("/api/") and request.method != "GET":
            origin = request.headers.get("Origin")
            # 只比對主機與埠號：放在 HTTPS 反向代理後面時，Origin 是
            # https://，伺服器收到的卻是 http。
            if origin and urllib.parse.urlsplit(origin).netloc != raw_host:
                raise workspace_lib.WebError(403, "不接受其他網站送來的請求")
            content_type = request.headers.get("Content-Type", "")
            if not content_type.startswith("application/json"):
                raise workspace_lib.WebError(415, "請求內容必須是 JSON")
        return await handler(request)

    return guard


def _auth_middleware(password: str):
    """要求 HTTP Basic Auth 登入；使用者名稱不限，只檢查密碼。"""
    expected = password.encode()

    @web.middleware
    async def auth(request: web.Request, handler):
        given = _basic_password(request.headers.get("Authorization", ""))
        if given is not None and hmac.compare_digest(given, expected):
            return await handler(request)
        if given is not None:
            await asyncio.sleep(_FAILED_LOGIN_DELAY)
        return web.Response(
            status=401,
            text="需要登入",
            headers={"WWW-Authenticate": _REALM, "Cache-Control": "no-store"},
        )

    return auth


def _basic_password(header: str) -> bytes | None:
    """從 Authorization 標頭取出 Basic Auth 的密碼。"""
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "basic" or not value:
        return None
    try:
        decoded = base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError):
        return None
    _, separator, secret = decoded.partition(b":")
    return secret if separator else None


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
    unsubscribe = _ws(request).subscribe(queue.put_nowait)
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
