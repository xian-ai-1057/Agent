"""網頁版的多人帳號、資料隔離與工作資料夾（單機模式）。

跨使用者的測試（TestIsolation）是架構文件要求的 CI 必備項目：每個對話
API 與檔案 API 都要確認別人碰不到。
"""

import asyncio
import dataclasses
import json
import os
import pathlib
import threading
import time
from unittest import mock

import aiohttp
import pytest
from aiohttp.test_utils import TestClient
from aiohttp.test_utils import TestServer
from aiohttp.test_utils import make_mocked_request

from harness import cli
from harness import permissions as permissions_lib
from harness import project as project_lib
from harness import sandbox as sandbox_lib
from harness import tools as tools_lib
from harness.builtin import shell
from harness.builtin import web as web_tools
from harness.web import accounts as accounts_lib
from harness.web import files as files_lib
from harness.web import folders as folders_lib
from harness.web import migration
from harness.web import server as server_lib
from harness.web import workspace as workspace_lib
from tests import conftest

CONFIG = """name: team
defaultAgent: main
permissions:
  mode: ask
"""

AGENT = """---
name: main
description: 測試用的助手
tools:
  - files
  - shell
---
你是助手。
"""

SANDBOX_AGENT = AGENT.replace("  - shell\n", "  - shell\n  - sandbox\n")


@pytest.fixture(autouse=True)
def fast_hash(monkeypatch):
    """測試時把 scrypt 調輕一點（參數存在雜湊裡，驗證不受影響）。"""
    monkeypatch.setattr(accounts_lib, "SCRYPT_N", 2**10)


def make_project(
    root: pathlib.Path, extra: str = "", agent: str = AGENT
) -> pathlib.Path:
    (root / "agents").mkdir(parents=True)
    (root / "harness.yaml").write_text(CONFIG + extra, encoding="utf-8")
    (root / "agents" / "main.md").write_text(agent, encoding="utf-8")
    return root


def store_of(root: pathlib.Path) -> accounts_lib.AccountStore:
    return accounts_lib.AccountStore.for_project(root)


class Person:
    """以某個人登入的瀏覽器：帶著他的 cookie 呼叫 API、收 SSE 事件。"""

    def __init__(self, client: TestClient, cookie: str | None) -> None:
        self.client = client
        self.headers = (
            {"Cookie": f"{server_lib.COOKIE}={cookie}"} if cookie else {}
        )
        self.events: list[dict] = []
        self._reader: asyncio.Task | None = None

    async def api(self, method: str, path: str, body=None):
        response = await self.client.request(
            method, f"/api{path}", json=body, headers=self.headers
        )
        try:
            data = await response.json()
        except (aiohttp.ContentTypeError, ValueError):
            data = None
        return response.status, data

    async def listen(self) -> None:
        response = await self.client.get("/api/events", headers=self.headers)
        assert response.status == 200

        async def read() -> None:
            buffer = ""
            async for chunk in response.content.iter_any():
                buffer += chunk.decode()
                while "\n\n" in buffer:
                    block, buffer = buffer.split("\n\n", 1)
                    if block.startswith("data: "):
                        self.events.append(json.loads(block[6:]))

        self._reader = asyncio.create_task(read())
        await self.until(lambda: any(e["type"] == "hello" for e in self.events))

    async def until(self, predicate) -> None:
        async with asyncio.timeout(3):
            while not predicate():
                await asyncio.sleep(0.01)

    async def wait_for(self, kind: str, predicate=lambda event: True):
        found: list[dict] = []

        def check() -> bool:
            found[:] = [
                e for e in self.events if e["type"] == kind and predicate(e)
            ]
            return bool(found)

        await self.until(check)
        return found[0]

    async def wait_idle(self, session_id: str) -> dict:
        async with asyncio.timeout(3):
            while True:
                _, detail = await self.api("GET", f"/sessions/{session_id}")
                if detail["summary"]["status"] not in ("running",):
                    return detail
                await asyncio.sleep(0.02)

    def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()


class Site:
    """啟動好的伺服器，可以用不同的人登入。"""

    def __init__(self, client: TestClient, root: pathlib.Path, model) -> None:
        self.client = client
        self.root = root
        self.model = model
        self.people: list[Person] = []

    async def login(self, username: str, password: str) -> Person:
        response = await self.client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )
        assert response.status == 200, await response.text()
        person = Person(self.client, response.cookies[server_lib.COOKIE].value)
        self.people.append(person)
        return person

    def anonymous(self) -> Person:
        person = Person(self.client, None)
        self.people.append(person)
        return person


@pytest.fixture
async def site(tmp_path, fake_model):
    """建立有帳號的專案並啟動伺服器；回傳啟動用的函式。

    users 是 [(使用者名稱, 角色)]，密碼一律是 "<名稱>-password-1"。
    """
    started = []

    async def launch(
        script=None,
        users=(("boss", "admin"), ("alice", "member"), ("bob", "member")),
        extra="",
        agent=AGENT,
        prepare=None,
        **options,
    ):
        root = make_project(tmp_path / f"s{len(started)}", extra, agent)
        store = store_of(root)
        for name, role in users:
            store.create_user(name, None, role, f"{name}-password-1")
        if prepare is not None:
            prepare(root)
        loaded = project_lib.load_project(root)
        model = fake_model(script or [])
        app = server_lib.create_app(loaded, model_fn=model, **options)
        client = TestClient(
            TestServer(app), cookie_jar=aiohttp.DummyCookieJar()
        )
        await client.start_server()
        site = Site(client, root, model)
        started.append((site, loaded))
        return site

    yield launch
    for site, loaded in started:
        for person in site.people:
            person.close()
        await site.client.close()
        loaded.close()


def password_of(name: str) -> str:
    return f"{name}-password-1"


def private(root: pathlib.Path, user_id: str) -> pathlib.Path:
    return root / folders_lib.PRIVATE_DIR / user_id


def user_id(root: pathlib.Path, username: str) -> str:
    user = store_of(root).find_user(username)
    assert user is not None
    return user.id


