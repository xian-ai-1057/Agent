"""llm 模組的測試。

使用 litellm 內建的 mock_response，走完整的 litellm 流程但不連網。
"""

import litellm

from harness import datatypes
from harness import llm

_MESSAGES = [{"role": "user", "content": "嗨"}]
_MOCK_CALL = {
    "id": "call_1",
    "type": "function",
    "function": {"name": "add", "arguments": '{"a": 1, "b": 2}'},
}


def _mock_completion(**kwargs):
    return litellm.completion(model="gpt-4o", messages=_MESSAGES, **kwargs)


def test_default_model_reads_environment(monkeypatch):
    monkeypatch.setenv("HARNESS_MODEL", "ollama/hermes3")

    assert llm.default_model() == "ollama/hermes3"


def test_default_model_falls_back_when_unset(monkeypatch):
    monkeypatch.delenv("HARNESS_MODEL", raising=False)

    assert llm.default_model() == llm.FALLBACK_MODEL


async def test_complete_streams_text_then_final_response():
    items = [
        item
        async for item in llm.complete(
            "gpt-4o", _MESSAGES, [], {"mock_response": "你好，世界"}
        )
    ]

    deltas, response = items[:-1], items[-1]
    assert "".join(deltas) == "你好，世界"
    assert len(deltas) > 1
    assert isinstance(response, datatypes.ModelResponse)
    assert response.message == {"role": "assistant", "content": "你好，世界"}
    assert response.usage.output_tokens > 0


def test_to_model_response_parses_tool_calls():
    response = llm.to_model_response(
        _mock_completion(mock_tool_calls=[_MOCK_CALL])
    )

    assert response.tool_calls == [
        datatypes.ToolCall("call_1", "add", {"a": 1, "b": 2})
    ]
    assert response.message["tool_calls"] == [_MOCK_CALL]


def test_to_model_response_flags_invalid_json_arguments():
    broken = {**_MOCK_CALL, "function": {"name": "add", "arguments": "{a:"}}

    response = llm.to_model_response(_mock_completion(mock_tool_calls=[broken]))

    assert response.tool_calls[0].parse_error
    assert response.tool_calls[0].arguments == {}


def test_to_model_response_treats_empty_arguments_as_empty_object():
    empty = {**_MOCK_CALL, "function": {"name": "ping", "arguments": ""}}

    response = llm.to_model_response(_mock_completion(mock_tool_calls=[empty]))

    assert response.tool_calls[0].arguments == {}
    assert response.tool_calls[0].parse_error is None


def test_to_model_response_keeps_thinking_blocks():
    completion = _mock_completion(mock_response="答案")
    blocks = [{"type": "thinking", "thinking": "…", "signature": "sig"}]
    completion.choices[0].message.thinking_blocks = blocks
    completion.choices[0].message.reasoning_content = "…"

    message = llm.to_model_response(completion).message

    assert message["thinking_blocks"] == blocks
    assert "reasoning_content" not in message
