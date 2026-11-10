"""llm 模組的測試。

使用 litellm 內建的 mock_response，走完整的 litellm 流程但不連網。
"""

import http.server
import json
import threading

import litellm
import pytest

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


class _FakeEndpoint(http.server.BaseHTTPRequestHandler):
    """假的 OpenAI 相容端點：把工具參數分段串流回來。"""

    received: list[dict] = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(length))
        type(self).received.append(
            {
                "body": body,
                "auth": self.headers.get("Authorization"),
                "path": self.path,
            }
        )
        if self.path.endswith("/embeddings"):
            self._send_embeddings(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        first = {
            "index": 0,
            "id": "call_1",
            "type": "function",
            "function": {"name": "add", "arguments": ""},
        }
        deltas = [
            {"role": "assistant", "content": "先算"},
            {"tool_calls": [first]},
            {"tool_calls": [{"index": 0, "function": {"arguments": '{"a":'}}]},
            {"tool_calls": [{"index": 0, "function": {"arguments": " 1}"}}]},
        ]
        for delta in deltas:
            self._send({"choices": [{"index": 0, "delta": delta}]})
        finish = {"index": 0, "delta": {}, "finish_reason": "tool_calls"}
        self._send({"choices": [finish]})
        self.wfile.write(b"data: [DONE]\n\n")

    def _send_embeddings(self, body):
        """依輸入長度回傳向量，並故意把順序倒過來測試排序。"""
        rows = [
            {"object": "embedding", "index": index, "embedding": [len(text)]}
            for index, text in enumerate(body["input"])
        ]
        payload = {
            "object": "list",
            "data": list(reversed(rows)),
            "model": body["model"],
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        }
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send(self, payload):
        chunk = {"id": "c", "object": "chat.completion.chunk", **payload}
        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.flush()


@pytest.fixture
def endpoint(monkeypatch):
    """啟動假端點，並把 HARNESS_BASE_URL 指向它。"""
    _FakeEndpoint.received = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeEndpoint)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv(
        "HARNESS_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1"
    )
    monkeypatch.setenv("HARNESS_API_KEY", "test-key")
    yield _FakeEndpoint
    server.shutdown()


async def test_complete_uses_openai_compatible_endpoint(endpoint):
    items = [
        item
        async for item in llm.complete(
            "my-model", _MESSAGES, [{"type": "function"}], {"max_tokens": 9}
        )
    ]

    response = items[-1]
    assert items[:-1] == ["先算"]
    assert response.tool_calls == [
        datatypes.ToolCall("call_1", "add", {"a": 1})
    ]
    sent = endpoint.received[0]
    assert sent["auth"] == "Bearer test-key"
    assert sent["body"]["model"] == "my-model"
    assert sent["body"]["stream"] is True
    assert sent["body"]["max_tokens"] == 9


def test_endpoint_options_are_empty_without_base_url(monkeypatch):
    monkeypatch.delenv("HARNESS_BASE_URL", raising=False)

    assert llm._endpoint_options() == {}


def test_endpoint_options_allow_missing_api_key(monkeypatch):
    monkeypatch.setenv("HARNESS_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.delenv("HARNESS_API_KEY", raising=False)

    assert llm._endpoint_options()["api_key"] == "not-needed"


async def test_embed_uses_endpoint_in_batches_and_keeps_order(
    endpoint, monkeypatch
):
    monkeypatch.setattr(llm, "EMBED_BATCH_SIZE", 2)

    vectors = await llm.embed("my-embedder", ["a", "bb", "ccc"])

    assert vectors == [[1.0], [2.0], [3.0]]
    assert [item["path"] for item in endpoint.received] == [
        "/v1/embeddings",
        "/v1/embeddings",
    ]
    assert endpoint.received[0]["body"]["input"] == ["a", "bb"]
    assert endpoint.received[0]["auth"] == "Bearer test-key"