class TestAccountStore:
    """帳號資料庫本身。"""

    def test_password_is_hashed_with_scrypt_and_salt(self, tmp_path):
        store = store_of(tmp_path)

        user, password = store.create_user("Alice", "愛麗絲")
        _, other = store.create_user("bob", password="same-password-1")

        assert len(password) >= 16
        with store._connect() as db:  # noqa: SLF001
            hashes = [
                row[0] for row in db.execute("SELECT password_hash FROM users")
            ]
        assert all(item.startswith("scrypt$1024$8$1$") for item in hashes)
        assert password not in hashes[0]
        assert store.authenticate("alice", password).id == user.id
        assert store.authenticate("ALICE", password) is not None
        assert store.authenticate("alice", "wrong") is None
        assert store.authenticate("nobody", password) is None
        assert user.display_name == "愛麗絲"
        assert user.role == "member"

    @pytest.mark.parametrize("name", ["a", "x" * 33, "王小明", "a b", ""])
    def test_username_rules(self, tmp_path, name):
        with pytest.raises(accounts_lib.AccountError):
            store_of(tmp_path).create_user(name)

    def test_duplicate_username_ignores_case(self, tmp_path):
        store = store_of(tmp_path)
        store.create_user("alice")

        with pytest.raises(accounts_lib.AccountError) as error:
            store.create_user("ALICE")

        assert error.value.status == 409

    @pytest.mark.parametrize("display_name", ["", "   ", "長" * 51])
    def test_display_name_rules(self, tmp_path, display_name):
        with pytest.raises(accounts_lib.AccountError):
            store_of(tmp_path).create_user("alice", display_name)

    def test_last_admin_cannot_be_demoted_or_disabled(self, tmp_path):
        store = store_of(tmp_path)
        boss, _ = store.create_user("boss", role="admin")

        for change in ({"role": "member"}, {"disabled": True}):
            with pytest.raises(accounts_lib.AccountError, match="管理者"):
                store.update_user(boss.id, **change)

        second, _ = store.create_user("second", role="admin")
        store.update_user(boss.id, role="member")
        with pytest.raises(accounts_lib.AccountError):
            store.update_user(second.id, disabled=True)

    def test_disabled_user_cannot_log_in_and_sessions_end(self, tmp_path):
        store = store_of(tmp_path)
        store.create_user("boss", role="admin")
        alice, password = store.create_user("alice")
        token = store.create_login(alice.id)

        store.update_user(alice.id, disabled=True)

        assert store.authenticate("alice", password) is None
        assert store.resolve_login(token) is None

    def test_login_token_is_stored_only_as_a_hash(self, tmp_path):
        store = store_of(tmp_path)
        alice, _ = store.create_user("alice")

        token = store.create_login(alice.id)

        with store._connect() as db:  # noqa: SLF001
            [row] = db.execute("SELECT token_hash FROM login_sessions")
        assert token not in row[0]
        assert store.resolve_login(token).id == alice.id
        store.end_login(token)
        assert store.resolve_login(token) is None

    def test_login_expires_after_idle_timeout_and_slides(self, tmp_path):
        now = [1000.0]
        store = accounts_lib.AccountStore.for_project(
            tmp_path, clock=lambda: now[0]
        )
        alice, _ = store.create_user("alice")
        token = store.create_login(alice.id)

        # 一直有在用：超過 14 天也不會過期（每次使用往後延）。
        for _ in range(40):
            now[0] += accounts_lib.IDLE_TIMEOUT - 60
            assert store.resolve_login(token) is not None
        now[0] += accounts_lib.IDLE_TIMEOUT

        assert store.resolve_login(token) is None

    def test_new_password_ends_every_login(self, tmp_path):
        store = store_of(tmp_path)
        alice, _ = store.create_user("alice")
        token = store.create_login(alice.id)

        store.set_password(alice.id, "another-password")

        assert store.resolve_login(token) is None
        assert store.authenticate("alice", "another-password") is not None

    @staticmethod
    def _fail(throttle, username, address):
        assert throttle.reserve(username, address) == 0
        throttle.release(username, address, False)

    def test_throttle_backs_off_per_username_and_ip(self):
        now = [0.0]
        throttle = accounts_lib.LoginThrottle(clock=lambda: now[0])

        for _ in range(accounts_lib.MAX_FAILURES):
            self._fail(throttle, "Alice", "6.6.6.6")

        # 同一個 IP 要等；其他 IP 上真正的使用者、其他帳號名稱不受影響。
        first = throttle.reserve("alice", "6.6.6.6")
        assert first == accounts_lib.BACKOFF_SECONDS
        assert throttle.reserve("alice", "1.2.3.4") == 0
        throttle.release("alice", "1.2.3.4", True)
        assert throttle.reserve("bob", "6.6.6.6") == 0
        throttle.release("bob", "6.6.6.6", True)
        now[0] += first
        self._fail(throttle, "alice", "6.6.6.6")
        # 每次失敗等待加倍，但有上限。
        assert throttle.reserve("alice", "6.6.6.6") == 2 * first
        for _ in range(20):
            now[0] += accounts_lib.MAX_BACKOFF_SECONDS
            self._fail(throttle, "alice", "6.6.6.6")
        assert (
            throttle.reserve("alice", "6.6.6.6")
            == accounts_lib.MAX_BACKOFF_SECONDS
        )
        now[0] += accounts_lib.MAX_BACKOFF_SECONDS
        assert throttle.reserve("alice", "6.6.6.6") == 0
        throttle.release("alice", "6.6.6.6", True)
        # 成功登入後歸零。
        assert throttle.reserve("alice", "6.6.6.6") == 0

    def test_many_ips_guessing_one_username_are_capped(self, monkeypatch):
        monkeypatch.setattr(accounts_lib, "MAX_NAME_FAILURES", 12)
        now = [0.0]
        throttle = accounts_lib.LoginThrottle(clock=lambda: now[0])

        for index in range(12):
            self._fail(throttle, "alice", f"10.0.0.{index}")

        assert throttle.reserve("alice", "10.9.9.9") > 0
        assert throttle.reserve("bob", "10.9.9.9") == 0
        now[0] += accounts_lib.IP_WINDOW_SECONDS
        assert throttle.reserve("alice", "10.9.9.9") == 0

    def test_attempts_in_flight_count_before_they_finish(self):
        throttle = accounts_lib.LoginThrottle(clock=lambda: 0.0)

        reserved = [throttle.reserve("alice", "10.0.0.1") for _ in range(20)]
        # 同一個 IP 同時試很多帳號名稱，也只有剩下的次數會被檢查。
        spray = [throttle.reserve(f"u{i}", "10.0.0.2") for i in range(200)]

        assert reserved.count(0) == accounts_lib.MAX_FAILURES
        assert all(wait > 0 for wait in reserved[accounts_lib.MAX_FAILURES :])
        assert spray.count(0) == accounts_lib.MAX_IP_FAILURES

    def test_one_ip_cannot_spray_many_usernames(self):
        now = [0.0]
        throttle = accounts_lib.LoginThrottle(clock=lambda: now[0])

        for index in range(accounts_lib.MAX_IP_FAILURES):
            self._fail(throttle, f"user{index}", "6.6.6.6")

        assert throttle.reserve("someone", "6.6.6.6") > 0
        assert throttle.reserve("someone", "7.7.7.7") == 0
        now[0] += accounts_lib.IP_WINDOW_SECONDS
        assert throttle.reserve("other", "6.6.6.6") == 0

    def test_throttle_entries_expire_and_are_bounded(self, monkeypatch):
        monkeypatch.setattr(accounts_lib, "MAX_THROTTLE_ENTRIES", 40)
        monkeypatch.setattr(accounts_lib, "MAX_IP_FAILURES", 10_000)
        now = [0.0]
        throttle = accounts_lib.LoginThrottle(clock=lambda: now[0])

        for index in range(200):
            self._fail(throttle, f"user{index}", "1.2.3.4")
            now[0] += 0.001
        sizes = len(throttle._names)  # noqa: SLF001
        now[0] += accounts_lib.FORGET_SECONDS
        self._fail(throttle, "late", "1.2.3.4")

        assert sizes <= 40
        assert set(throttle._names) == {"late"}  # noqa: SLF001


class TestLogin:
    """登入、登出、cookie 與改密碼。"""

    async def test_login_sets_a_strict_http_only_cookie(self, site):
        s = await site()

        response = await s.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": password_of("alice")},
        )

        assert response.status == 200
        body = await response.json()
        assert body["username"] == "alice"
        assert body["role"] == "member"
        assert set(body) == {"id", "username", "displayName", "role"}
        cookie = response.cookies[server_lib.COOKIE]
        assert cookie["httponly"]
        assert cookie["samesite"] == "Strict"
        assert cookie["path"] == "/"
        assert not cookie["secure"]
        assert int(cookie["max-age"]) == accounts_lib.SESSION_LIFETIME

    @pytest.mark.parametrize(
        ("proxies", "secure"), [(["127.0.0.1"], True), ([], False)]
    )
    async def test_cookie_is_secure_only_behind_a_trusted_https_proxy(
        self, site, proxies, secure
    ):
        s = await site(trusted_proxies=proxies)

        response = await s.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": password_of("alice")},
            headers={"X-Forwarded-Proto": "https"},
        )

        assert bool(response.cookies[server_lib.COOKIE]["secure"]) is secure

    async def test_me_logout_and_401_without_session(self, site):
        s = await site()
        alice = await s.login("alice", password_of("alice"))

        status, me = await alice.api("GET", "/auth/me")
        logout, _ = await alice.api("POST", "/auth/logout", {})
        after, error = await alice.api("GET", "/auth/me")
        anonymous, body = await s.anonymous().api("GET", "/sessions")

        assert status == 200
        assert me["username"] == "alice"
        assert logout == 200
        assert after == 401
        assert error == {"error": "需要登入"}
        assert anonymous == 401
        assert body == {"error": "需要登入"}

    @pytest.mark.parametrize(
        ("username", "password"),
        [("alice", "wrong-password"), ("nobody", "whatever-123")],
    )
    async def test_failures_are_generic(self, site, username, password):
        s = await site()

        status, body = await s.anonymous().api(
            "POST", "/auth/login", {"username": username, "password": password}
        )

        assert status == 401
        assert body == {"error": "帳號或密碼不正確"}

    async def test_repeated_failures_are_locked_out(self, site):
        s = await site()
        person = s.anonymous()
        wrong = {"username": "alice", "password": "wrong-password"}

        statuses = [
            (await person.api("POST", "/auth/login", wrong))[0]
            for _ in range(accounts_lib.MAX_FAILURES + 1)
        ]
        right = {"username": "alice", "password": password_of("alice")}
        locked, body = await person.api("POST", "/auth/login", right)

        assert statuses[:-1] == [401] * accounts_lib.MAX_FAILURES
        assert statuses[-1] == 429
        assert locked == 429
        assert f"{accounts_lib.BACKOFF_SECONDS} 秒" in body["error"]

    async def test_disabled_user_is_logged_out_and_cannot_log_in(self, site):
        s = await site()
        alice = await s.login("alice", password_of("alice"))
        boss = await s.login("boss", password_of("boss"))
        alice_id = user_id(s.root, "alice")

        status, info = await boss.api(
            "PATCH", f"/admin/users/{alice_id}", {"disabled": True}
        )
        after, _ = await alice.api("GET", "/sessions")
        again, _ = await s.anonymous().api(
            "POST",
            "/auth/login",
            {"username": "alice", "password": password_of("alice")},
        )

        assert status == 200
        assert info["disabled"] is True
        assert after == 401
        assert again == 401

    async def test_change_password(self, site):
        s = await site()
        alice = await s.login("alice", password_of("alice"))

        short, error = await alice.api(
            "PUT",
            "/auth/password",
            {"current": password_of("alice"), "next": "x"},
        )
        wrong, _ = await alice.api(
            "PUT", "/auth/password", {"current": "nope", "next": "x" * 12}
        )
        response = await s.client.put(
            "/api/auth/password",
            json={"current": password_of("alice"), "next": "a-new-password"},
            headers=alice.headers,
        )
        old_cookie, _ = await alice.api("GET", "/auth/me")

        assert short == 400
        assert "10" in error["error"]
        assert wrong == 400
        assert response.status == 200
        # 換密碼後舊的登入失效，這個瀏覽器拿到新的 cookie。
        assert old_cookie == 401
        fresh = response.cookies[server_lib.COOKIE].value
        assert store_of(s.root).resolve_login(fresh).username == "alice"
        assert store_of(s.root).authenticate("alice", "a-new-password")

    async def test_login_page_assets_load_without_login(self, site, tmp_path):
        static = tmp_path / "static"
        static.mkdir()
        (static / "index.html").write_text("<html>app</html>", "utf-8")
        s = await site(static_dir=static)

        response = await s.client.get("/")

        assert response.status == 200
        assert "app" in await response.text()


LEGACY_ID = "20250101-000000-aaaaaa"


def legacy_data(root: pathlib.Path) -> None:
    """加入帳號之前（或本機模式）的資料：對話、記憶與專案裡的檔案。"""
    sessions = root / ".harness" / "sessions"
    sessions.mkdir(parents=True)
    lines = [
        {
            "role": "user",
            "content": "舊的問題\n\n[附件] workspace/uploads/old.csv",
        },
        {"role": "assistant", "content": "圖在 workspace/chart.png"},
    ]
    (sessions / f"{LEGACY_ID}.jsonl").write_text(
        "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in lines),
        "utf-8",
    )
    (sessions / f"{LEGACY_ID}.meta.json").write_text(
        json.dumps({"agent": "main"}), "utf-8"
    )
    (root / ".harness" / "memory.json").write_text("[]", "utf-8")
    uploads = root / "workspace" / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "old.csv").write_text("a,b", "utf-8")
    (root / "workspace" / "chart.png").write_bytes(b"png")
    (root / "report.md").write_text("# 報告", "utf-8")


