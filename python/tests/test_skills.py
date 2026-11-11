"""Skills 的測試。"""

import pytest

import harness
from harness import scaffold
from harness import sessions
from tests import conftest


def _tool_names(model, index):
    return [item["function"]["name"] for item in model.requests[index]["tools"]]


def _last_content(model, index):
    return model.requests[index]["messages"][-1]["content"]


@pytest.fixture
def refund_tool():
    @harness.tool
    def issue_refund(order_id: str) -> str:
        """退款。"""
        return f"{order_id} 已退款"

    return issue_refund


@pytest.fixture
def refund_skill(refund_tool, tmp_path):
    folder = tmp_path / "refund"
    (folder / "docs").mkdir(parents=True)
    (folder / "SKILL.md").write_text("ignored", encoding="utf-8")
    (folder / "policy.md").write_text("七天內可退", encoding="utf-8")
    (folder / "docs" / "faq.md").write_text("常見問題", encoding="utf-8")
    return harness.Skill(
        name="refund",
        description="處理退款申請",
        instructions="先確認訂單再退款。",
        tools=[refund_tool],
        root=folder,
    )


class TestSkillsInRun:
    """agent 執行時的 skill 行為。"""

    async def test_system_prompt_lists_skills_but_not_instructions(
        self, refund_skill, fake_model
    ):
        agent = harness.Agent(
            name="a", instructions="你是客服", skills=[refund_skill]
        )
        model = fake_model([conftest.text_response("好")])

        await harness.run(agent, "嗨", model_fn=model)

        system = model.requests[0]["messages"][0]["content"]
        assert system.startswith("你是客服")
        assert "- refund：處理退款申請" in system
        assert "先確認訂單再退款" not in system

    async def test_skill_tools_are_hidden_until_loaded(
        self, refund_skill, fake_model
    ):
        agent = harness.Agent(name="a", skills=[refund_skill])
        model = fake_model(
            [
                conftest.tool_response(("load_skill", {"name": "refund"})),
                conftest.tool_response(("issue_refund", {"order_id": "A1"})),
                conftest.text_response("已退款"),
            ]
        )

        result = await harness.run(agent, "我要退 A1", model_fn=model)

        assert _tool_names(model, 0) == ["load_skill", "read_skill_file"]
        assert "issue_refund" in _tool_names(model, 1)
        assert _last_content(model, 2) == "A1 已退款"
        assert result.output == "已退款"

    async def test_load_skill_returns_instructions_tools_and_files(
        self, refund_skill, fake_model
    ):
        agent = harness.Agent(name="a", skills=[refund_skill])
        model = fake_model(
            [
                conftest.tool_response(("load_skill", {"name": "refund"})),
                conftest.text_response("好"),
            ]
        )

        await harness.run(agent, "退款", model_fn=model)

        loaded = _last_content(model, 1)
        assert loaded.startswith("# 技能：refund\n")
        assert "先確認訂單再退款。" in loaded
        assert "issue_refund" in loaded
        assert "- docs/faq.md" in loaded
        assert "- policy.md" in loaded
        assert "SKILL.md" not in loaded

    async def test_loaded_skill_tools_survive_session_resume(
        self, refund_skill, fake_model
    ):
        agent = harness.Agent(name="a", skills=[refund_skill])
        session = sessions.Session()
        first = fake_model(
            [
                conftest.tool_response(("load_skill", {"name": "refund"})),
                conftest.text_response("請給訂單號"),
            ]
        )
        await harness.run(agent, "退款", session=session, model_fn=first)
        second = fake_model([conftest.text_response("好")])

        await harness.run(agent, "A1", session=session, model_fn=second)

        assert "issue_refund" in _tool_names(second, 0)

    async def test_failed_load_does_not_unlock_tools(
        self, refund_skill, fake_model
    ):
        agent = harness.Agent(name="a", skills=[refund_skill])
        model = fake_model(
            [
                conftest.tool_response(("load_skill", {"name": "nope"})),
                conftest.text_response("沒有"),
            ]
        )

        await harness.run(agent, "?", model_fn=model)

        assert "沒有 nope 這個技能" in _last_content(model, 1)
        assert "issue_refund" not in _tool_names(model, 1)

    async def test_read_skill_file_reads_listed_files_only(
        self, refund_skill, fake_model
    ):
        agent = harness.Agent(name="a", skills=[refund_skill])
        model = fake_model(
            [
                conftest.tool_response(
                    ("read_skill_file", {"name": "refund", "path": "policy.md"})
                ),
                conftest.tool_response(
                    ("read_skill_file", {"name": "refund", "path": "../x"})
                ),
                conftest.text_response("好"),
            ]
        )

        await harness.run(agent, "政策？", model_fn=model)

        assert _last_content(model, 1) == "七天內可退"
        assert "沒有 ../x 這個參考檔案" in _last_content(model, 2)

    async def test_skill_without_files_has_no_read_tool(self, fake_model):
        skill = harness.Skill(
            name="tone", description="語氣", instructions="親切"
        )
        model = fake_model([conftest.text_response("好")])

        await harness.run(
            harness.Agent(name="a", skills=[skill]), "嗨", model_fn=model
        )

        assert _tool_names(model, 0) == ["load_skill"]

    def test_agent_rejects_duplicate_skill_names(self):
        skill = harness.Skill(name="x", description="d", instructions="i")

        with pytest.raises(ValueError, match="skill"):
            harness.Agent(name="a", skills=[skill, skill])


