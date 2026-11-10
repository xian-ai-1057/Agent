"""project 與 scaffold 模組的測試。"""

import asyncio
import sys

import pytest

import harness
from harness import mcp_servers
from harness import project
from harness import scaffold
from tests import test_mcp_servers


@pytest.fixture
def project_dir(tmp_path):
    """用 scaffold 建立的範本專案。"""
    root = tmp_path / "demo_project"
    scaffold.create_project(root)
    return root


def _write_mcp_config(root, name, script):
    (root / "harness.yaml").write_text(
        f"mcpServers:\n  {name}:\n    command: {sys.executable}\n"
        f"    args: [{script}]\n",
        encoding="utf-8",
    )


def _write_server(root, relative="server.py", prefix=""):
    path = root / relative
    path.write_text(prefix + test_mcp_servers.SERVER, encoding="utf-8")


def _write_agent(root, name, frontmatter, body="你是助手"):
    path = root / "agents" / f"{name}.md"
    path.write_text(f"---\n{frontmatter}\n---\n{body}\n", encoding="utf-8")


class TestLoadProject:
    """專案資料夾的載入。"""

    def test_load_project_reads_scaffolded_project(self, project_dir):
        loaded = harness.load_project(project_dir)

        assert loaded.name == "demo_project"
        assert loaded.default_agent == "main"
        assert list(loaded.tools) == ["word_count"]

    def test_load_project_appends_agents_md_to_instructions(self, project_dir):
        (project_dir / "AGENTS.md").write_text("共用規則", encoding="utf-8")

        agent = harness.load_project(project_dir).agents["main"]

        assert agent.instructions.startswith("你是 demo_project 的助手")
        assert agent.instructions.endswith("共用規則")

    def test_load_project_gives_all_tools_when_tools_key_is_absent(
        self, project_dir
    ):
        _write_agent(project_dir, "open", "name: open")

        agent = harness.load_project(project_dir).agents["open"]

        assert [item.name for item in agent.tools] == ["word_count"]

    def test_load_project_gives_no_tools_when_tools_is_empty(self, project_dir):
        _write_agent(project_dir, "bare", "name: bare\ntools: []")

        assert harness.load_project(project_dir).agents["bare"].tools == []

    def test_load_project_resolves_builtin_toolset(self, project_dir):
        _write_agent(project_dir, "writer", "tools: [files, word_count]")

        agent = harness.load_project(project_dir).agents["writer"]

        assert [item.name for item in agent.tools] == [
            "read_file",
            "list_dir",
            "write_file",
            "word_count",
        ]

    def test_load_project_prefers_agent_model_over_project_model(
        self, project_dir
    ):
        (project_dir / "harness.yaml").write_text(
            "model: openai/base\n", encoding="utf-8"
        )
        _write_agent(project_dir, "special", "model: openai/special")

        loaded = harness.load_project(project_dir)

        assert loaded.agents["main"].model == "openai/base"
        assert loaded.agents["special"].model == "openai/special"

    def test_load_project_reads_camel_case_agent_settings(self, project_dir):
        (project_dir / "harness.yaml").write_text(
            "maxTurns: 5\nmodelParams: {max_tokens: 100}\n", encoding="utf-8"
        )
        _write_agent(project_dir, "special", "maxTurns: 9")

        loaded = harness.load_project(project_dir)

        assert loaded.agents["main"].max_turns == 5
        assert loaded.agents["main"].model_params == {"max_tokens": 100}
        assert loaded.agents["special"].max_turns == 9

    def test_load_project_connects_mcp_servers(self, project_dir):
        pytest.importorskip("mcp")
        _write_server(project_dir)
        _write_mcp_config(project_dir, "backend", "server.py")
        _write_agent(project_dir, "remote", "tools: [backend, word_count]")
        _write_agent(project_dir, "picky", "tools: [add]")

        with harness.load_project(project_dir) as loaded:
            remote = [item.name for item in loaded.agents["remote"].tools]
            picky = [item.name for item in loaded.agents["picky"].tools]
            assert [c.name for c in loaded.mcp_connections] == ["backend"]
            assert asyncio.run(loaded.tools["add"].func(a=1, b=2)) == "3"

        assert remote == [
            "add",
            "summary",
            "slow",
            "delete_everything",
            "word_count",
        ]
        assert picky == ["add"]
        with pytest.raises(harness.ToolError, match="已經斷線"):
            asyncio.run(loaded.tools["add"].func(a=1, b=2))

    def test_load_project_does_not_import_server_scripts_as_tools(
        self, project_dir
    ):
        pytest.importorskip("mcp")
        # 這個檔案在被 import 時會出錯，但當成伺服器執行（__main__）時正常。
        _write_server(
            project_dir,
            "tools/server.py",
            prefix="if __name__ != '__main__':\n"
            "    raise RuntimeError('不應該被當成工具模組載入')\n",
        )
        _write_mcp_config(project_dir, "backend", "tools/server.py")

        with harness.load_project(project_dir) as loaded:
            assert "add" in loaded.tools
            assert "word_count" in loaded.tools

    def test_load_project_prefers_same_named_python_toolset(self, project_dir):
        # 給兩個版本共用的專案：TypeScript 版啟動伺服器，Python 版直接
        # 載入同名的 tools/example.py，不啟動伺服器（也不需要 mcp 套件）。
        _write_mcp_config(project_dir, "example", "missing_server.py")

        with harness.load_project(project_dir) as loaded:
            assert loaded.mcp_connections == []
            assert list(loaded.tools) == ["word_count"]

    def test_load_project_rejects_duplicate_tool_names(self, project_dir):
        pytest.importorskip("mcp")
        _write_server(project_dir)
        (project_dir / "tools" / "math.py").write_text(
            "import harness\n\n\n@harness.tool\n"
            "def add(a: int) -> int:\n    return a + 1\n",
            encoding="utf-8",
        )
        _write_mcp_config(project_dir, "backend", "server.py")

        with pytest.raises(ValueError, match="工具 add 與 math 裡的工具同名"):
            harness.load_project(project_dir)

    def test_load_project_closes_servers_when_loading_fails(
        self, project_dir, monkeypatch
    ):
        pytest.importorskip("mcp")
        _write_server(project_dir)
        _write_mcp_config(project_dir, "backend", "server.py")
        _write_agent(project_dir, "broken", "tools: [no_such_tool]")
        opened = []
        original = mcp_servers.from_config

        def record(*args, **kwargs):
            opened.append(original(*args, **kwargs))
            return opened[-1]

        monkeypatch.setattr(mcp_servers, "from_config", record)

        with pytest.raises(ValueError, match="no_such_tool"):
            harness.load_project(project_dir)
        with pytest.raises(harness.ToolError, match="已經斷線"):
            asyncio.run(opened[0].tools[0].func(a=1, b=2))

    def test_load_project_reports_server_that_fails_to_start(self, project_dir):
        pytest.importorskip("mcp")
        _write_mcp_config(project_dir, "backend", "missing_server.py")

        with pytest.raises(RuntimeError, match="無法啟動 MCP 伺服器 backend"):
            harness.load_project(project_dir)

    def test_load_project_rejects_unknown_mcp_settings(self, project_dir):
        (project_dir / "harness.yaml").write_text(
            "mcpServers:\n  backend:\n    comand: python3\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="不認得的設定"):
            harness.load_project(project_dir)

    def test_load_project_reads_permissions(self, project_dir):
        (project_dir / "harness.yaml").write_text(
            "permissions:\n  mode: dontAsk\n  deny: ['write_*']\n"
            "  ask: ['send_*']\n",
            encoding="utf-8",
        )

        loaded = harness.load_project(project_dir)

        assert loaded.permissions.mode == "dontAsk"
        assert loaded.permissions.deny == ["write_*"]
        assert loaded.permissions.ask == ["send_*"]

    def test_load_project_lets_tools_import_project_packages(self, project_dir):
        package = project_dir / "legacy_pkg_for_test"
        package.mkdir()
        (package / "__init__.py").write_text("ANSWER = 42\n", encoding="utf-8")
        (project_dir / "tools" / "uses_legacy.py").write_text(
            "import harness\n"
            "import legacy_pkg_for_test\n\n"
            "@harness.tool\n"
            "def answer() -> int:\n"
            '    """回答。"""\n'
            "    return legacy_pkg_for_test.ANSWER\n",
            encoding="utf-8",
        )

        loaded = harness.load_project(project_dir)

        assert loaded.tools["answer"]() == 42

    def test_load_project_raises_without_config(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="harness new"):
            harness.load_project(tmp_path)

    def test_load_project_raises_on_unknown_tool_name(self, project_dir):
        _write_agent(project_dir, "broken", "tools: [nope]")

        with pytest.raises(ValueError, match="找不到工具或 toolset：nope"):
            harness.load_project(project_dir)

    def test_load_project_raises_on_unknown_default_agent(self, project_dir):
        (project_dir / "harness.yaml").write_text(
            "defaultAgent: ghost\n", encoding="utf-8"
        )

        with pytest.raises(ValueError, match="ghost"):
            harness.load_project(project_dir)

    def test_load_project_raises_without_agents(self, project_dir):
        (project_dir / "agents" / "main.md").unlink()

        with pytest.raises(ValueError, match="沒有任何 agent"):
            harness.load_project(project_dir)


class TestAgentLinks:
    """agent 檔案裡的 subagents 與 handoffs。"""

    def test_load_project_links_subagents_and_handoffs(self, project_dir):
        _write_agent(project_dir, "researcher", "description: 查資料")
        _write_agent(project_dir, "billing", "description: 帳務")
        _write_agent(
            project_dir,
            "front",
            "subagents: [researcher]\nhandoffs: [billing]",
        )

        loaded = harness.load_project(project_dir).agents

        assert loaded["front"].subagents == [loaded["researcher"]]
        assert loaded["front"].handoffs == [loaded["billing"]]
        assert loaded["billing"].handoffs == []

    def test_load_project_allows_agents_to_hand_off_to_each_other(
        self, project_dir
    ):
        _write_agent(project_dir, "front", "handoffs: [billing]")
        _write_agent(project_dir, "billing", "handoffs: [front]")

        loaded = harness.load_project(project_dir).agents

        assert loaded["front"].handoffs[0].handoffs[0] is loaded["front"]

    @pytest.mark.parametrize("field", ["subagents", "handoffs"])
    def test_load_project_raises_on_unknown_linked_agent(
        self, project_dir, field
    ):
        _write_agent(project_dir, "front", f"{field}: [ghost]")

        with pytest.raises(ValueError, match=f"{field} 引用不存在的 agent"):
            harness.load_project(project_dir)

    def test_load_project_raises_on_handoff_to_self(self, project_dir):
        _write_agent(project_dir, "front", "handoffs: [front]")

        with pytest.raises(ValueError, match="不能把對話轉交給自己"):
            harness.load_project(project_dir)

    def test_scaffolded_agent_has_no_links(self, project_dir):
        main = harness.load_project(project_dir).agents["main"]

        assert main.subagents == []
        assert main.handoffs == []


class TestParseFrontmatter:
    """Markdown frontmatter 的解析。"""

    def test_parse_frontmatter_splits_meta_and_body(self):
        meta, body = project.parse_frontmatter(
            "---\nname: x\n---\n內文\n---\n後面的分隔線"
        )

        assert meta == {"name": "x"}
        assert body == "內文\n---\n後面的分隔線"

    def test_parse_frontmatter_handles_plain_markdown(self):
        assert project.parse_frontmatter("只有內文\n") == ({}, "只有內文")


class TestScaffold:
    """`harness new` 的範本。"""

    def test_create_project_does_not_overwrite_existing_files(
        self, project_dir
    ):
        (project_dir / "AGENTS.md").write_text("我改過了", encoding="utf-8")

        created = scaffold.create_project(project_dir)

        assert created == []
        assert (project_dir / "AGENTS.md").read_text(encoding="utf-8") == (
            "我改過了"
        )