class TestLocalMode:
    """只在本機使用、沒有帳號：不必登入，和加入帳號之前完全一樣。"""

    async def test_works_without_login(self, site):
        s = await site([conftest.text_response("好")], users=())
        person = s.anonymous()

        _, info = await person.api("GET", "/project")
        _, me = await person.api("GET", "/auth/me")
        status, created = await person.api("POST", "/sessions", {"text": "嗨"})
        await person.wait_idle(created["id"])
        _, members = await person.api("GET", "/admin/users")
        add, error = await person.api(
            "POST", "/admin/users", {"username": "alice", "role": "member"}
        )

        assert info["auth"]["mode"] == "local"
        assert me["id"] == "local"
        assert me["role"] == "admin"
        assert status == 201
        assert members == []
        assert add == 400
        assert "pyharness user add" in error["error"]
        assert store_of(s.root).list_users() == []
        # 和 CLI 共用同一份對話紀錄。
        sessions = s.root / ".harness" / "sessions"
        assert (sessions / f"{created['id']}.jsonl").exists()
        assert not (s.root / ".harness" / "users").exists()

    async def test_keeps_cli_data_and_works_in_the_project_root(self, site):
        read = ("read_file", {"path": "report.md"})
        s = await site(
            [conftest.tool_response(read), conftest.text_response("好")],
            users=(),
            prepare=legacy_data,
        )
        person = s.anonymous()

        _, info = await person.api("GET", "/project")
        _, listed = await person.api("GET", "/sessions")
        served = await s.client.get("/api/files", params={"path": "report.md"})
        chart = await s.client.get(
            "/api/files", params={"path": "workspace/chart.png"}
        )
        _, created = await person.api("POST", "/sessions", {"text": "讀報告"})
        detail = await person.wait_idle(created["id"])

        assert info["features"]["folders"] is False
        assert [item["id"] for item in listed][-1] == LEGACY_ID
        # 沒有搬走任何東西：pyharness chat / memory 看到的是同一份。
        assert (
            s.root / ".harness" / "sessions" / f"{LEGACY_ID}.jsonl"
        ).exists()
        assert (s.root / ".harness" / "memory.json").exists()
        assert not (s.root / migration.MARKER).exists()
        assert not (s.root / "private").exists()
        assert served.status == 200
        assert await served.text() == "# 報告"
        assert chart.status == 200
        tool = next(m for m in detail["messages"] if m["role"] == "tool")
        assert tool["content"] == "# 報告"

    @pytest.mark.parametrize(
        "header",
        [
            {"X-Forwarded-For": "203.0.113.9"},
            {"Forwarded": "for=203.0.113.9"},
            {"X-Forwarded-Host": "ai.example.com"},
            {"Via": "1.1 nginx"},
        ],
    )
    async def test_proxied_requests_are_refused(self, site, header):
        s = await site(users=())

        plain = await s.client.get("/api/project")
        proxied = await s.client.get("/api/project", headers=header)
        page = await s.client.get("/", headers=header)

        assert plain.status == 200
        assert proxied.status == 403
        assert "HARNESS_WEB_PASSWORD" in (await proxied.json())["error"]
        assert page.status == 403

    async def test_requests_from_other_machines_are_refused(self):
        transport = mock.Mock()
        transport.get_extra_info.side_effect = lambda name, default=None: (
            ("192.168.1.20", 5555) if name == "peername" else default
        )
        request = make_mocked_request(
            "GET", "/api/project", transport=transport
        )

        async def handler(_):
            raise AssertionError("不應該處理這個請求")

        with pytest.raises(workspace_lib.WebError) as error:
            await server_lib._local_only_middleware(request, handler)  # noqa: SLF001

        assert error.value.status == 403

    async def test_any_user_makes_login_required(self, site):
        s = await site(users=(("boss", "admin"),))

        status, _ = await s.anonymous().api("GET", "/project")

        assert status == 401


class TestBootstrapAndMigration:
    """升級：舊的網頁密碼變成 admin 的密碼，舊資料交給第一位管理者。"""

    async def test_password_creates_admin_and_old_data_moves(self, site):
        s = await site(
            [conftest.text_response("好")],
            users=(),
            prepare=legacy_data,
            password="old-web-password",
        )

        admin = await s.login("admin", "old-web-password")
        _, listed = await admin.api("GET", "/sessions")
        _, me = await admin.api("GET", "/auth/me")
        _, info = await admin.api("GET", "/project")
        _, detail = await admin.api("GET", f"/sessions/{LEGACY_ID}")

        assert me["role"] == "admin"
        assert [item["title"] for item in listed] == ["舊的問題"]
        owner = me["id"]
        base = s.root / ".harness" / "users" / owner
        assert (base / "sessions" / f"{LEGACY_ID}.jsonl").exists()
        assert (base / "memory.json").exists()
        assert not (s.root / ".harness" / "sessions").exists()
        assert not (s.root / ".harness" / "memory.json").exists()
        # 舊對話在「舊資料（專案資料夾）」裡：檔案不搬，路徑照舊。
        assert detail["options"]["folder"] == "legacy"
        legacy = next(f for f in info["folders"] if f["id"] == "legacy")
        assert legacy == {
            "id": "legacy",
            "name": "舊資料（專案資料夾）",
            "kind": "shared",
            "writable": False,
        }
        assert (s.root / "workspace" / "uploads" / "old.csv").exists()
        assert info["features"]["folders"] is True

    async def test_old_file_cards_resolve_only_for_the_admin(self, site):
        s = await site(
            users=(("boss", "admin"), ("alice", "member")),
            prepare=lambda root: (
                legacy_data(root),
                (root / "private" / "x").mkdir(parents=True),
                (root / "private" / "x" / "secret.txt").write_text("s"),
            ),
        )
        boss = await s.login("boss", password_of("boss"))
        alice = await s.login("alice", password_of("alice"))

        async def get(person, path, folder="legacy"):
            response = await s.client.get(
                "/api/files",
                params={"folder": folder, "path": path},
                headers=person.headers,
            )
            return response.status

        _, entries = await boss.api("GET", "/files/list?folder=legacy")
        _, alice_info = await alice.api("GET", "/project")

        assert await get(boss, "workspace/uploads/old.csv") == 200
        assert await get(boss, "workspace/chart.png") == 200
        assert await get(boss, "report.md") == 200
        # 碰不到 .harness/、private/ 與以點開頭的檔案。
        assert await get(boss, ".harness/users/x.json") == 403
        assert await get(boss, "private/x/secret.txt") == 403
        names = [item["name"] for item in entries]
        assert "private" not in names
        assert "report.md" in names
        # 只有接收舊資料的管理者看得到這個資料夾；舊檔案也不在「共用」。
        assert "legacy" not in [f["id"] for f in alice_info["folders"]]
        assert await get(alice, "report.md") == 404
        assert await get(alice, "chart.png", "shared:shared") == 404
        assert await get(alice, "workspace/chart.png", "shared:shared") == 404

    async def test_legacy_folder_is_read_only_and_follows_the_role(self, site):
        s = await site(
            [
                conftest.tool_response(
                    ("write_file", {"path": "tools/evil.py", "content": "x"})
                ),
                conftest.text_response("好"),
            ],
            users=(("boss", "admin"), ("other", "admin")),
            prepare=legacy_data,
        )
        boss = await s.login("boss", password_of("boss"))
        other = await s.login("other", password_of("other"))

        await boss.api(
            "POST", f"/sessions/{LEGACY_ID}/messages", {"text": "改一下"}
        )
        detail = await boss.wait_idle(LEGACY_ID)
        tool = next(m for m in detail["messages"] if m["role"] == "tool")
        # 專案資料夾裡有 tools/ 與 harness.yaml，舊資料資料夾一律唯讀。
        assert not (s.root / "tools" / "evil.py").exists()
        assert "唯讀" in tool["content"]

        # 被降為成員後就看不到整個專案資料夾。
        await other.api(
            "PATCH",
            f"/admin/users/{(await boss.api('GET', '/auth/me'))[1]['id']}",
            {"role": "member"},
        )
        _, info = await boss.api("GET", "/project")
        status, _ = await boss.api("GET", "/files/list?folder=legacy")
        assert "legacy" not in [f["id"] for f in info["folders"]]
        assert status == 404

    async def test_legacy_folder_runs_cannot_run_code_or_reach_private(
        self, site, monkeypatch
    ):
        async def never(*args, **kwargs):
            raise AssertionError("舊資料資料夾不能執行指令")

        monkeypatch.setattr(shell, "execute", never)
        peek = ("read_file", {"path": "private/x/secret.txt"})
        s = await site(
            [conftest.tool_response(peek), conftest.text_response("好")],
            users=(("boss", "admin"),),
            prepare=lambda root: (
                legacy_data(root),
                (root / "private" / "x").mkdir(parents=True),
                (root / "private" / "x" / "secret.txt").write_text("s"),
            ),
            extra="sandbox: true\n",
            agent=SANDBOX_AGENT,
        )
        boss = await s.login("boss", password_of("boss"))

        status, _ = await boss.api(
            "POST", f"/sessions/{LEGACY_ID}/messages", {"text": "再看看"}
        )
        detail = await boss.wait_idle(LEGACY_ID)

        assert status == 202
        request = s.model.requests[0]
        names = [item["function"]["name"] for item in request["tools"]]
        assert "run_command" not in names
        assert "run_python" not in names
        assert "read_file" in names
        assert "舊資料" in request["messages"][0]["content"]
        tool = next(m for m in detail["messages"] if m["role"] == "tool")
        assert "不能存取" in tool["content"]
        assert "s" != tool["content"]

    async def test_local_mode_data_moves_when_accounts_are_enabled(
        self, tmp_path, fake_model
    ):
        root = make_project(tmp_path / "l")
        loaded = project_lib.load_project(root)
        model = fake_model([conftest.text_response("好")])
        local = server_lib.create_app(loaded, model_fn=model)
        async with TestClient(TestServer(local)) as client:
            response = await client.post("/api/sessions", json={"text": "嗨"})
            created = await response.json()
            await asyncio.sleep(0.1)
        loaded.close()
        assert (root / ".harness" / "sessions").exists()

        loaded = project_lib.load_project(root)
        server_lib.create_app(loaded, password="secret-password")
        loaded.close()

        admin = store_of(root).find_user("admin")
        moved = migration.user_dir(root, admin.id) / "sessions"
        meta = json.loads(
            (moved / f"{created['id']}.meta.json").read_text("utf-8")
        )
        assert meta["options"]["folder"] == "legacy"
        assert migration.legacy_owner(root) == admin.id

    def test_migration_is_idempotent_and_never_overwrites(self, tmp_path):
        root = make_project(tmp_path / "m")
        legacy_data(root)

        first = migration.migrate(root, "abc")
        again = migration.migrate(root, "abc")

        assert ".harness/sessions" in first
        assert again == []
        # 之後 CLI 建立的對話不會再被搬走。
        (root / ".harness" / "sessions").mkdir()
        (root / ".harness" / "sessions" / "x.jsonl").write_text("", "utf-8")
        assert migration.migrate(root, "abc") == []
        assert (root / ".harness" / "sessions" / "x.jsonl").exists()
        # workspace/ 不搬。
        assert (root / "workspace" / "chart.png").exists()

    def test_fresh_project_gets_no_legacy_folder(self, tmp_path):
        root = make_project(tmp_path / "f")

        migration.migrate(root, "abc")

        assert (root / migration.MARKER).exists()
        assert migration.legacy_owner(root) is None

    def test_local_data_moves_to_the_first_admin(self, tmp_path):
        root = make_project(tmp_path / "m")
        local = root / ".harness" / "users" / "local" / "sessions"
        local.mkdir(parents=True)
        (local / "s1.jsonl").write_text("", "utf-8")
        (root / "private" / "local").mkdir(parents=True)
        (root / "private" / "local" / "a.txt").write_text("a", "utf-8")
        # 管理者已經有同名的檔案：不覆蓋，留在原處。
        (root / "private" / "admin1").mkdir(parents=True)
        (root / "private" / "admin1" / "a.txt").write_text("mine", "utf-8")
        (root / "private" / "local" / "b.txt").write_text("b", "utf-8")

        migration.migrate(root, "admin1")

        target = root / ".harness" / "users" / "admin1" / "sessions"
        assert (target / "s1.jsonl").exists()
        assert (root / "private" / "admin1" / "a.txt").read_text() == "mine"
        assert (root / "private" / "admin1" / "b.txt").exists()
        assert (root / "private" / "local" / "a.txt").exists()

    def test_member_named_admin_blocks_bootstrap(self, tmp_path):
        root = make_project(tmp_path / "b")
        store_of(root).create_user("admin", role="member")
        loaded = project_lib.load_project(root)

        with pytest.raises(ValueError, match="--admin"):
            server_lib.create_app(loaded, password="secret-password")
        loaded.close()

    def test_users_without_admin_need_a_password(self, tmp_path):
        root = make_project(tmp_path / "b")
        store_of(root).create_user("alice", role="member")
        loaded = project_lib.load_project(root)

        with pytest.raises(ValueError, match="HARNESS_WEB_PASSWORD"):
            server_lib.create_app(loaded)
        app = server_lib.create_app(loaded, password="secret-password")
        loaded.close()

        assert app is not None
        assert store_of(root).find_user("admin").is_admin


