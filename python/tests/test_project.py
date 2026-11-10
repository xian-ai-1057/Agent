"""project 與 scaffold 模組的測試。"""

import pytest

import harness
from harness import project
from harness import scaffold


@pytest.fixture
def project_dir(tmp_path):
    """用 scaffold 建立的範本專案。"""
    root = tmp_path / "demo_project"
    scaffold.create_project(root)
    return root


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

    def test_load_project_skips_files_run_as_mcp_servers(self, project_dir):
        (project_dir / "tools" / "server.py").write_text(
            "raise RuntimeError('這個檔案不應該被載入')\n", encoding="utf-8"
        )
        (project_dir / "harness.yaml").write_text(
            "mcpServers:\n  backend:\n    command: python3\n"
            "    args: [tools/server.py]\n",
            encoding="utf-8",
        )

        loaded = harness.load_project(project_dir)

        assert list(loaded.tools) == ["word_count"]

    def test_load_project_explains_server_only_toolset(self, project_dir):
        (project_dir / "harness.yaml").write_text(
            "mcpServers:\n  backend:\n    command: python3\n"
            "    args: [server.py]\n",
            encoding="utf-8",
        )
        _write_agent(project_dir, "remote", "tools: [backend]")

        with pytest.raises(ValueError, match="只有 TypeScript 版"):
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
