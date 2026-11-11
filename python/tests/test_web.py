"""網頁版（pyharness web）的測試。

API 與事件格式要和 TypeScript 版完全相同，兩個版本共用同一份前端，
所以這裡的測試對照 tests/web.test.ts。
"""

import asyncio
import base64
import json
import os
import pathlib

import aiohttp
import pytest
import yaml
from aiohttp.test_utils import TestClient
from aiohttp.test_utils import TestServer

from harness import cli
from harness import credentials
from harness import llm
from harness import project as project_lib
from harness import providers
from harness import sandbox as sandbox_lib
from harness.web import config as config_lib
from harness.web import server as server_lib
from harness.web import uploads as uploads_lib
from tests import conftest

CONFIG = """# 測試專案
name: web_demo
defaultAgent: main

permissions:
  # 預設先問
  mode: ask
"""

AGENT = """---
name: main
description: 測試用的助手
tools:
  - files
---
你是助手。
"""

WRITE_CALL = ("write_file", {"path": "reports/a.md", "content": "# 報告\n"})


def make_project(root: pathlib.Path) -> pathlib.Path:
    """建立只用內建檔案工具的專案。"""
    (root / "agents").mkdir(parents=True)
    (root / "harness.yaml").write_text(CONFIG, encoding="utf-8")
    (root / "agents" / "main.md").write_text(AGENT, encoding="utf-8")
    return root


class Harness:
    """啟動好的伺服器、它的 SSE 事件，以及呼叫 API 的方法。"""

    def __init__(self, client: TestClient, root: pathlib.Path, model) -> None:
        self.client = client
        self.root = root
        self.model = model
        self.events: list[dict] = []
        self._changed = asyncio.Event()
        self._reader: asyncio.Task | None = None

    async def listen(self) -> None:
        response = await self.client.get("/api/events")

        async def read() -> None:
            buffer = ""
            async for chunk in response.content.iter_any():
                buffer += chunk.decode()
                while "\n\n" in buffer:
                    block, buffer = buffer.split("\n\n", 1)
                    if block.startswith("data: "):
                        self.events.append(json.loads(block[6:]))
                        self._changed.set()

        self._reader = asyncio.create_task(read())
        await self.wait_for("hello")

    async def wait_for(self, kind: str, predicate=lambda event: True):
        """等到出現符合條件的事件。"""
        async with asyncio.timeout(3):
            while True:
                for event in self.events:
                    if event["type"] == kind and predicate(event):
                        return event
                self._changed.clear()
                await self._changed.wait()

    async def api(self, method: str, path: str, body=None, headers=None):
        response = await self.client.request(
            method,
            f"/api{path}",
            json=body,
            headers=headers or {},
        )
        return response.status, await response.json()

    async def decide(self, approval: dict, decision: str) -> int:
        status, _ = await self.api(
            "POST",
            f"/sessions/{approval['sessionId']}/approvals/{approval['id']}",
            {"decision": decision},
        )
        return status


@pytest.fixture
async def start(tmp_path, fake_model):
    """建立專案並啟動伺服器；回傳啟動用的函式。"""
    started = []

    async def launch(script=None, model=None, static_dir=None):
        root = make_project(tmp_path / f"p{len(started)}")
        loaded = project_lib.load_project(root)
        model = model or fake_model(script or [])
        app = server_lib.create_app(
            loaded, model_fn=model, static_dir=static_dir
        )
        client = TestClient(TestServer(app))
        await client.start_server()
        harness = Harness(client, root, model)
        started.append((harness, loaded))
        await harness.listen()
        return harness

    yield launch
    for harness, loaded in started:
        if harness._reader is not None:
            harness._reader.cancel()
        await harness.client.close()
        loaded.close()


async def test_message_creates_session_and_streams_reply(start):
    h = await start([conftest.text_response("你好")])

    status, created = await h.api("POST", "/sessions", {"text": "嗨\n第二行"})
    end = await h.wait_for("run_end")

    assert status == 201
    assert created["title"] == "嗨"
    assert end["stopReason"] == "end_turn"
    assert any(e["type"] == "text_delta" for e in h.events)
    _, detail = await h.api("GET", f"/sessions/{created['id']}")
    assert detail["summary"]["status"] == "done"
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    _, listed = await h.api("GET", "/sessions")
    assert [item["id"] for item in listed] == [created["id"]]


async def test_follow_up_message_continues_the_session(start):
    h = await start(
        [conftest.text_response("一"), conftest.text_response("二")]
    )
    _, created = await h.api("POST", "/sessions", {"text": "第一題"})
    await h.wait_for("run_end")

    await h.api(
        "POST", f"/sessions/{created['id']}/messages", {"text": "第二題"}
    )
    await h.wait_for("run_end", lambda _: len(h.model.requests) == 2)

    contents = [m["content"] for m in h.model.requests[1]["messages"]]
    assert contents == ["你是助手。", "第一題", "一", "第二題"]


async def test_empty_message_is_rejected(start):
    h = await start()

    status, body = await h.api("POST", "/sessions", {"text": "  "})

    assert status == 400
    assert "空" in body["error"]