class TestAdmin:
    """成員管理與設定只有管理者能用。"""

    async def test_member_gets_403_on_admin_and_settings(self, site):
        s = await site()
        alice = await s.login("alice", password_of("alice"))
        bob_id = user_id(s.root, "bob")

        calls = [
            ("GET", "/admin/users", None),
            ("POST", "/admin/users", {"username": "eve", "role": "admin"}),
            ("PATCH", f"/admin/users/{bob_id}", {"disabled": True}),
            ("POST", f"/admin/users/{bob_id}/password", {}),
            ("PUT", "/settings/permissions", {"mode": "bypass"}),
            ("PUT", "/settings/model", {"model": "x", "maxTurns": 3}),
            (
                "PUT",
                "/settings/connection",
                {"provider": "endpoint", "model": "x"},
            ),
            ("POST", "/settings/model/test", {}),
            ("POST", "/settings/models/discover", {"provider": "anthropic"}),
            ("PUT", "/settings/models", {"models": ["x"]}),
        ]
        for method, path, body in calls:
            status, _ = await alice.api(method, path, body)
            assert status == 403, path

        assert store_of(s.root).find_user("eve") is None
        assert "bypass" not in (s.root / "harness.yaml").read_text("utf-8")

    async def test_admin_manages_members(self, site):
        s = await site()
        boss = await s.login("boss", password_of("boss"))

        status, issued = await boss.api(
            "POST",
            "/admin/users",
            {"username": "carol", "displayName": "卡蘿", "role": "member"},
        )
        new_id = issued["user"]["id"]
        _, members = await boss.api("GET", "/admin/users")
        _, changed = await boss.api(
            "PATCH", f"/admin/users/{new_id}", {"displayName": "Carol"}
        )
        _, reset = await boss.api("POST", f"/admin/users/{new_id}/password", {})
        duplicate, _ = await boss.api(
            "POST", "/admin/users", {"username": "CAROL", "displayName": "x"}
        )
        invalid, _ = await boss.api(
            "POST", "/admin/users", {"username": "王", "displayName": "x"}
        )

        assert status == 201
        assert issued["user"]["displayName"] == "卡蘿"
        assert issued["user"]["role"] == "member"
        assert issued["user"]["disabled"] is False
        assert issued["user"]["createdAt"] > 0
        assert "lastSeenAt" not in issued["user"]
        assert len(issued["password"]) >= 16
        assert [m["username"] for m in members] == [
            "boss",
            "alice",
            "bob",
            "carol",
        ]
        assert members[0]["lastSeenAt"] > 0
        assert changed["displayName"] == "Carol"
        assert reset["password"] != issued["password"]
        carol = await s.login("carol", reset["password"])
        assert (await carol.api("GET", "/auth/me"))[1]["displayName"] == "Carol"
        assert duplicate == 409
        assert invalid == 400

    async def test_last_admin_is_protected(self, site):
        s = await site()
        boss = await s.login("boss", password_of("boss"))
        boss_id = user_id(s.root, "boss")

        demote, error = await boss.api(
            "PATCH", f"/admin/users/{boss_id}", {"role": "member"}
        )
        disable, _ = await boss.api(
            "PATCH", f"/admin/users/{boss_id}", {"disabled": True}
        )

        assert demote == 400
        assert "管理者" in error["error"]
        assert disable == 400

    async def test_member_sees_no_server_details(self, site, monkeypatch):
        monkeypatch.setenv("SECRET_PROVIDER_API_KEY", "sk-1")
        monkeypatch.setenv("HARNESS_BASE_URL", "http://internal:8000/v1")
        s = await site()
        alice = await s.login("alice", password_of("alice"))
        boss = await s.login("boss", password_of("boss"))

        _, member = await alice.api("GET", "/project")
        _, admin = await boss.api("GET", "/project")

        assert member["root"] == ""
        assert member["model"]["env"]["keyVars"] == []
        assert member["model"]["env"]["baseURL"] is None
        assert member["model"]["connection"]["providers"] == []
        assert "ready" in member["model"]["connection"]
        assert member["auth"]["user"]["username"] == "alice"
        assert admin["root"] == str(s.root.resolve())
        assert "SECRET_PROVIDER_API_KEY" in admin["model"]["env"]["keyVars"]


WRITE = ("write_file", {"path": "a.md", "content": "# A\n"})


