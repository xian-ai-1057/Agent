"""主迴圈的測試，全部使用假模型，不連網。"""

import asyncio

import pytest

import harness
from tests import conftest


async def test_run_returns_text_when_model_calls_no_tools(fake_model):
    agent = harness.Agent(name="a", instructions="你是助手")
    model = fake_model([conftest.text_response("你好")])

    result = await harness.run(agent, "嗨", model_fn=model)

    assert result.output == "你好"
    assert result.stop_reason == "end_turn"
    assert result.turns == 1


async def test_run_sends_system_prompt_and_user_message(fake_model):
    agent = harness.Agent(name="a", instructions="你是助手")
    model = fake_model([conftest.text_response("你好")])

    await harness.run(agent, "嗨", model_fn=model)

    assert model.requests[0]["messages"] == [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "嗨"},
    ]


async def test_run_executes_tool_and_feeds_result_back(fake_model, add_tool):
    agent = harness.Agent(name="a", tools=[add_tool])
    model = fake_model(
        [
            conftest.tool_response(("add", {"a": 1, "b": 2})),
            conftest.text_response("答案是 3"),
        ]
    )

    result = await harness.run(agent, "1+2?", model_fn=model)

    tool_message = model.requests[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["content"] == "3"
    assert result.output == "答案是 3"
    assert result.turns == 2


async def test_run_accumulates_usage_across_turns(fake_model, add_tool):
    agent = harness.Agent(name="a", tools=[add_tool])
    model = fake_model(
        [
            conftest.tool_response(("add", {"a": 1, "b": 2})),
            conftest.text_response("3"),
        ]
    )

    result = await harness.run(agent, "1+2?", model_fn=model)

    assert result.usage.input_tokens == 20
    assert result.usage.output_tokens == 10


async def test_run_executes_parallel_tool_calls_concurrently(fake_model):
    running = 0
    peak = 0

    @harness.tool
    async def slow(label: str) -> str:
        """慢速工具。"""
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return label

    agent = harness.Agent(name="a", tools=[slow])
    model = fake_model(
        [
            conftest.tool_response(
                ("slow", {"label": "甲"}), ("slow", {"label": "乙"})
            ),
            conftest.text_response("完成"),
        ]
    )

    await harness.run(agent, "go", model_fn=model)

    outputs = [m["content"] for m in model.requests[1]["messages"][-2:]]
    assert peak == 2
    assert outputs == ["甲", "乙"]


async def test_run_reports_tool_exception_to_model(fake_model):
    @harness.tool
    def explode() -> str:
        """一定會失敗。"""
        raise RuntimeError("資料庫連不上")

    agent = harness.Agent(name="a", tools=[explode])
    model = fake_model(
        [
            conftest.tool_response(("explode", {})),
            conftest.text_response("抱歉"),
        ]
    )

    result = await harness.run(agent, "go", model_fn=model)

    content = model.requests[1]["messages"][-1]["content"]
    assert content == "[錯誤] RuntimeError: 資料庫連不上"
    assert result.output == "抱歉"


async def test_run_reports_unknown_tool_to_model(fake_model, add_tool):
    agent = harness.Agent(name="a", tools=[add_tool])
    model = fake_model(
        [conftest.tool_response(("nope", {})), conftest.text_response("好")]
    )

    await harness.run(agent, "go", model_fn=model)

    content = model.requests[1]["messages"][-1]["content"]
    assert "沒有 nope 這個工具" in content
    assert "add" in content


async def test_run_reports_invalid_arguments_to_model(fake_model, add_tool):
    agent = harness.Agent(name="a", tools=[add_tool])
    model = fake_model(
        [
            conftest.tool_response(("add", {"a": "不是數字", "b": 2})),
            conftest.text_response("好"),
        ]
    )

    await harness.run(agent, "go", model_fn=model)

    content = model.requests[1]["messages"][-1]["content"]
    assert content.startswith("[錯誤] ValidationError")


async def test_run_reports_unparseable_arguments_to_model(fake_model, add_tool):
    response = conftest.tool_response(("add", {}))
    response.tool_calls[0].parse_error = "Expecting value"
    agent = harness.Agent(name="a", tools=[add_tool])
    model = fake_model([response, conftest.text_response("好")])

    await harness.run(agent, "go", model_fn=model)

    content = model.requests[1]["messages"][-1]["content"]
    assert "不是合法的 JSON" in content


async def test_run_stops_at_max_turns(fake_model, add_tool):
    agent = harness.Agent(name="a", tools=[add_tool], max_turns=2)
    model = fake_model([conftest.tool_response(("add", {"a": 1, "b": 1}))] * 3)

    result = await harness.run(agent, "go", model_fn=model)

    assert result.stop_reason == "max_turns"
    assert result.turns == 2
    assert result.messages[-1]["role"] == "tool"


async def test_run_maps_length_finish_reason_to_max_tokens(fake_model):
    agent = harness.Agent(name="a")
    model = fake_model([conftest.text_response("被截斷", "length")])

    result = await harness.run(agent, "go", model_fn=model)

    assert result.stop_reason == "max_tokens"


async def test_run_continues_conversation_with_same_session(fake_model):
    agent = harness.Agent(name="a")
    session = harness.Session()
    model = fake_model(
        [conftest.text_response("第一次"), conftest.text_response("第二次")]
    )

    await harness.run(agent, "一", session=session, model_fn=model)
    await harness.run(agent, "二", session=session, model_fn=model)

    roles = [m["role"] for m in model.requests[1]["messages"]]
    assert roles == ["user", "assistant", "user"]


async def test_run_closes_tool_calls_left_by_interrupted_run(
    fake_model, add_tool
):
    interrupted = conftest.tool_response(("add", {"a": 1, "b": 2}))
    session = harness.Session(
        messages=[{"role": "user", "content": "1+2?"}, interrupted.message]
    )
    agent = harness.Agent(name="a", tools=[add_tool])
    model = fake_model([conftest.text_response("好")])

    await harness.run(agent, "還在嗎", session=session, model_fn=model)

    sent = model.requests[0]["messages"]
    assert sent[2]["role"] == "tool"
    assert sent[2]["tool_call_id"] == interrupted.tool_calls[0].id
    assert "中斷" in sent[2]["content"]
    assert sent[3] == {"role": "user", "content": "還在嗎"}


async def test_run_passes_deps_to_tools(fake_model):
    @harness.tool
    def current_user(context: harness.RunContext) -> str:
        """取得目前的使用者。"""
        return context.deps["user"]

    agent = harness.Agent(name="a", tools=[current_user])
    model = fake_model(
        [
            conftest.tool_response(("current_user", {})),
            conftest.text_response("好"),
        ]
    )

    await harness.run(agent, "我是誰", deps={"user": "小明"}, model_fn=model)

    assert model.requests[1]["messages"][-1]["content"] == "小明"


async def test_run_uses_agent_model_and_params(fake_model):
    agent = harness.Agent(
        name="a", model="openai/gpt-x", model_params={"max_tokens": 99}
    )
    model = fake_model([conftest.text_response("好")])

    await harness.run(agent, "go", model_fn=model)

    assert model.requests[0]["model"] == "openai/gpt-x"
    assert model.requests[0]["params"] == {"max_tokens": 99}


async def test_run_falls_back_to_env_model(fake_model, monkeypatch):
    monkeypatch.setenv("HARNESS_MODEL", "ollama/hermes3")
    model = fake_model([conftest.text_response("好")])

    await harness.run(harness.Agent(name="a"), "go", model_fn=model)

    assert model.requests[0]["model"] == "ollama/hermes3"


async def test_run_stream_yields_events_in_order(fake_model, add_tool):
    agent = harness.Agent(name="a", tools=[add_tool])
    model = fake_model(
        [
            conftest.tool_response(("add", {"a": 1, "b": 2})),
            conftest.text_response("答案是 3"),
        ]
    )

    types = [
        event.type
        async for event in harness.run_stream(agent, "1+2?", model_fn=model)
    ]

    assert types == [
        "message",
        "tool_call",
        "tool_result",
        "text_delta",
        "message",
        "run_end",
    ]


def test_run_sync_works_without_event_loop(fake_model):
    model = fake_model([conftest.text_response("你好")])

    result = harness.run_sync(harness.Agent(name="a"), "嗨", model_fn=model)

    assert result.output == "你好"


def test_agent_rejects_duplicate_tool_names(add_tool):
    with pytest.raises(ValueError, match="重複"):
        harness.Agent(name="a", tools=[add_tool, add_tool])