async def test_tool_needing_approval_waits_and_shows_file_preview(start):
    h = await start(
        [conftest.tool_response(WRITE_CALL), conftest.text_response("好了")]
    )
    _, created = await h.api("POST", "/sessions", {"text": "寫報告"})

    approval = (await h.wait_for("approval"))["approval"]
    _, waiting = await h.api("GET", f"/sessions/{created['id']}")
    assert waiting["summary"]["status"] == "waiting"
    assert waiting["summary"]["approval"]["id"] == approval["id"]
    assert approval["preview"] == {
        "path": "reports/a.md",
        "before": None,
        "after": "# 報告\n",
    }

    assert await h.decide(approval, "once") == 200
    await h.wait_for("run_end")

    written = (h.root / "reports" / "a.md").read_text(encoding="utf-8")
    assert written == "# 報告\n"


async def test_allow_always_adds_rule_and_keeps_comments(start):
    h = await start(
        [conftest.tool_response(WRITE_CALL), conftest.text_response("好了")]
    )
    await h.api("POST", "/sessions", {"text": "寫報告"})

    approval = (await h.wait_for("approval"))["approval"]
    await h.decide(approval, "always")
    await h.wait_for("run_end")

    config = (h.root / "harness.yaml").read_text(encoding="utf-8")
    assert 'allow: ["write_file"]' in config
    assert "# 預設先問" in config
    _, info = await h.api("GET", "/project")
    assert info["permissions"]["allow"] == ["write_file"]


async def test_denied_tool_is_not_run_and_model_is_told(start):
    h = await start(
        [conftest.tool_response(WRITE_CALL), conftest.text_response("不寫")]
    )
    await h.api("POST", "/sessions", {"text": "寫報告"})

    approval = (await h.wait_for("approval"))["approval"]
    await h.decide(approval, "deny")
    result = await h.wait_for("tool_result")
    await h.wait_for("run_end")

    assert result["result"]["isError"] is True
    assert "沒有同意" in h.model.requests[1]["messages"][-1]["content"]
    assert not (h.root / "reports" / "a.md").exists()


async def test_stop_while_waiting_marks_session_stopped(start):
    h = await start([conftest.tool_response(WRITE_CALL)])
    _, created = await h.api("POST", "/sessions", {"text": "寫報告"})
    await h.wait_for("approval")

    await h.api("POST", f"/sessions/{created['id']}/stop", {})
    await h.wait_for("run_stopped")

    _, detail = await h.api("GET", f"/sessions/{created['id']}")
    assert detail["summary"]["status"] == "stopped"
    assert detail["approvals"] == []


async def test_resolved_approval_cannot_be_answered_again(start):
    h = await start(
        [conftest.tool_response(WRITE_CALL), conftest.text_response("好")]
    )
    await h.api("POST", "/sessions", {"text": "寫報告"})
    approval = (await h.wait_for("approval"))["approval"]
    await h.decide(approval, "once")

    assert await h.decide(approval, "once") == 404


async def test_model_error_then_retry_does_not_repeat_user_message(start):
    calls = []

    async def flaky(model, messages, tools, params):
        calls.append([m["role"] for m in messages])
        if len(calls) == 1:
            raise ConnectionError("無法連線到模型端點")
        yield conftest.text_response("回來了")

    h = await start(model=flaky)
    _, created = await h.api("POST", "/sessions", {"text": "嗨"})

    failed = await h.wait_for("run_error")
    assert "無法連線" in failed["message"]
    _, errored = await h.api("GET", f"/sessions/{created['id']}")
    assert errored["summary"]["status"] == "error"

    status, _ = await h.api("POST", f"/sessions/{created['id']}/retry", {})
    await h.wait_for("run_end")

    assert status == 202
    assert calls[1].count("user") == 1
    _, detail = await h.api("GET", f"/sessions/{created['id']}")
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]


async def test_tool_errors_are_counted(start):
    h = await start(
        [
            conftest.tool_response(("read_file", {"path": "nope.txt"})),
            conftest.text_response("找不到"),
        ]
    )
    _, created = await h.api("POST", "/sessions", {"text": "讀檔"})
    await h.wait_for("run_end")

    _, detail = await h.api("GET", f"/sessions/{created['id']}")
    assert detail["summary"]["toolErrors"] == 1


async def test_project_info_lists_agents_and_toolsets(start):
    h = await start()

    _, info = await h.api("GET", "/project")

    assert info["name"] == "web_demo"
    assert info["runtime"] == "python"
    assert info["agents"][0]["name"] == "main"
    assert info["agents"][0]["maxTurns"] == 20
    files = next(item for item in info["toolsets"] if item["name"] == "files")
    assert files["builtin"] is True
    write = next(t for t in files["tools"] if t["name"] == "write_file")
    assert write["needsApproval"] is True


async def test_save_permissions_drops_empty_rules(start):
    h = await start()

    status, _ = await h.api(
        "PUT",
        "/settings/permissions",
        {"mode": "dontAsk", "allow": [], "ask": ["send_*"], "deny": ["*_x"]},
    )

    assert status == 200
    config = yaml.safe_load((h.root / "harness.yaml").read_text("utf-8"))
    assert config["permissions"] == {
        "mode": "dontAsk",
        "ask": ["send_*"],
        "deny": ["*_x"],
    }