class TestIsolation:
    """每個人只看得到、改得到自己的對話與檔案。"""

    async def test_categories_belong_to_one_person(self, site):
        s = await site([conftest.text_response("好")])
        alice = await s.login("alice", password_of("alice"))
        bob = await s.login("bob", password_of("bob"))
        _, created = await alice.api("POST", "/sessions", {"text": "嗨"})
        await alice.wait_idle(created["id"])
        _, mine = await alice.api("POST", "/categories", {"name": "月報"})

        listed_status, listed = await bob.api("GET", "/categories")
        rename, _ = await bob.api(
            "PATCH", f"/categories/{mine['id']}", {"name": "偷改"}
        )
        delete, _ = await bob.api("DELETE", f"/categories/{mine['id']}")
        _, bobs = await bob.api("POST", "/categories", {"name": "月報"})
        # 不能把別人的對話移進自己的專案，也不能批次刪掉別人的對話。
        move, _ = await bob.api(
            "PATCH", f"/sessions/{created['id']}", {"category": bobs["id"]}
        )
        _, bulk = await bob.api(
            "POST",
            "/sessions/bulk",
            {"action": "delete", "ids": [created["id"]]},
        )
        own, _ = await alice.api(
            "PATCH", f"/sessions/{created['id']}", {"category": bobs["id"]}
        )

        assert listed_status == 200
        assert listed == []
        assert rename == 404
        assert delete == 404
        assert move == 404
        assert bulk["done"] == []
        assert own == 404
        _, still = await alice.api("GET", "/categories")
        assert [item["name"] for item in still] == ["月報"]
        alice_id = user_id(s.root, "alice")
        assert (
            s.root / ".harness" / "users" / alice_id / "categories.json"
        ).exists()

    async def test_other_users_sessions_are_not_found(self, site):
        s = await site(
            [conftest.tool_response(WRITE), conftest.text_response("好了")]
        )
        alice = await s.login("alice", password_of("alice"))
        bob = await s.login("bob", password_of("bob"))
        await alice.listen()
        await bob.listen()
        _, created = await alice.api("POST", "/sessions", {"text": "寫 a"})
        sid = created["id"]
        approval = (await alice.wait_for("approval"))["approval"]
        attempts = [
            ("GET", f"/sessions/{sid}", None),
            ("PATCH", f"/sessions/{sid}", {"title": "偷改"}),
            ("DELETE", f"/sessions/{sid}", None),
            ("PUT", f"/sessions/{sid}/options", {"model": None}),
            (
                "PUT",
                f"/sessions/{sid}/feedback",
                {"index": 1, "feedback": {"rating": "up"}},
            ),
            ("POST", f"/sessions/{sid}/messages", {"text": "插話"}),
            ("POST", f"/sessions/{sid}/retry", {}),
            ("POST", f"/sessions/{sid}/stop", {}),
            ("PUT", f"/sessions/{sid}/mode", {"mode": "ask"}),
            (
                "POST",
                f"/sessions/{sid}/approvals/{approval['id']}",
                {"decision": "always"},
            ),
        ]

        for method, path, body in attempts:
            status, _ = await bob.api(method, path, body)
            assert status == 404, f"{method} {path}"
        _, listed = await bob.api("GET", "/sessions")
        _, recent = await bob.api("GET", "/sessions/search?q=")
        _, found = await bob.api("GET", "/sessions/search?q=寫")

        assert listed == []
        assert recent == []
        assert found == []
        # alice 的對話完全沒有被動到：還在等她確認。
        _, detail = await alice.api("GET", f"/sessions/{sid}")
        assert detail["summary"]["status"] == "waiting"
        assert detail["summary"]["title"] == "寫 a"
        assert detail["mode"] is None
        status, _ = await alice.api(
            "POST",
            f"/sessions/{sid}/approvals/{approval['id']}",
            {"decision": "once"},
        )
        assert status == 200
        await alice.wait_for("run_end")
        # 事件只送給對話的擁有者。
        assert any(e.get("sessionId") == sid for e in alice.events)
        leaked = [
            e
            for e in bob.events
            if e.get("sessionId") == sid
            or (e.get("summary") or {}).get("id") == sid
        ]
        assert leaked == []
        assert "allow" not in (s.root / "harness.yaml").read_text("utf-8")

    async def test_files_are_per_user(self, site):
        s = await site(
            [conftest.tool_response(WRITE), conftest.text_response("好了")]
        )
        alice = await s.login("alice", password_of("alice"))
        bob = await s.login("bob", password_of("bob"))
        await alice.listen()
        await alice.api("POST", "/sessions", {"text": "寫 a"})
        approval = (await alice.wait_for("approval"))["approval"]
        await alice.api(
            "POST",
            f"/sessions/{approval['sessionId']}/approvals/{approval['id']}",
            {"decision": "once"},
        )
        await alice.wait_for("run_end")

        mine = await s.client.get(
            "/api/files", params={"path": "a.md"}, headers=alice.headers
        )
        theirs = await s.client.get(
            "/api/files", params={"path": "a.md"}, headers=bob.headers
        )
        _, listed = await bob.api("GET", "/files/list")
        other_id = user_id(s.root, "alice")
        escaped = await s.client.get(
            "/api/files",
            params={"path": f"../{other_id}/a.md"},
            headers=bob.headers,
        )

        assert mine.status == 200
        assert await mine.text() == "# A\n"
        assert (private(s.root, other_id) / "a.md").exists()
        assert theirs.status == 404
        assert listed == []
        assert escaped.status == 403

    async def test_shared_folder_members(self, site):
        extra = (
            "workspaces:\n"
            "  - name: 營收分析\n"
            "    path: workspace/revenue\n"
            "    members: [alice]\n"
            "  - name: 公告\n"
            "    id: notice\n"
            "    path: workspace/notice\n"
            "    writable: false\n"
        )
        s = await site(extra=extra)
        (s.root / "workspace" / "revenue").mkdir(parents=True)
        (s.root / "workspace" / "revenue" / "q3.csv").write_text("1", "utf-8")
        alice = await s.login("alice", password_of("alice"))
        bob = await s.login("bob", password_of("bob"))
        boss = await s.login("boss", password_of("boss"))

        _, alice_info = await alice.api("GET", "/project")
        _, bob_info = await bob.api("GET", "/project")
        _, boss_info = await boss.api("GET", "/project")
        folder = "shared:workspace-revenue"
        allowed, entries = await alice.api(
            "GET", f"/files/list?folder={folder}"
        )
        denied, _ = await bob.api("GET", f"/files/list?folder={folder}")
        denied_file = await s.client.get(
            "/api/files",
            params={"folder": folder, "path": "q3.csv"},
            headers=bob.headers,
        )

        ids = [item["id"] for item in alice_info["folders"]]
        assert ids == ["private", folder, "shared:notice"]
        assert [item["id"] for item in bob_info["folders"]] == [
            "private",
            "shared:notice",
        ]
        assert len(boss_info["folders"]) == 3
        assert alice_info["folders"][2]["writable"] is False
        assert allowed == 200
        assert [item["name"] for item in entries] == ["q3.csv"]
        assert denied == 404
        assert denied_file.status == 404

    async def test_member_cannot_bypass_but_admin_can(self, site):
        s = await site([conftest.text_response("好")] * 2)
        alice = await s.login("alice", password_of("alice"))
        boss = await s.login("boss", password_of("boss"))
        _, mine = await alice.api("POST", "/sessions", {"text": "嗨"})
        _, theirs = await boss.api("POST", "/sessions", {"text": "嗨"})

        refused, error = await alice.api(
            "PUT", f"/sessions/{mine['id']}/mode", {"mode": "bypass"}
        )
        fine, _ = await alice.api(
            "PUT", f"/sessions/{mine['id']}/mode", {"mode": "dontAsk"}
        )
        allowed, _ = await boss.api(
            "PUT", f"/sessions/{theirs['id']}/mode", {"mode": "bypass"}
        )

        assert refused == 400
        assert "管理者" in error["error"]
        assert fine == 200
        assert allowed == 200

    async def test_member_always_applies_only_to_them_in_that_folder(
        self, site
    ):
        script = [
            conftest.tool_response(WRITE),
            conftest.text_response("好"),
            conftest.tool_response(
                ("write_file", {"path": "b.md", "content": "b"})
            ),
            conftest.text_response("好"),
            conftest.tool_response(
                ("write_file", {"path": "c.md", "content": "c"})
            ),
        ]
        s = await site(script)
        alice = await s.login("alice", password_of("alice"))
        bob = await s.login("bob", password_of("bob"))
        await alice.listen()
        await bob.listen()
        config = (s.root / "harness.yaml").read_text("utf-8")

        _, first = await alice.api("POST", "/sessions", {"text": "寫"})
        approval = (await alice.wait_for("approval"))["approval"]
        await alice.api(
            "POST",
            f"/sessions/{first['id']}/approvals/{approval['id']}",
            {"decision": "always"},
        )
        await alice.wait_idle(first["id"])
        # 同一個人、同一個資料夾的新對話不再問。
        _, second = await alice.api("POST", "/sessions", {"text": "再寫"})
        await alice.wait_idle(second["id"])
        # 別人還是要問。
        _, other = await bob.api("POST", "/sessions", {"text": "寫"})
        await bob.wait_for("approval")

        assert [e["type"] for e in alice.events].count("approval") == 1
        assert (s.root / "harness.yaml").read_text("utf-8") == config
        saved = json.loads(
            (
                migration.user_dir(s.root, user_id(s.root, "alice"))
                / "permissions.json"
            ).read_text("utf-8")
        )
        assert saved == {"allow": {"private": ["write_file"]}}
        await bob.api("POST", f"/sessions/{other['id']}/stop", {})


