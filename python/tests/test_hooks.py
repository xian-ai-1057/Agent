"""hooks 的測試。"""

import pytest

import harness
from tests import conftest


def _script():
    return [
        conftest.tool_response(("add", {"a": 1, "b": 2})),
        conftest.text_response("完成"),
    ]


async def test_pre_tool_use_deny_blocks_tool(fake_model):
    executed = []

    @harness.tool
    def add(a: int, b: int) -> int:
        """相加。"""
        executed.append((a, b))
        return a + b

    hooks = harness.Hooks()
    hooks.add("pre_tool_use", lambda call, context: harness.deny("不准算"))
    model = fake_model(_script())

    await harness.run(
        harness.Agent(name="a", tools=[add]), "go", hooks=hooks, model_fn=model
    )

    assert executed == []
    assert "不准算" in model.requests[1]["messages"][-1]["content"]


async def test_pre_tool_use_can_rewrite_arguments(fake_model, add_tool):
    hooks = harness.Hooks()

    @hooks.on("pre_tool_use")
    def force_b(call, context):
        call.arguments["b"] = 100

    model = fake_model(_script())

    await harness.run(
        harness.Agent(name="a", tools=[add_tool]),
        "go",
        hooks=hooks,
        model_fn=model,
    )

    assert model.requests[1]["messages"][-1]["content"] == "101"


async def test_post_tool_use_can_rewrite_output(fake_model, add_tool):
    hooks = harness.Hooks()

    @hooks.on("post_tool_use")
    async def redact(call, result, context):
        result.output = "[已遮蔽]"

    model = fake_model(_script())

    await harness.run(
        harness.Agent(name="a", tools=[add_tool]),
        "go",
        hooks=hooks,
        model_fn=model,
    )

    assert model.requests[1]["messages"][-1]["content"] == "[已遮蔽]"


async def test_run_start_and_run_end_hooks_fire_once(fake_model):
    hooks = harness.Hooks()
    seen = []
    hooks.add("run_start", lambda text, context: seen.append(("start", text)))
    hooks.add(
        "run_end", lambda result, context: seen.append(("end", result.output))
    )
    model = fake_model([conftest.text_response("你好")])

    await harness.run(
        harness.Agent(name="a"), "嗨", hooks=hooks, model_fn=model
    )

    assert seen == [("start", "嗨"), ("end", "你好")]


def test_add_rejects_unknown_event():
    with pytest.raises(ValueError, match="沒有 nope 這個事件"):
        harness.Hooks().add("nope", print)