async def test_unknown_permission_mode_is_rejected(start):
    h = await start()

    status, _ = await h.api(
        "PUT",
        "/settings/permissions",
        {"mode": "yolo", "allow": [], "ask": [], "deny": []},
    )

    assert status == 400


async def test_saved_model_settings_apply_to_next_run(start):
    h = await start([conftest.text_response("好")])

    await h.api("PUT", "/settings/model", {"model": "my-model", "maxTurns": 5})
    await h.api("POST", "/sessions", {"text": "嗨"})
    await h.wait_for("run_end")

    assert h.model.requests[0]["model"] == "my-model"
    config = yaml.safe_load((h.root / "harness.yaml").read_text("utf-8"))
    assert config["model"] == "my-model"
    assert config["maxTurns"] == 5
    _, info = await h.api("GET", "/project")
    assert info["agents"][0]["modelSource"] == "config"
    assert info["agents"][0]["maxTurns"] == 5


async def test_model_connection_test(start):
    h = await start([conftest.text_response("pong")])

    _, result = await h.api("POST", "/settings/model/test", {})

    assert result["ok"] is True


async def test_handoff_changes_the_agent_shown_in_summary(tmp_path, fake_model):
    root = make_project(tmp_path / "handoff")
    (root / "agents" / "main.md").write_text(
        AGENT.replace("tools:", "handoffs:\n  - billing\ntools:"),
        encoding="utf-8",
    )
    (root / "agents" / "billing.md").write_text(
        "---\nname: billing\ndescription: 處理帳單\n---\n你處理帳單。\n",
        encoding="utf-8",
    )
    loaded = project_lib.load_project(root)
    model = fake_model(
        [
            conftest.tool_response(("transfer_to_billing", {})),
            conftest.text_response("我是帳單助手"),
        ]
    )
    app = server_lib.create_app(loaded, model_fn=model)
    async with TestClient(TestServer(app)) as client:
        h = Harness(client, root, model)
        await h.listen()
        _, created = await h.api("POST", "/sessions", {"text": "帳單問題"})
        await h.wait_for("run_end")
        _, detail = await h.api("GET", f"/sessions/{created['id']}")
        h._reader.cancel()
    loaded.close()

    assert detail["summary"]["agent"] == "billing"


async def test_subagent_progress_is_streamed_and_kept(tmp_path, fake_model):
    root = make_project(tmp_path / "subagent")
    (root / "agents" / "main.md").write_text(
        AGENT.replace("tools:", "subagents:\n  - researcher\ntools:"),
        encoding="utf-8",
    )
    (root / "agents" / "researcher.md").write_text(
        "---\nname: researcher\ndescription: 查資料\ntools: [files]\n---\n"
        "你查資料。\n",
        encoding="utf-8",
    )
    (root / "notes.md").write_text("十月賣了 192,000", encoding="utf-8")
    loaded = project_lib.load_project(root)
    model = fake_model(
        [
            conftest.tool_response(("ask_researcher", {"task": "查十月"})),
            conftest.tool_response(("read_file", {"path": "notes.md"})),
            conftest.text_response("十月賣了 192,000"),
            conftest.text_response("研究員說十月賣了 192,000"),
        ]
    )
    app = server_lib.create_app(loaded, model_fn=model)
    async with TestClient(TestServer(app)) as client:
        h = Harness(client, root, model)
        await h.listen()
        _, created = await h.api("POST", "/sessions", {"text": "十月？"})
        await h.wait_for("run_end")
        _, detail = await h.api("GET", f"/sessions/{created['id']}")
        h._reader.cancel()
    loaded.close()

    steps = [e for e in h.events if e["type"] == "subagent_step"]
    assert [step["step"]["status"] for step in steps] == ["running", "done"]
    assert steps[0]["callId"] == "call_0_ask_researcher"
    assert steps[0]["step"]["agent"] == "researcher"
    assert steps[0]["step"]["name"] == "read_file"
    activities = [
        e["summary"].get("activity") for e in h.events if e["type"] == "session"
    ]
    assert {"kind": "tool", "tool": "read_file", "agent": "researcher"} in (
        activities
    )
    [kept] = detail["subagentSteps"]["call_0_ask_researcher"]
    assert kept["result"] == "十月賣了 192,000"
    assert kept["arguments"] == {"path": "notes.md"}


async def test_rejects_requests_from_other_sites(start):
    h = await start()

    status, _ = await h.api(
        "POST",
        "/sessions",
        {"text": "嗨"},
        headers={"Origin": "https://evil.example"},
    )

    assert status == 403


async def test_state_changing_requests_must_be_json(start):
    h = await start()

    response = await h.client.post(
        "/api/sessions",
        data=json.dumps({"text": "嗨"}),
        headers={"Content-Type": "text/plain"},
    )

    assert response.status == 415


async def test_rejects_foreign_host_header(start):
    h = await start()

    response = await h.client.get(
        "/api/project", headers={"Host": "evil.example"}
    )

    assert response.status == 403


async def test_unknown_paths_serve_index_and_cannot_escape(tmp_path, start):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<p>app</p>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    h = await start(static_dir=static)

    page = await h.client.get("/s/123")
    escaped = await h.client.get("/../secret.txt")
    encoded = await h.client.get("/%2e%2e/secret.txt")

    assert await page.text() == "<p>app</p>"
    assert await escaped.text() == "<p>app</p>"
    assert await encoded.text() == "<p>app</p>"


