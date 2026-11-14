"""共用的 fixture 與測試輔助函式。"""

import json
from typing import Any

import pytest

import harness
from harness import datatypes
from harness import llm


def text_response(text: str, finish_reason: str = "stop") -> Any:
    """建立一個只有文字的模型回應。"""
    return datatypes.ModelResponse(
        message={"role": "assistant", "content": text},
        tool_calls=[],
        usage=datatypes.Usage(input_tokens=10, output_tokens=5),
        finish_reason=finish_reason,
    )


def tool_response(*calls: tuple[str, dict[str, Any]]) -> Any:
    """建立一個要求呼叫工具的模型回應。

    Args:
        *calls: 每個元素是 (工具名稱, 參數)。
    """
    tool_calls = [
        datatypes.ToolCall(f"call_{index}_{name}", name, arguments)
        for index, (name, arguments) in enumerate(calls)
    ]
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for call in tool_calls
        ],
    }
    return datatypes.ModelResponse(
        message=message,
        tool_calls=tool_calls,
        usage=datatypes.Usage(input_tokens=10, output_tokens=5),
    )


class FakeModel:
    """依照劇本回應的假模型，並記錄每次收到的請求。

    劇本裡的例外會在輪到它時丟出（模擬模型中台拒絕請求）。請求裡另外
    記下當時的 llm.current_caller()（附在模型呼叫上的使用者身分）。
    """

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    async def __call__(self, model, messages, tools, params):
        self.requests.append(
            {
                "model": model,
                "messages": [dict(message) for message in messages],
                "tools": tools,
                "params": params,
                "caller": llm.current_caller(),
            }
        )
        response = self.script.pop(0)
        if isinstance(response, BaseException):
            raise response
        content = response.message.get("content")
        if content:
            yield content
        yield response


@pytest.fixture
def fake_model():
    """回傳建立 FakeModel 的函式。"""
    return FakeModel


@pytest.fixture
def add_tool():
    """一個簡單的加法工具。"""

    @harness.tool
    def add(a: int, b: int) -> int:
        """把兩個整數相加。

        Args:
            a: 第一個數。
            b: 第二個數。
        """
        return a + b

    return add


@pytest.fixture
def context(tmp_path):
    """以臨時資料夾為專案根目錄的 RunContext。"""
    return datatypes.RunContext(
        agent_name="test", workdir=tmp_path, session_id="s1"
    )