class TestFolders:
    """對話的工作資料夾：工具、上傳與沙盒都只在這裡面。"""

    async def test_options_validate_the_folder(self, site):
        extra = (
            "workspaces:\n"
            "  - name: 營收\n"
            "    path: workspace/revenue\n"
            "    members: [alice]\n"
        )
        s = await site([conftest.text_response("好")], extra=extra)
        alice = await s.login("alice", password_of("alice"))
        bob = await s.login("bob", password_of("bob"))

        _, created = await alice.api(
            "POST",
            "/sessions",
            {"text": "嗨", "options": {"folder": "shared:workspace-revenue"}},
        )
        detail = await alice.wait_idle(created["id"])
        bad, error = await alice.api(
            "POST", "/sessions", {"text": "嗨", "options": {"folder": "nope"}}
        )
        refused, _ = await bob.api(
            "POST",
            "/sessions",
            {"text": "嗨", "options": {"folder": "shared:workspace-revenue"}},
        )
        _, bob_sessions = await bob.api("GET", "/sessions")

        assert detail["options"]["folder"] == "shared:workspace-revenue"
        assert bad == 400
        assert "nope" in error["error"]
        assert refused == 400
        assert bob_sessions == []

    async def test_folder_is_fixed_once_the_conversation_starts(self, site):
        s = await site([conftest.text_response("好")])
        alice = await s.login("alice", password_of("alice"))
        _, created = await alice.api("POST", "/sessions", {"text": "嗨"})
        await alice.wait_idle(created["id"])
        path = f"/sessions/{created['id']}/options"

        moved, error = await alice.api("PUT", path, {"folder": "shared:shared"})
        same, _ = await alice.api("PUT", path, {"folder": "private"})
        _, detail = await alice.api("GET", f"/sessions/{created['id']}")

        # 之前的附件與檔案卡片都相對於原本的資料夾，換了就全部失效。
        assert moved == 400
        assert error["error"] == "資料夾只能在開始對話前選擇"
        assert same == 200
        assert detail["options"]["folder"] == "private"

    async def test_file_tools_stay_inside_the_folder(self, site):
        escape = ("write_file", {"path": "../escape.md", "content": "x"})
        s = await site(
            [
                conftest.tool_response(escape),
                conftest.text_response("寫不了"),
            ],
        )
        alice = await s.login("alice", password_of("alice"))
        boss = await s.login("boss", password_of("boss"))
        await boss.api(
            "PUT",
            "/settings/permissions",
            {"mode": "ask", "allow": ["write_file"]},
        )

        _, created = await alice.api("POST", "/sessions", {"text": "寫"})
        detail = await alice.wait_idle(created["id"])

        tool = next(m for m in detail["messages"] if m["role"] == "tool")
        assert "超出" in tool["content"]
        assert not (s.root / "private" / "escape.md").exists()
        assert detail["summary"]["toolErrors"] == 1

    async def test_read_only_folder_refuses_writes(self, site):
        extra = (
            "workspaces:\n"
            "  - name: 公告\n"
            "    path: workspace/notice\n"
            "    writable: false\n"
        )
        s = await site(
            [conftest.tool_response(WRITE), conftest.text_response("好")],
            extra=extra,
        )
        (s.root / "workspace" / "notice").mkdir(parents=True)
        alice = await s.login("alice", password_of("alice"))
        await alice.listen()

        _, created = await alice.api(
            "POST",
            "/sessions",
            {"text": "寫", "options": {"folder": "shared:workspace-notice"}},
        )
        detail = await alice.wait_idle(created["id"])
        status, error = await self._upload(
            s.client, alice, "shared:workspace-notice"
        )

        tool = next(m for m in detail["messages"] if m["role"] == "tool")
        assert "唯讀" in tool["content"]
        # 唯讀的工具直接拒絕，不會先問使用者。
        assert not [e for e in alice.events if e["type"] == "approval"]
        assert not (s.root / "workspace" / "notice" / "a.md").exists()
        assert status == 403
        assert "唯讀" in error["error"]

    @staticmethod
    async def _upload(client, person, folder=None):
        form = aiohttp.FormData()
        form.add_field("file", b"a,b", filename="sales.csv")
        query = f"?folder={folder}" if folder else ""
        response = await client.post(
            f"/api/uploads{query}",
            data=form,
            headers={**person.headers, "X-Harness-Upload": "1"},
        )
        return response.status, await response.json()

    async def test_uploads_land_in_the_folder(self, site):
        s = await site(
            [conftest.text_response("收到")], extra="sandbox: true\n"
        )
        alice = await s.login("alice", password_of("alice"))
        bob = await s.login("bob", password_of("bob"))

        status, body = await self._upload(s.client, alice)
        shared, shared_body = await self._upload(
            s.client, alice, "shared:shared"
        )
        missing, _ = await self._upload(s.client, alice, "shared:nope")
        _, created = await alice.api(
            "POST",
            "/sessions",
            {"text": "看看", "attachments": [body["files"][0]["path"]]},
        )
        await alice.wait_idle(created["id"])
        # 別人的上傳不能當成附件。
        stolen, _ = await bob.api(
            "POST",
            "/sessions",
            {"text": "看看", "attachments": ["uploads/sales.csv"]},
        )

        alice_id = user_id(s.root, "alice")
        assert status == 201
        assert body["files"][0]["path"] == "uploads/sales.csv"
        assert (private(s.root, alice_id) / "uploads" / "sales.csv").exists()
        assert shared == 201
        assert shared_body["files"][0]["path"] == "uploads/sales.csv"
        assert (s.root / "shared" / "uploads" / "sales.csv").exists()
        assert missing == 404
        user = s.model.requests[0]["messages"][-1]["content"]
        assert user == (
            "看看\n\n[附件] uploads/sales.csv"
            "（run_python 裡的路徑：/workspace/uploads/sales.csv）"
        )
        assert stolen == 400

    async def test_sandbox_mounts_the_session_folder(self, site, monkeypatch):
        runs = []

        async def fake_execute(self, argv, stdin, workdir, timeout):
            runs.append((self, argv, workdir))
            (self.workspace / "chart.png").write_bytes(b"png")
            return sandbox_lib.Result(0, "ok", "")

        monkeypatch.setattr(sandbox_lib.Sandbox, "_execute", fake_execute)
        extra = (
            "sandbox: true\n"
            "workspaces:\n"
            "  - name: 公告\n"
            "    path: workspace/notice\n"
            "    writable: false\n"
        )
        call = ("run_python", {"code": "print(1)"})
        s = await site(
            [
                conftest.tool_response(call),
                conftest.text_response("好"),
                conftest.tool_response(call),
                conftest.text_response("好"),
            ],
            extra=extra,
            agent=SANDBOX_AGENT,
        )
        alice = await s.login("alice", password_of("alice"))

        _, first = await alice.api("POST", "/sessions", {"text": "畫圖"})
        detail = await alice.wait_idle(first["id"])
        _, second = await alice.api(
            "POST",
            "/sessions",
            {"text": "畫圖", "options": {"folder": "shared:workspace-notice"}},
        )
        await alice.wait_idle(second["id"])

        alice_id = user_id(s.root, "alice")
        box, argv, _ = runs[0]
        assert box.workspace == private(s.root, alice_id).resolve()
        assert argv == ("python", "-")
        mount = box.docker_args("x")
        assert f"{box.workspace}:/workspace" in mount
        tool = next(m for m in detail["messages"] if m["role"] == "tool")
        assert f"{sandbox_lib.PRODUCED_MARKER}chart.png" in tool["content"]
        notice, _, _ = runs[1]
        assert notice.read_only
        assert notice.workspace == (s.root / "workspace" / "notice").resolve()
        assert any(
            arg.endswith("/workspace:ro") for arg in notice.docker_args("x")
        )

    def test_volume_mount_uses_the_folder_subpath(self, tmp_path):
        box = sandbox_lib.Sandbox(
            tmp_path / "workspace", volume="harness-data", volume_subpath="w"
        )

        scoped = box.for_folder(tmp_path, tmp_path / "private" / "u1", True)

        assert scoped.volume_subpath == "private/u1"
        [mount] = [a for a in scoped.docker_args("x") if "type=volume" in a]
        assert "volume-subpath=private/u1" in mount
        assert mount.endswith(",readonly")
        with pytest.raises(ValueError, match="volume"):
            box.for_folder(tmp_path, tmp_path.parent / "elsewhere")


class TestMemberCommands:
    """成員的 run_command 不在伺服器上執行。"""

    async def test_member_command_runs_in_the_sandbox(self, site, monkeypatch):
        runs = []

        async def fake_execute(self, argv, stdin, workdir, timeout):
            runs.append((self.workspace, argv, workdir, timeout))
            return sandbox_lib.Result(0, "hello\n", "")

        async def never(*args, **kwargs):
            raise AssertionError("成員的指令不能在伺服器上執行")

        monkeypatch.setattr(sandbox_lib.Sandbox, "_execute", fake_execute)
        monkeypatch.setattr(shell, "execute", never)
        call = ("run_command", {"command": "echo hello", "cwd": "data"})
        s = await site(
            [conftest.tool_response(call), conftest.text_response("好")],
            extra="sandbox: true\n",
            agent=SANDBOX_AGENT,
        )
        alice = await s.login("alice", password_of("alice"))
        await alice.listen()

        _, created = await alice.api("POST", "/sessions", {"text": "跑"})
        approval = (await alice.wait_for("approval"))["approval"]
        await alice.api(
            "POST",
            f"/sessions/{created['id']}/approvals/{approval['id']}",
            {"decision": "once"},
        )
        detail = await alice.wait_idle(created["id"])

        alice_id = user_id(s.root, "alice")
        [(workspace, argv, workdir, timeout)] = runs
        assert workspace == private(s.root, alice_id).resolve()
        assert argv == ("bash", "-lc", "echo hello")
        assert workdir == "/workspace/data"
        assert timeout == shell.DEFAULT_TIMEOUT_SECONDS
        tool = next(m for m in detail["messages"] if m["role"] == "tool")
        assert "hello" in tool["content"]
        assert "結束代碼：0" in tool["content"]

    async def test_without_sandbox_members_lose_run_command(self, site):
        s = await site([conftest.text_response("好")] * 2)
        alice = await s.login("alice", password_of("alice"))
        boss = await s.login("boss", password_of("boss"))

        _, member_info = await alice.api("GET", "/project")
        _, admin_info = await boss.api("GET", "/project")
        _, mine = await alice.api("POST", "/sessions", {"text": "嗨"})
        await alice.wait_idle(mine["id"])
        _, theirs = await boss.api("POST", "/sessions", {"text": "嗨"})
        await boss.wait_idle(theirs["id"])
        refused, _ = await alice.api(
            "PUT", f"/sessions/{mine['id']}/options", {"tools": ["run_command"]}
        )

        def names(request):
            return [item["function"]["name"] for item in request["tools"]]

        assert "run_command" not in member_info["agents"][0]["tools"]
        shell_set = next(
            t for t in member_info["toolsets"] if t["name"] == "shell"
        )
        assert shell_set["tools"] == []
        assert "run_command" in admin_info["agents"][0]["tools"]
        assert "run_command" not in names(s.model.requests[0])
        assert "read_file" in names(s.model.requests[0])
        assert "run_command" in names(s.model.requests[1])
        assert refused == 400

    async def test_admin_commands_in_shared_folders_use_the_sandbox(
        self, site, monkeypatch
    ):
        # 成員可以在共用資料夾放檔案，管理者在那裡的指令也不在伺服器上跑。
        runs = []

        async def fake_execute(self, argv, stdin, workdir, timeout):
            runs.append(self.workspace)
            return sandbox_lib.Result(0, "ok\n", "")

        async def never(*args, **kwargs):
            raise AssertionError("共用資料夾的指令不能在伺服器上執行")

        monkeypatch.setattr(sandbox_lib.Sandbox, "_execute", fake_execute)
        monkeypatch.setattr(shell, "execute", never)
        call = ("run_command", {"command": "git status"})
        s = await site(
            [conftest.tool_response(call), conftest.text_response("好")],
            extra="sandbox: true\n",
            agent=SANDBOX_AGENT,
        )
        boss = await s.login("boss", password_of("boss"))
        await boss.listen()

        _, created = await boss.api(
            "POST",
            "/sessions",
            {"text": "跑", "options": {"folder": "shared:shared"}},
        )
        approval = (await boss.wait_for("approval"))["approval"]
        await boss.api(
            "POST",
            f"/sessions/{created['id']}/approvals/{approval['id']}",
            {"decision": "once"},
        )
        await boss.wait_idle(created["id"])

        assert runs == [(s.root / "shared").resolve()]