async def test_missing_frontend_explains_how_to_build(tmp_path, start):
    h = await start(static_dir=tmp_path / "empty")

    response = await h.client.get("/")

    assert response.status == 503
    assert "npm run build" in await response.text()


def test_packaged_frontend_is_present():
    index = server_lib.STATIC_DIR / "index.html"

    assert index.exists(), "請在 repo 根目錄執行 npm run build"
    assets = server_lib.STATIC_DIR / "assets"
    referenced = [p.name for p in assets.iterdir()]
    html = index.read_text(encoding="utf-8")
    assert all(name in html for name in referenced)


def test_cli_web_reports_port_in_use(tmp_path, capsys, monkeypatch):
    root = make_project(tmp_path / "cli")

    async def fail(*args, **kwargs):
        raise OSError("address already in use")

    monkeypatch.setattr("harness.web.serve", fail)
    code = cli.main(["web", "-p", str(root), "--port", "1"])

    assert code == 1
    assert "無法在" in capsys.readouterr().err


class TestConfigEditor:
    """只改需要的那幾行，保留註解。"""

    def test_scaffold_template_keeps_comments(self, tmp_path):
        root = tmp_path / "scaffolded"
        assert cli.main(["new", str(root)]) == 0
        before = (root / "harness.yaml").read_text(encoding="utf-8")

        config_lib.save_permissions(
            root,
            {"mode": "bypass", "allow": ["a_*"], "ask": [], "deny": ["*"]},
        )
        config_lib.save_model_settings(root, "my/model", 8)

        text = (root / "harness.yaml").read_text(encoding="utf-8")
        comments = [line for line in before.splitlines() if "#" in line]
        kept = [line for line in comments if line in text]
        assert len(kept) >= len(comments) - 1
        config = yaml.safe_load(text)
        assert config["permissions"] == {
            "mode": "bypass",
            "allow": ["a_*"],
            "deny": ["*"],
        }
        assert config["model"] == "my/model"
        assert config["maxTurns"] == 8

    def test_block_list_form_is_replaced(self, tmp_path):
        (tmp_path / "harness.yaml").write_text(
            "name: x\npermissions:\n  mode: ask\n  allow:\n    - a\n    - b\n"
            "  deny: [c]\nmemory: true\n",
            encoding="utf-8",
        )

        config_lib.save_permissions(
            tmp_path, {"mode": "ask", "allow": ["z"], "ask": [], "deny": []}
        )

        config = yaml.safe_load((tmp_path / "harness.yaml").read_text())
        assert config["permissions"] == {"mode": "ask", "allow": ["z"]}
        assert config["memory"] is True

    def test_missing_permissions_block_is_added(self, tmp_path):
        (tmp_path / "harness.yaml").write_text("name: x\n", encoding="utf-8")

        config_lib.save_permissions(
            tmp_path, {"mode": "ask", "allow": [], "ask": ["s"], "deny": []}
        )
        config_lib.save_model_settings(tmp_path, None, None)

        config = yaml.safe_load((tmp_path / "harness.yaml").read_text())
        assert config == {
            "name": "x",
            "permissions": {"mode": "ask", "ask": ["s"]},
        }

    def test_removes_model_when_cleared(self, tmp_path):
        (tmp_path / "harness.yaml").write_text(
            "name: x\nmodel: old\nmaxTurns: 3\n", encoding="utf-8"
        )

        config_lib.save_model_settings(tmp_path, None, None)

        assert yaml.safe_load((tmp_path / "harness.yaml").read_text()) == {
            "name": "x"
        }

    def test_unusual_file_is_left_alone(self, tmp_path):
        original = "permissions: &p\n  mode: ask\nother: *p\n"
        (tmp_path / "harness.yaml").write_text(original, encoding="utf-8")

        with pytest.raises(ValueError, match="直接編輯"):
            config_lib.save_permissions(
                tmp_path,
                {"mode": "bypass", "allow": [], "ask": [], "deny": []},
            )

        assert (tmp_path / "harness.yaml").read_text() == original


