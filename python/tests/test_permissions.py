"""權限的測試。"""

import pytest

import harness
from harness import permissions
from tests import conftest


@pytest.fixture
def risky_tool():
    """需要使用者同意的工具，會記錄被執行的次數。"""
    calls = []

    @harness.tool(needs_approval=True)
    def send_mail(to: str) -> str:
        """寄信。"""
        calls.append(to)
        return "已寄出"

    send_mail.calls = calls
    return send_mail


class TestCheck:
    """不詢問使用者的判斷。"""

    @pytest.mark.parametrize(
        "mode, expected",
        [("ask", "ask"), ("bypass", "allow"), ("dontAsk", "deny")],
    )
    def test_check_follows_mode_for_risky_tool(
        self, risky_tool, mode, expected
    ):
        assert permissions.check(harness.Permissions(mode), risky_tool) == (
            expected
        )

    @pytest.mark.parametrize("mode", permissions.MODES)
    def test_check_allows_safe_tool_in_every_mode(self, add_tool, mode):
        assert permissions.check(harness.Permissions(mode), add_tool) == (
            "allow"
        )

    def test_check_allow_rule_skips_asking(self, risky_tool):
        rules = harness.Permissions(allow=["send_*"])

        assert permissions.check(rules, risky_tool) == "allow"

    def test_check_deny_rule_beats_allow_rule_and_bypass(self, risky_tool):
        rules = harness.Permissions(
            "bypass", allow=["send_mail"], deny=["send_*"]
        )

        assert permissions.check(rules, risky_tool) == "deny"

    def test_check_ask_rule_makes_safe_tool_require_approval(self, add_tool):
        rules = harness.Permissions(ask=["a??"])

        assert permissions.check(rules, add_tool) == "ask"

    def test_check_ask_rule_is_denied_in_dont_ask_mode(self, add_tool):
        rules = harness.Permissions("dontAsk", ask=["add"])

        assert permissions.check(rules, add_tool) == "deny"

    def test_permissions_rejects_unknown_mode(self):
        with pytest.raises(ValueError, match="權限模式"):
            harness.Permissions("yolo")


class TestInLoop:
    """權限在主迴圈裡的行為。"""

    def _script(self):
        return [
            conftest.tool_response(("send_mail", {"to": "a@b.c"})),
            conftest.text_response("完成"),
        ]

    async def _run(self, risky_tool, fake_model, rules, hooks=None):
        model = fake_model(self._script())
        agent = harness.Agent(name="a", tools=[risky_tool])
        await harness.run(
            agent, "go", permissions=rules, hooks=hooks, model_fn=model
        )
        return model.requests[1]["messages"][-1]["content"]

    async def test_run_executes_tool_when_user_approves(
        self, risky_tool, fake_model
    ):
        async def approve(call, target):
            return True

        rules = harness.Permissions(approver=approve)

        content = await self._run(risky_tool, fake_model, rules)

        assert content == "已寄出"
        assert risky_tool.calls == ["a@b.c"]

    async def test_run_skips_tool_when_user_declines(
        self, risky_tool, fake_model
    ):
        async def decline(call, target):
            return False

        rules = harness.Permissions(approver=decline)

        content = await self._run(risky_tool, fake_model, rules)

        assert "使用者沒有同意" in content
        assert risky_tool.calls == []

    async def test_run_declines_when_no_approver_is_set(
        self, risky_tool, fake_model
    ):
        content = await self._run(risky_tool, fake_model, harness.Permissions())

        assert "使用者沒有同意" in content
        assert risky_tool.calls == []

    async def test_run_hook_allow_skips_asking_user(
        self, risky_tool, fake_model
    ):
        hooks = harness.Hooks()
        hooks.add("pre_tool_use", lambda call, context: harness.allow())

        content = await self._run(
            risky_tool, fake_model, harness.Permissions(), hooks
        )

        assert content == "已寄出"

    async def test_run_hook_allow_cannot_override_deny_rule(
        self, risky_tool, fake_model
    ):
        hooks = harness.Hooks()
        hooks.add("pre_tool_use", lambda call, context: harness.allow())
        rules = harness.Permissions(deny=["send_mail"])

        content = await self._run(risky_tool, fake_model, rules, hooks)

        assert "權限設定不允許" in content
        assert risky_tool.calls == []