class TestWorkspacesConfig:
    """harness.yaml 的 workspaces。"""

    @pytest.mark.parametrize(
        "path",
        [
            ".",
            ".harness",
            ".harness/x",
            "private",
            "private/u",
            "Private/u",
            "agents",
            "Agents",
            "tools/a",
            "skills",
            "harness.yaml",
            "..",
            "/",
        ],
    )
    def test_overlapping_paths_are_refused(self, tmp_path, path):
        root = make_project(
            tmp_path / "c", f"workspaces:\n  - name: x\n    path: {path!r}\n"
        )

        with (
            project_lib.load_project(root) as loaded,
            pytest.raises(ValueError, match="workspaces"),
        ):
            server_lib.create_app(loaded)

    @pytest.mark.parametrize(
        "entries",
        [
            "  - name: a\n    path: workspace\n"
            "  - name: b\n    path: workspace/x\n",
            "  - name: a\n    path: data\n    members: bob\n",
            "  - name: a\n    path: data\n    writable: maybe\n",
            "  - name: a\n    path: data\n    color: red\n",
            "  - path: data\n",
            "  - name: a\n    path: x\n  - name: b\n    path: y\n    id: x\n",
        ],
    )
    def test_bad_entries_are_refused(self, tmp_path, entries):
        root = make_project(tmp_path / "c", f"workspaces:\n{entries}")

        with (
            project_lib.load_project(root) as loaded,
            pytest.raises(ValueError, match="workspaces"),
        ):
            server_lib.create_app(loaded)

    def test_absolute_path_outside_the_project_is_allowed(self, tmp_path):
        outside = tmp_path / "mounted"
        root = make_project(
            tmp_path / "c",
            f"workspaces:\n  - name: 掛載\n    path: {str(outside)!r}\n",
        )

        with project_lib.load_project(root) as loaded:
            folders = folders_lib.Folders.for_project(loaded)

        [folder] = folders.shared
        assert folder.path == outside.resolve()
        assert (
            folder.id == f"shared:{str(outside).strip('/').replace('/', '-')}"
        )

    def test_default_shared_folder_is_a_fresh_shared_dir(self, tmp_path):
        root = make_project(tmp_path / "c", "sandbox:\n  workspace: data\n")

        with project_lib.load_project(root) as loaded:
            [folder] = folders_lib.Folders.for_project(loaded).shared

        # 不是舊版的 workspace/（或 sandbox.workspace）：舊檔案不會自動
        # 分享給所有成員。
        assert folder.path == (root / "shared").resolve()
        assert folder.id == "shared:shared"
        assert folder.name == "共用"


class TestUserCommand:
    """pyharness user。"""

    def test_add_list_passwd_disable_enable(self, tmp_path, capsys):
        root = make_project(tmp_path / "u")
        project = ["-p", str(root)]

        assert cli.main(["user", "add", "boss", "--admin", *project]) == 0
        added = capsys.readouterr().out
        assert (
            cli.main(["user", "add", "alice", "--name", "愛麗絲", *project])
            == 0
        )
        capsys.readouterr()
        assert cli.main(["user", "list", *project]) == 0
        listed = capsys.readouterr().out
        assert cli.main(["user", "passwd", "alice", *project]) == 0
        reset = capsys.readouterr().out
        assert cli.main(["user", "disable", "alice", *project]) == 0
        disabled = store_of(root).find_user("alice").disabled
        assert cli.main(["user", "enable", "alice", *project]) == 0

        store = store_of(root)
        boss_password = added.split("密碼：")[1].split()[0]
        assert store.authenticate("boss", boss_password).is_admin
        assert "boss" in listed
        assert "愛麗絲" in listed
        assert "成員" in listed
        new_password = reset.split("新密碼：")[1].split()[0]
        assert store.authenticate("alice", new_password) is not None
        assert disabled is True
        assert store.find_user("alice").disabled is False

    def test_errors(self, tmp_path, capsys):
        root = make_project(tmp_path / "u")
        project = ["-p", str(root)]
        cli.main(["user", "add", "boss", "--admin", *project])

        duplicate = cli.main(["user", "add", "BOSS", *project])
        missing = cli.main(["user", "passwd", "nobody", *project])
        last = cli.main(["user", "disable", "boss", *project])
        nowhere = cli.main(["user", "list", "-p", str(tmp_path / "none")])

        assert (duplicate, missing, last, nowhere) == (1, 1, 1, 1)
        assert "harness.yaml" in capsys.readouterr().err


async def _upload(client, person, name="evil.py", folder=None):
    form = aiohttp.FormData()
    form.add_field("file", b"print('pwned')", filename=name)
    query = f"?folder={folder}" if folder else ""
    response = await client.post(
        f"/api/uploads{query}",
        data=form,
        headers={**person.headers, "X-Harness-Upload": "1"},
    )
    return response.status, await response.json()


def _app(s: Site):
    return s.client.server.app