def test_static_guard_blocks_paths_outside_folder(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<p>app</p>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")

    response = server_lib._serve_static(static, "/../secret.txt")

    assert response._path == (static / "index.html").resolve()


async def test_blocked_message_keeps_notice_in_summary(tmp_path, fake_model):
    root = make_project(tmp_path / "guarded")
    with (root / "harness.yaml").open("a", encoding="utf-8") as file:
        file.write("guardrails:\n  blockedWords: [機密]\n")
    loaded = project_lib.load_project(root)
    model = fake_model([])
    app = server_lib.create_app(loaded, model_fn=model)
    async with TestClient(TestServer(app)) as client:
        h = Harness(client, root, model)
        await h.listen()
        _, created = await h.api("POST", "/sessions", {"text": "這是機密"})
        end = await h.wait_for("run_end")
        _, detail = await h.api("GET", f"/sessions/{created['id']}")
        h._reader.cancel()
    loaded.close()

    assert end["stopReason"] == "blocked"
    assert detail["summary"]["notice"]
    assert model.requests == []


class TestServerDeployment:
    """架在伺服器上：登入密碼、允許的主機名稱與反向代理。"""

    @pytest.fixture
    async def client(self, tmp_path):
        """依參數建立伺服器的函式，回傳 TestClient。"""
        made = []

        async def make(**options):
            loaded = project_lib.load_project(make_project(tmp_path / "srv"))
            app = server_lib.create_app(loaded, **options)
            client = TestClient(TestServer(app))
            await client.start_server()
            made.append((client, loaded))
            return client

        yield make
        for client, loaded in made:
            await client.close()
            loaded.close()

    @staticmethod
    def _login(password, user="anyone"):
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    async def test_password_is_required_for_every_path(self, client):
        c = await client(password="s3cret")

        for path in ("/", "/api/project", "/api/events"):
            response = await c.get(path)
            assert response.status == 401
            assert response.headers["WWW-Authenticate"].startswith("Basic ")

    async def test_wrong_password_is_rejected(self, client, monkeypatch):
        monkeypatch.setattr(server_lib, "_FAILED_LOGIN_DELAY", 0)
        c = await client(password="s3cret")

        response = await c.get("/api/project", headers=self._login("nope"))

        assert response.status == 401

    async def test_any_user_name_with_right_password_gets_in(self, client):
        c = await client(password="s3cret")

        response = await c.get(
            "/api/project", headers=self._login("s3cret", user="王小明")
        )

        assert response.status == 200
        assert (await response.json())["name"] == "web_demo"

    async def test_with_password_any_host_is_accepted(self, client):
        c = await client(password="s3cret", host="0.0.0.0")

        response = await c.get(
            "/api/project",
            headers={**self._login("s3cret"), "Host": "10.0.0.5:4321"},
        )

        assert response.status == 200

    async def test_allowed_hosts_limit_the_host_header(self, client):
        c = await client(password="s3cret", allowed_hosts=["ai.example.com"])
        login = self._login("s3cret")

        allowed = await c.get(
            "/api/project", headers={**login, "Host": "AI.example.com"}
        )
        other = await c.get(
            "/api/project", headers={**login, "Host": "evil.example"}
        )

        assert allowed.status == 200
        assert other.status == 403

    async def test_https_reverse_proxy_origin_is_accepted(self, client):
        c = await client(password="s3cret", allowed_hosts=["ai.example.com"])
        headers = {
            **self._login("s3cret"),
            "Host": "ai.example.com",
            "Origin": "https://ai.example.com",
        }

        # 不存在的對話：通過安全檢查後才會得到 404。
        path = "/api/sessions/missing/stop"
        response = await c.post(path, json={}, headers=headers)
        foreign = await c.post(
            path, json={}, headers={**headers, "Origin": "https://evil.example"}
        )

        assert response.status == 404
        assert foreign.status == 403

    @pytest.mark.parametrize(
        "options",
        [{"host": "0.0.0.0"}, {"allowed_hosts": ["ai.example.com"]}],
    )
    async def test_serving_to_others_requires_a_password(
        self, tmp_path, options
    ):
        loaded = project_lib.load_project(make_project(tmp_path / "open"))

        with pytest.raises(ValueError, match="HARNESS_WEB_PASSWORD"):
            await server_lib.serve(loaded, port=0, **options)
        loaded.close()

    @pytest.fixture
    def served(self, monkeypatch):
        """把伺服器換成假的，記下 CLI 交給它的參數。"""
        calls = []

        async def fake_serve(loaded, **options):
            calls.append(options)
            options["on_ready"]("http://test")

        monkeypatch.setattr("harness.web.serve", fake_serve)
        monkeypatch.delenv("HARNESS_WEB_PASSWORD", raising=False)
        return calls

    def test_cli_generates_password_for_public_host(
        self, tmp_path, served, capsys
    ):
        root = make_project(tmp_path / "cli")

        code = cli.main(["web", "-p", str(root), "--host", "0.0.0.0"])
        cli.main(["web", "-p", str(root), "--host", "0.0.0.0"])

        assert code == 0
        password = served[0]["password"]
        assert len(password) >= 20
        assert password in capsys.readouterr().out
        # 重新啟動後還是同一組，使用者不用重新登入。
        assert served[1]["password"] == password
        saved = credentials.secrets_dir(root) / credentials.PASSWORD_FILE
        assert saved.read_text("utf-8").strip() == password

    def test_cli_generates_password_behind_reverse_proxy(
        self, tmp_path, served
    ):
        root = make_project(tmp_path / "cli")

        cli.main(["web", "-p", str(root), "--allowed-host", "ai.example.com"])

        assert served[0]["password"]

    def test_cli_uses_password_from_environment(
        self, tmp_path, served, monkeypatch, capsys
    ):
        monkeypatch.setenv("HARNESS_WEB_PASSWORD", "my-own-password")
        root = make_project(tmp_path / "cli")

        cli.main(["web", "-p", str(root), "--host", "0.0.0.0"])

        assert served[0]["password"] == "my-own-password"
        assert "my-own-password" not in capsys.readouterr().out
        assert not credentials.secrets_dir(root).exists()

    def test_cli_needs_no_password_on_localhost(self, tmp_path, served):
        root = make_project(tmp_path / "cli")

        cli.main(["web", "-p", str(root)])

        assert served[0]["password"] is None
        assert not credentials.secrets_dir(root).exists()


class TestZeroConfigStart:
    """pyharness web 指到空資料夾時，自動建立可以直接使用的助手。"""

    @pytest.fixture
    def served(self, monkeypatch):
        """把伺服器換成假的，記下啟動時載入的專案。"""
        loaded_projects = []

        async def fake_serve(loaded, **options):
            loaded_projects.append(loaded)

        monkeypatch.setattr("harness.web.serve", fake_serve)
        monkeypatch.setattr(sandbox_lib, "available", lambda: False)
        return loaded_projects

    def test_empty_folder_gets_a_ready_assistant(self, tmp_path, served):
        root = tmp_path / "fresh"

        code = cli.main(["web", "-p", str(root)])

        assert code == 0
        agent = served[0].agents["main"]
        names = {item.name for item in agent.tools}
        assert {"read_file", "update_plan", "web_fetch"} <= names
        assert "run_python" not in names
        assert served[0].memory is not None
        assert (root / "workspace").is_dir()

    def test_assistant_uses_sandbox_when_docker_works(
        self, tmp_path, served, monkeypatch
    ):
        monkeypatch.setattr(sandbox_lib, "available", lambda: True)

        cli.main(["web", "-p", str(tmp_path / "fresh")])

        names = {item.name for item in served[0].agents["main"].tools}
        assert "run_python" in names
        assert served[0].sandbox is not None

    def test_existing_project_is_left_alone(self, tmp_path, served):
        root = make_project(tmp_path / "mine")

        cli.main(["web", "-p", str(root)])

        assert (root / "harness.yaml").read_text("utf-8") == CONFIG
        assert served[0].name == "web_demo"

    def test_folder_with_other_files_is_not_touched(
        self, tmp_path, served, capsys
    ):
        (tmp_path / "notes.txt").write_text("我的檔案", encoding="utf-8")

        code = cli.main(["web", "-p", str(tmp_path)])

        assert code == 1
        assert "harness.yaml" in capsys.readouterr().err
        assert [item.name for item in tmp_path.iterdir()] == ["notes.txt"]

    def test_other_commands_do_not_create_projects(self, tmp_path, capsys):
        code = cli.main(["run", "-p", str(tmp_path / "fresh"), "嗨"])

        assert code == 1
        assert not (tmp_path / "fresh").exists()

    def test_keys_saved_from_the_web_reach_other_commands(
        self, tmp_path, monkeypatch, fake_model
    ):
        monkeypatch.delenv("DEMO_API_KEY", raising=False)
        root = make_project(tmp_path / "mine")
        credentials.update(root, {"DEMO_API_KEY": "sk-saved"})
        monkeypatch.delenv("DEMO_API_KEY")
        monkeypatch.setattr(
            llm, "complete", fake_model([conftest.text_response("好")])
        )

        cli.main(["run", "-p", str(root), "嗨"])

        assert os.environ["DEMO_API_KEY"] == "sk-saved"


class TestConnectionSettings:
    """在網頁上選供應商、填金鑰與模型。"""

    @pytest.fixture(autouse=True)
    def clean_env(self, monkeypatch):
        """清掉連線相關的環境變數，測試改動的部分結束後恢復。"""
        names = [
            "HARNESS_MODEL",
            "HARNESS_BASE_URL",
            "HARNESS_API_KEY",
            "OLLAMA_API_BASE",
            *(p.key_var for p in providers.PROVIDERS if p.key_var),
        ]
        for name in names:
            monkeypatch.delenv(name, raising=False)

    async def test_project_reports_missing_key(self, start):
        h = await start()

        _, info = await h.api("GET", "/project")

        connection = info["model"]["connection"]
        assert connection["ready"] is False
        assert connection["provider"] == "anthropic"
        anthropic = connection["providers"][0]
        assert anthropic["keyVar"] == "ANTHROPIC_API_KEY"
        assert anthropic["keySet"] is False

    async def test_key_from_environment_makes_it_ready(
        self, start, monkeypatch
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        h = await start()

        _, info = await h.api("GET", "/project")

        assert info["model"]["connection"]["ready"] is True
        assert info["model"]["connection"]["provider"] == "openai"
        assert info["agents"][0]["model"] == "openai/gpt-5"

    async def test_saving_tests_the_model_then_stores_key_and_model(
        self, start
    ):
        h = await start([conftest.text_response("pong")])

        status, _ = await h.api(
            "PUT",
            "/settings/connection",
            {
                "provider": "openai",
                "apiKey": " sk-new ",
                "baseURL": "",
                "model": "openai/gpt-5",
            },
        )

        assert status == 200
        assert h.model.requests[0]["model"] == "openai/gpt-5"
        assert os.environ["OPENAI_API_KEY"] == "sk-new"
        assert credentials.load(h.root)["OPENAI_API_KEY"] == "sk-new"
        config = yaml.safe_load((h.root / "harness.yaml").read_text("utf-8"))
        assert config["model"] == "openai/gpt-5"
        _, info = await h.api("GET", "/project")
        assert info["model"]["connection"]["ready"] is True
        assert info["model"]["connection"]["provider"] == "openai"
        assert "sk-new" not in json.dumps(info)
        assert await h.wait_for("project")

    async def test_failed_connection_is_not_saved(self, start):
        class AuthenticationError(Exception):
            """和 litellm 金鑰錯誤時丟出的例外同名。"""

        async def broken(model, messages, tools, params):
            raise AuthenticationError("401 invalid api key")
            yield

        h = await start(model=broken)

        status, body = await h.api(
            "PUT",
            "/settings/connection",
            {"provider": "openai", "apiKey": "sk-bad", "model": "openai/gpt-5"},
        )

        assert status == 400
        assert body["error"].startswith("金鑰不正確")
        assert "invalid api key" in body["error"]
        assert "OPENAI_API_KEY" not in os.environ
        assert credentials.load(h.root) == {}
        config = yaml.safe_load((h.root / "harness.yaml").read_text("utf-8"))
        assert "model" not in config

    @pytest.mark.parametrize(
        ("settings", "message"),
        [
            (
                {"provider": "openai", "apiKey": "", "model": "openai/gpt-5"},
                "金鑰",
            ),
            ({"provider": "openai", "apiKey": "k", "model": ""}, "模型名稱"),
            (
                {"provider": "openai", "apiKey": "k", "model": "gpt-5"},
                "openai/",
            ),
            ({"provider": "nope", "apiKey": "k", "model": "x/y"}, "供應商"),
            ({"provider": "endpoint", "apiKey": "", "model": "claude"}, "端點"),
            (
                {
                    "provider": "endpoint",
                    "baseURL": "litellm:4000",
                    "model": "c",
                },
                "http",
            ),
        ],
    )
    async def test_incomplete_settings_are_rejected(
        self, start, settings, message
    ):
        h = await start([conftest.text_response("pong")])

        status, body = await h.api("PUT", "/settings/connection", settings)

        assert status == 400
        assert message in body["error"]
        assert h.model.requests == []

    async def test_existing_key_can_be_kept_when_changing_model(
        self, start, monkeypatch
    ):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
        h = await start([conftest.text_response("pong")])

        status, _ = await h.api(
            "PUT",
            "/settings/connection",
            {
                "provider": "anthropic",
                "apiKey": "",
                "model": "anthropic/claude-sonnet-5-5",
            },
        )

        assert status == 200
        assert "ANTHROPIC_API_KEY" not in credentials.load(h.root)
        _, info = await h.api("GET", "/project")
        assert info["agents"][0]["model"] == "anthropic/claude-sonnet-5-5"

    async def test_endpoint_and_back_to_direct_provider(self, start):
        h = await start(
            [conftest.text_response("pong"), conftest.text_response("pong")]
        )

        status, _ = await h.api(
            "PUT",
            "/settings/connection",
            {
                "provider": "endpoint",
                "apiKey": "sk-proxy",
                "baseURL": "http://litellm:4000",
                "model": "claude",
            },
        )
        _, info = await h.api("GET", "/project")

        assert status == 200
        assert os.environ["HARNESS_BASE_URL"] == "http://litellm:4000"
        assert os.environ["HARNESS_API_KEY"] == "sk-proxy"
        assert info["model"]["connection"]["provider"] == "endpoint"
        assert info["model"]["connection"]["ready"] is True

        status, _ = await h.api(
            "PUT",
            "/settings/connection",
            {"provider": "openai", "apiKey": "sk-o", "model": "openai/gpt-5"},
        )
        _, info = await h.api("GET", "/project")

        assert status == 200
        assert "HARNESS_BASE_URL" not in os.environ
        assert credentials.load(h.root)["HARNESS_BASE_URL"] == ""
        assert info["model"]["connection"]["provider"] == "openai"

    async def test_ollama_needs_no_key_and_keeps_server_address(self, start):
        h = await start([conftest.text_response("pong")])

        status, _ = await h.api(
            "PUT",
            "/settings/connection",
            {
                "provider": "ollama",
                "baseURL": "http://host.docker.internal:11434",
                "model": "ollama_chat/hermes3",
            },
        )
        _, info = await h.api("GET", "/project")

        assert status == 200
        saved = "http://host.docker.internal:11434"
        assert os.environ["OLLAMA_API_BASE"] == saved
        ollama = next(
            p
            for p in info["model"]["connection"]["providers"]
            if p["id"] == "ollama"
        )
        assert ollama["baseURL"] == saved
        assert ollama["needsKey"] is False
        assert info["model"]["connection"]["ready"] is True


class TestUploads:
    """網頁上傳檔案與訊息附件。"""

    @pytest.fixture
    async def server(self, tmp_path, fake_model):
        """啟動伺服器；回傳 (TestClient, 專案根目錄, 假模型) 的建立函式。"""
        made = []

        async def make(script=None, config="", **options):
            root = make_project(tmp_path / f"up{len(made)}")
            if config:
                (root / "harness.yaml").write_text(CONFIG + config)
            loaded = project_lib.load_project(root)
            model = fake_model(script or [])
            app = server_lib.create_app(loaded, model_fn=model, **options)
            client = TestClient(TestServer(app))
            await client.start_server()
            made.append((client, loaded))
            return client, root, model

        yield make
        for client, loaded in made:
            await client.close()
            loaded.close()

    @staticmethod
    async def _upload(client, *files, headers=None):
        form = aiohttp.FormData()
        for name, content in files:
            form.add_field("file", content, filename=name)
        response = await client.post(
            "/api/uploads",
            data=form,
            headers={"X-Harness-Upload": "1"} if headers is None else headers,
        )
        return response.status, await response.json()

    async def test_upload_saves_files_under_workspace(self, server):
        client, root, _ = await server()

        status, body = await self._upload(
            client, ("十月 業績.xlsx", b"excel"), ("notes.txt", b"hi")
        )

        assert status == 201
        assert body["files"] == [
            {
                "name": "十月_業績.xlsx",
                "path": "workspace/uploads/十月_業績.xlsx",
                "size": 5,
            },
            {
                "name": "notes.txt",
                "path": "workspace/uploads/notes.txt",
                "size": 2,
            },
        ]
        assert (
            root / "workspace/uploads/十月_業績.xlsx"
        ).read_bytes() == b"excel"

    async def test_same_name_gets_a_number(self, server):
        client, _, _ = await server()

        await self._upload(client, ("a.csv", b"1"))
        _, body = await self._upload(client, ("a.csv", b"2"))

        assert body["files"][0]["path"] == "workspace/uploads/a-2.csv"

    async def test_large_file_streams_past_json_body_limit(self, server):
        client, root, _ = await server(max_upload_bytes=3 * 1024 * 1024)
        content = b"x" * (2 * 1024 * 1024)

        status, _ = await self._upload(client, ("big.bin", content))

        assert status == 201
        assert (root / "workspace/uploads/big.bin").stat().st_size == len(
            content
        )

    async def test_file_over_limit_is_rejected_and_removed(self, server):
        client, root, _ = await server(max_upload_bytes=1024)

        status, body = await self._upload(client, ("big.bin", b"x" * 2048))

        assert status == 413
        assert "超過上限" in body["error"]
        assert list((root / "workspace/uploads").iterdir()) == []

    @pytest.mark.parametrize(
        "headers",
        [{}, {"X-Harness-Upload": "1", "Origin": "https://evil.example"}],
    )
    async def test_cross_site_uploads_are_rejected(self, server, headers):
        client, root, _ = await server()

        status, _ = await self._upload(client, ("a.txt", b"x"), headers=headers)

        assert status == 403
        assert not (root / "workspace/uploads").exists()

    async def test_attachments_are_described_in_the_message(self, server):
        client, root, model = await server(
            [conftest.text_response("收到")],
            config="sandbox:\n  workspace: data\n",
        )
        _, body = await self._upload(client, ("sales.csv", b"a,b"))

        status, created = await _post(
            client,
            "/api/sessions",
            {"text": "幫我分析", "attachments": [body["files"][0]["path"]]},
        )
        await _wait_idle(client, created["id"])

        assert status == 201
        user = model.requests[0]["messages"][-1]["content"]
        assert user == (
            "幫我分析\n\n[附件] data/uploads/sales.csv"
            "（run_python 裡的路徑：/workspace/uploads/sales.csv）"
        )

    async def test_attachments_alone_are_enough(self, server):
        client, _, model = await server([conftest.text_response("收到")])
        _, body = await self._upload(client, ("報告.pdf", b"%PDF"))

        status, created = await _post(
            client,
            "/api/sessions",
            {"text": "", "attachments": [body["files"][0]["path"]]},
        )
        await _wait_idle(client, created["id"])

        assert status == 201
        assert created["title"] == "報告.pdf"
        user = model.requests[0]["messages"][-1]["content"]
        assert user == "[附件] workspace/uploads/報告.pdf"

    @pytest.mark.parametrize(
        "path",
        [
            "harness.yaml",
            "workspace/uploads/../../harness.yaml",
            "workspace/uploads/none.txt",
        ],
    )
    async def test_only_uploaded_files_can_be_attached(self, server, path):
        client, _, _ = await server()

        status, body = await _post(
            client, "/api/sessions", {"text": "看看", "attachments": [path]}
        )

        assert status == 400
        assert "上傳的檔案" in body["error"]

    async def test_project_info_advertises_uploads(self, server):
        client, _, _ = await server(max_upload_bytes=1234)

        response = await client.get("/api/project")

        assert (await response.json())["uploads"] == {"maxBytes": 1234}

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("../../etc/passwd", "passwd"),
            ("C:\\Users\\me\\報表 (1).xlsx", "報表_(1).xlsx"),
            (".env", "env"),
            ("", "file"),
            ("%E5%A0%B1%E5%91%8A.pdf", "報告.pdf"),
            ("a" * 200 + ".csv", "a" * 96 + ".csv"),
        ],
    )
    def test_safe_name(self, given, expected):
        assert uploads_lib.safe_name(given) == expected


async def _post(client, path, body):
    response = await client.post(path, json=body)
    return response.status, await response.json()


async def _wait_idle(client, session_id):
    """等這段對話跑完。"""
    async with asyncio.timeout(3):
        while True:
            response = await client.get(f"/api/sessions/{session_id}")
            detail = await response.json()
            if detail["summary"]["status"] != "running":
                return detail
            await asyncio.sleep(0.02)