class TestSkillsInProject:
    """從專案資料夾載入 skill。"""

    @pytest.fixture
    def project_dir(self, tmp_path):
        root = tmp_path / "shop"
        scaffold.create_project(root)
        (root / "tools" / "orders.py").write_text(
            "import harness\n\n\n"
            "@harness.tool\n"
            "def issue_refund(order_id: str) -> str:\n"
            '    """退款。"""\n'
            '    return "ok"\n',
            encoding="utf-8",
        )
        folder = root / "skills" / "refund"
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            "---\ndescription: 處理退款\ntools: [issue_refund]\n---\n"
            "先查訂單。",
            encoding="utf-8",
        )
        (folder / "policy.md").write_text("七天", encoding="utf-8")
        return root

    def _write_agent(self, root, name, frontmatter):
        (root / "agents" / f"{name}.md").write_text(
            f"---\n{frontmatter}\n---\n你是助手\n", encoding="utf-8"
        )

    def test_skills_folder_is_loaded(self, project_dir):
        loaded = harness.load_project(project_dir)

        skill = loaded.skills["refund"]
        assert skill.description == "處理退款"
        assert skill.instructions == "先查訂單。"
        assert [item.name for item in skill.tools] == ["issue_refund"]
        assert skill.resource_files() == ["policy.md"]

    def test_agent_without_skills_key_gets_all_skills(self, project_dir):
        self._write_agent(project_dir, "open", "name: open")

        agent = harness.load_project(project_dir).agents["open"]

        assert [item.name for item in agent.skills] == ["refund"]
        # 屬於 skill 的工具要等載入後才開放。
        assert "issue_refund" not in [item.name for item in agent.tools]

    def test_agent_can_opt_out_of_skills(self, project_dir):
        self._write_agent(project_dir, "plain", "name: plain\nskills: []")

        agent = harness.load_project(project_dir).agents["plain"]

        assert agent.skills == []
        assert "issue_refund" in [item.name for item in agent.tools]

    def test_unknown_skill_is_an_error(self, project_dir):
        self._write_agent(project_dir, "bad", "name: bad\nskills: [nope]")

        with pytest.raises(ValueError, match="不存在的 skill：nope"):
            harness.load_project(project_dir)

    def test_skill_without_description_is_an_error(self, project_dir):
        (project_dir / "skills" / "refund" / "SKILL.md").write_text(
            "先查訂單。", encoding="utf-8"
        )

        with pytest.raises(ValueError, match="缺少 description"):
            harness.load_project(project_dir)

    def test_builtin_skills_are_opt_in(self, project_dir):
        self._write_agent(project_dir, "open", "name: open")

        agent = harness.load_project(project_dir).agents["open"]

        assert "code-review" not in [item.name for item in agent.skills]

    def test_agent_can_use_builtin_skill_by_name(self, project_dir):
        self._write_agent(
            project_dir, "dev", "name: dev\nskills: [refund, code-review]"
        )

        agent = harness.load_project(project_dir).agents["dev"]

        assert [item.name for item in agent.skills] == ["refund", "code-review"]
        review = agent.skills[1]
        assert "checklist.md" in review.resource_files()
        # 內建 skill 的專屬工具來自內建 toolsets，載入前不會給 agent。
        assert "run_command" in [item.name for item in review.tools]
        assert "run_command" not in [item.name for item in agent.tools]

    def test_project_skill_overrides_builtin_with_same_name(self, project_dir):
        folder = project_dir / "skills" / "planning"
        folder.mkdir()
        (folder / "SKILL.md").write_text(
            "---\ndescription: 我們自己的規劃方式\n---\n照公司流程。",
            encoding="utf-8",
        )
        self._write_agent(project_dir, "dev", "name: dev\nskills: [planning]")

        agent = harness.load_project(project_dir).agents["dev"]

        assert agent.skills[0].description == "我們自己的規劃方式"

    def test_unknown_skill_error_lists_builtin_skills(self, project_dir):
        self._write_agent(project_dir, "bad", "name: bad\nskills: [nope]")

        with pytest.raises(ValueError, match="內建的有：.*code-review"):
            harness.load_project(project_dir)


def test_builtin_skills_are_complete():
    loaded = harness.builtin_skills()

    assert sorted(loaded) == [
        "code-review",
        "debugging",
        "git-commit",
        "planning",
        "skill-creator",
        "web-research",
    ]
    for skill in loaded.values():
        assert skill.description
        assert skill.instructions
        assert skill.tools, f"{skill.name} 沒有專屬工具"


def test_resource_files_work_under_hidden_parent_folder(tmp_path):
    folder = tmp_path / ".config" / "skills" / "tone"
    (folder / ".cache").mkdir(parents=True)
    (folder / "SKILL.md").write_text("x", encoding="utf-8")
    (folder / "guide.md").write_text("x", encoding="utf-8")
    (folder / ".cache" / "tmp.md").write_text("x", encoding="utf-8")
    skill = harness.Skill(
        name="t", description="d", instructions="", root=folder
    )

    assert skill.resource_files() == ["guide.md"]