class TestHardening:
    """安全審查發現的問題（捷徑、登入限制、反向代理、失效的登入）。"""

    @pytest.mark.parametrize("planted", ["uploads", "uploads/x"])
    async def test_upload_never_follows_a_planted_link(self, site, planted):
        s = await site(extra="sandbox: true\n")
        (s.root / "tools").mkdir(exist_ok=True)
        alice = await s.login("alice", password_of("alice"))
        folder = private(s.root, user_id(s.root, "alice"))
        (folder / "uploads").mkdir(parents=True)
        if planted == "uploads":
            # 沙盒裡 `rm -rf uploads && ln -s ../../tools uploads` 的結果。
            (folder / "uploads").rmdir()
            os.symlink("../../tools", folder / "uploads")
            status, body = await _upload(s.client, alice)
            assert status == 403
            assert "捷徑" in body["error"]
        else:
            # 同名的檔案是捷徑：換一個名字，不會寫到捷徑指的位置。
            os.symlink("../../../tools/evil.py", folder / "uploads/evil.py")
            status, body = await _upload(s.client, alice)
            assert status == 201
            assert body["files"][0]["path"] == "uploads/evil-2.py"

        assert not (s.root / "tools" / "evil.py").exists()

    async def test_file_api_streams_ranges_from_the_opened_file(self, site):
        s = await site()
        alice = await s.login("alice", password_of("alice"))
        folder = private(s.root, user_id(s.root, "alice"))
        folder.mkdir(parents=True)
        (folder / "a.txt").write_bytes(b"0123456789")

        async def get(headers=None):
            response = await s.client.get(
                "/api/files",
                params={"path": "a.txt"},
                headers={**alice.headers, **(headers or {})},
            )
            return response, await response.read()

        whole, body = await get()
        part, middle = await get({"Range": "bytes=2-4"})
        tail, end = await get({"Range": "bytes=-3"})
        bad, _ = await get({"Range": "bytes=50-"})

        assert whole.status == 200
        assert body == b"0123456789"
        assert whole.headers["Content-Length"] == "10"
        assert whole.headers["X-Content-Type-Options"] == "nosniff"
        assert part.status == 206
        assert middle == b"234"
        assert part.headers["Content-Range"] == "bytes 2-4/10"
        assert (tail.status, end) == (206, b"789")
        assert bad.status == 416

    def test_file_api_never_serves_a_swapped_in_link(self, tmp_path):
        """沙盒一直把資料夾換成指向金鑰的捷徑：檔案 API 讀不到金鑰。"""
        secrets = tmp_path / ".harness" / "secrets"
        secrets.mkdir(parents=True)
        (secrets / "env.json").write_text("SECRET", encoding="utf-8")
        folder = tmp_path / "private" / "alice"
        folder.mkdir(parents=True)
        real = folder / "d"
        real.mkdir()
        (real / "env.json").write_text("benign", encoding="utf-8")
        stop = threading.Event()

        def swap():
            while not stop.is_set():
                os.rename(real, folder / "hold")
                os.symlink("../../.harness/secrets", real)
                os.unlink(real)
                os.rename(folder / "hold", real)

        worker = threading.Thread(target=swap)
        worker.start()
        leaks = reads = 0
        deadline = time.monotonic() + 1
        try:
            while time.monotonic() < deadline:
                try:
                    fd, _ = files_lib.open_file(folder, "d/env.json")
                except files_lib.FileError:
                    continue
                with os.fdopen(fd, "rb") as file:
                    leaks += file.read() == b"SECRET"
                reads += 1
        finally:
            stop.set()
            worker.join()

        assert reads > 0
        assert leaks == 0

    async def test_parallel_wrong_passwords_are_throttled(self, site):
        s = await site()

        async def attempt(index):
            response = await s.client.post(
                "/api/auth/login",
                json={"username": "alice", "password": f"wrong-{index}"},
            )
            return response.status

        statuses = await asyncio.gather(*(attempt(i) for i in range(30)))

        assert statuses.count(401) == accounts_lib.MAX_FAILURES
        assert statuses.count(429) == 30 - accounts_lib.MAX_FAILURES

    @pytest.mark.parametrize("username", ["x" * 100_000, "王小明", "a b", "a"])
    async def test_malformed_usernames_skip_the_throttle(
        self, site, monkeypatch, username
    ):
        s = await site()
        calls = []
        monkeypatch.setattr(
            accounts_lib.AccountStore,
            "authenticate",
            lambda *args: calls.append(args),
        )

        status, body = await s.anonymous().api(
            "POST", "/auth/login", {"username": username, "password": "x"}
        )

        throttle = _app(s)[server_lib._THROTTLE]  # noqa: SLF001
        assert status == 401
        assert body == {"error": "帳號或密碼不正確"}
        assert calls == []
        assert throttle._names == {}  # noqa: SLF001

    async def test_lockout_uses_the_real_client_behind_a_trusted_proxy(
        self, site
    ):
        s = await site(trusted_proxies=["127.0.0.1"])
        wrong = {"username": "alice", "password": "wrong-password"}

        statuses = []
        for _ in range(accounts_lib.MAX_FAILURES + 1):
            response = await s.client.post(
                "/api/auth/login",
                json=wrong,
                headers={"X-Forwarded-For": "198.51.100.3"},
            )
            statuses.append(response.status)
        # 經過代理伺服器時看的是真正的來源：別的地方的使用者不會被一起擋下。
        other = await s.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": password_of("alice")},
            headers={"X-Forwarded-For": "198.51.100.4"},
        )

        assert statuses[-1] == 429
        assert other.status == 200
        throttle = _app(s)[server_lib._THROTTLE]  # noqa: SLF001
        assert "198.51.100.3" in throttle._addresses  # noqa: SLF001

    @pytest.mark.parametrize(
        ("remote", "forwarded", "expected"),
        [
            # 沒有信任的反向代理：標頭一律不採信。
            ("10.0.0.2", "1.1.1.1", "10.0.0.2"),
            # 最左邊的是用戶端自己寫的，取最右邊不是反向代理的那一站。
            ("127.0.0.1", "6.6.6.6, 203.0.113.7", "203.0.113.7"),
            ("127.0.0.1", "6.6.6.6, 203.0.113.7, 10.0.0.9", "203.0.113.7"),
            ("127.0.0.1", "", "127.0.0.1"),
        ],
    )
    def test_client_address(self, remote, forwarded, expected):
        transport = mock.Mock()
        transport.get_extra_info.side_effect = lambda name, default=None: (
            (remote, 1234) if name == "peername" else default
        )
        app = aiohttp.web.Application()
        app[server_lib._PROXIES] = server_lib.parse_trusted_proxies(  # noqa: SLF001
            ["127.0.0.1", "10.0.0.0/24"] if remote == "127.0.0.1" else []
        )
        headers = {"X-Forwarded-For": forwarded} if forwarded else {}
        request = make_mocked_request(
            "POST", "/", headers=headers, app=app, transport=transport
        )

        assert server_lib.client_address(request) == expected

    def test_bad_trusted_proxy_is_refused(self):
        with pytest.raises(ValueError, match="反向代理"):
            server_lib.parse_trusted_proxies(["10.0.0.0/8, nope"])

    @pytest.mark.parametrize("action", ["logout", "disable", "password"])
    async def test_event_stream_ends_when_the_login_does(
        self, site, monkeypatch, action
    ):
        monkeypatch.setattr(server_lib, "_HEARTBEAT", 0.05)
        s = await site()
        alice = await s.login("alice", password_of("alice"))
        boss = await s.login("boss", password_of("boss"))
        response = await s.client.get("/api/events", headers=alice.headers)
        assert response.status == 200

        if action == "logout":
            await alice.api("POST", "/auth/logout", {})
        elif action == "disable":
            alice_id = user_id(s.root, "alice")
            await boss.api(
                "PATCH", f"/admin/users/{alice_id}", {"disabled": True}
            )
        else:
            store_of(s.root).set_password(
                user_id(s.root, "alice"), "another-password"
            )

        async with asyncio.timeout(3):
            body = await response.read()
        assert b"hello" in body

    @pytest.mark.parametrize("change", [{"disabled": True}, {"role": "member"}])
    async def test_account_changes_stop_running_turns(self, site, change):
        s = await site(
            [conftest.tool_response(WRITE), conftest.text_response("好")],
            users=(("boss", "admin"), ("carol", "admin")),
        )
        carol = await s.login("carol", password_of("carol"))
        boss = await s.login("boss", password_of("boss"))
        await carol.listen()
        _, created = await carol.api("POST", "/sessions", {"text": "寫"})
        await carol.wait_for("approval")

        status, _ = await boss.api(
            "PATCH", f"/admin/users/{user_id(s.root, 'carol')}", change
        )
        workspace = _app(s)[server_lib._WORKSPACE]  # noqa: SLF001
        state = workspace._states[created["id"]]  # noqa: SLF001
        async with asyncio.timeout(3):
            while state.task is not None:
                await asyncio.sleep(0.01)

        assert status == 200
        assert state.status == "stopped"
        assert state.notice == workspace_lib.ACCOUNT_CHANGED_NOTICE
        assert not (private(s.root, user_id(s.root, "carol")) / "a.md").exists()

    def test_permissions_read_the_role_live(self, tmp_path):
        root = make_project(tmp_path / "p")
        boss, _ = store_of(root).create_user("boss", None, "admin", "pw")
        current = {"user": boss}
        with project_lib.load_project(root) as loaded:
            ws = workspace_lib.Workspace(
                loaded,
                auth_mode="accounts",
                users=lambda _: current["user"],
            )
            state = ws._create(boss, "main")  # noqa: SLF001
            state.run_user = boss
            state.run_folder = ws.folder(boss, None)
            state.mode = "bypass"
            permissions = ws._permissions_for(state)  # noqa: SLF001

            before = permissions.mode
            current["user"] = dataclasses.replace(boss, role="member")
            demoted = permissions.mode
            current["user"] = None
            removed = permissions.mode

        assert before == "bypass"
        assert demoted == "ask"
        assert removed == "ask"

    def test_disabled_accounts_cannot_run_any_tool(self, tmp_path):
        # 從指令列停用（pyharness user disable）不會經過網頁：每次執行
        # 工具前都重新確認帳號。
        root = make_project(tmp_path / "p")
        carol, _ = store_of(root).create_user("carol", None, "member", "pw")
        current = {"user": carol}
        read = tools_lib.Tool(
            name="read_file",
            description="",
            parameters={},
            func=lambda: None,
        )
        with project_lib.load_project(root) as loaded:
            ws = workspace_lib.Workspace(
                loaded,
                auth_mode="accounts",
                users=lambda _: current["user"],
            )
            state = ws._create(carol, "main")  # noqa: SLF001
            state.run_user = carol
            state.run_folder = ws.folder(carol, None)
            permissions = ws._permissions_for(state)  # noqa: SLF001

            before = permissions_lib.check(permissions, read)
            current["user"] = dataclasses.replace(carol, disabled=True)
            disabled = permissions_lib.check(permissions, read)

        assert before == "allow"
        assert disabled == "deny"

    async def test_resetting_a_password_stops_running_turns(self, site):
        s = await site(
            [conftest.tool_response(WRITE), conftest.text_response("好")],
            users=(("boss", "admin"), ("carol", "admin")),
        )
        carol = await s.login("carol", password_of("carol"))
        boss = await s.login("boss", password_of("boss"))
        await carol.listen()
        _, created = await carol.api("POST", "/sessions", {"text": "寫"})
        await carol.wait_for("approval")

        status, _ = await boss.api(
            "POST", f"/admin/users/{user_id(s.root, 'carol')}/password", {}
        )
        workspace = _app(s)[server_lib._WORKSPACE]  # noqa: SLF001
        state = workspace._states[created["id"]]  # noqa: SLF001
        async with asyncio.timeout(3):
            while state.task is not None:
                await asyncio.sleep(0.01)

        assert status == 200
        assert state.status == "stopped"
        assert state.notice == workspace_lib.ACCOUNT_CHANGED_NOTICE

    @pytest.mark.parametrize(
        ("username", "config", "guarded"),
        [
            ("alice", "", True),
            ("boss", "", True),
            ("alice", "web:\n  allowPrivateNetworks: true\n", True),
            ("boss", "web:\n  allowPrivateNetworks: true\n", False),
        ],
    )
    def test_web_fetch_is_guarded_in_accounts_mode(
        self, tmp_path, username, config, guarded
    ):
        agent = AGENT.replace("  - shell\n", "  - shell\n  - web\n")
        root = make_project(tmp_path / "w", config, agent)
        store = store_of(root)
        store.create_user("boss", None, "admin", "pw")
        store.create_user("alice", None, "member", "pw")
        user = store.find_user(username)
        with project_lib.load_project(root) as loaded:
            ws = workspace_lib.Workspace(
                loaded, auth_mode="accounts", users=store.get_user
            )
            agent = ws._scoped_agent(  # noqa: SLF001
                loaded.agents["main"], user, ws.folder(user, None)
            )
            local = workspace_lib.Workspace(loaded)
            plain = local._run_agent  # noqa: SLF001

        [fetch] = [item for item in agent.tools if item.name == "web_fetch"]
        assert (fetch is not web_tools.web_fetch) is guarded
        assert plain is not None

    async def test_admin_resetting_own_password_stays_logged_in(self, site):
        s = await site()
        boss = await s.login("boss", password_of("boss"))
        boss_id = user_id(s.root, "boss")

        response = await s.client.post(
            f"/api/admin/users/{boss_id}/password",
            json={},
            headers=boss.headers,
        )
        old, _ = await boss.api("GET", "/auth/me")
        fresh = response.cookies[server_lib.COOKIE].value
        other = await s.client.post(
            f"/api/admin/users/{user_id(s.root, 'alice')}/password",
            json={},
            headers={"Cookie": f"{server_lib.COOKIE}={fresh}"},
        )

        assert response.status == 200
        assert old == 401
        assert store_of(s.root).resolve_login(fresh).id == boss_id
        # 重設別人的密碼不會換掉自己的 cookie。
        assert other.status == 200
        assert server_lib.COOKIE not in other.cookies
