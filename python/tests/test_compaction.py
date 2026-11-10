"""對話壓縮的測試，全部使用假模型，不連網。"""

import json
import os
import pathlib

import pytest

import harness
from harness import compaction
from harness import llm
from harness import scaffold
from harness import sessions
from tests import conftest

SUMMARY = "## 使用者的需求與意圖\n- 查十月營收"


def _summary_response(text=SUMMARY):
    return conftest.text_response(text)


def _long_history(session, turns=4, size=400):
    """放進幾輪很長的舊對話。"""
    for index in range(turns):
        session.append(
            {"role": "user", "content": f"問題{index} " + "字" * size}
        )
        session.append(
            {"role": "assistant", "content": f"回答{index} " + "答" * size}
        )


def _is_summary_request(request):
    return request["messages"][-1]["content"] == compaction.SUMMARY_INSTRUCTION


class OverflowError(Exception):
    """模擬 litellm 的 ContextWindowExceededError（用類別名稱辨識）。"""


OverflowError.__name__ = "ContextWindowExceededError"


class ScriptedModel(conftest.FakeModel):
    """劇本裡可以放例外，輪到時就丟出。"""

    async def __call__(self, model, messages, tools, params):
        if self.script and isinstance(self.script[0], Exception):
            self.requests.append({"messages": list(messages), "raised": True})
            raise self.script.pop(0)
        async for item in super().__call__(model, messages, tools, params):
            yield item


class TestModelHistory:
    """從對話紀錄推算模型看到的歷史。"""

    def test_history_without_records_is_unchanged(self):
        messages = [
            {"role": "user", "content": "嗨"},
            {"role": "assistant", "content": "你好"},
        ]

        assert compaction.model_history(messages) == messages

    def test_compaction_replaces_older_messages_with_checkpoint(self):
        messages = [
            {"role": "user", "content": "舊問題"},
            {"role": "assistant", "content": "舊回答"},
            {"role": "user", "content": "新問題"},
            {
                "role": "harness",
                "type": "compaction",
                "keep_from": 2,
                "summary": "摘要內容",
            },
            {"role": "assistant", "content": "新回答"},
        ]

        history = compaction.model_history(messages)

        assert [m["content"] for m in history[1:]] == ["新問題", "新回答"]
        assert history[0]["role"] == "user"
        assert (
            "<compacted-summary>\n摘要內容\n</compacted-summary>"
            in (history[0]["content"])
        )

    def test_only_latest_compaction_counts(self):
        messages = [
            {"role": "user", "content": "一"},
            {
                "role": "harness",
                "type": "compaction",
                "keep_from": 0,
                "summary": "舊",
            },
            {"role": "user", "content": "二"},
            {
                "role": "harness",
                "type": "compaction",
                "keep_from": 2,
                "summary": "新",
            },
        ]

        history = compaction.model_history(messages)

        assert "新" in history[0]["content"]
        assert [m["content"] for m in history[1:]] == ["二"]

    def test_prune_record_replaces_tool_result(self):
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "function": {"name": "read", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "很長的原文"},
            {"role": "harness", "type": "prune", "results": {"c1": "短"}},
        ]

        history = compaction.model_history(messages)

        assert history[1]["content"] == "短"
        assert messages[1]["content"] == "很長的原文"

    def test_orphan_tool_results_are_dropped(self):
        messages = [
            {"role": "tool", "tool_call_id": "gone", "content": "x"},
            {"role": "user", "content": "嗨"},
        ]

        assert compaction.model_history(messages) == [
            {"role": "user", "content": "嗨"}
        ]


class TestEstimateTokens:
    """token 估計。"""

    def test_ascii_is_about_four_chars_per_token(self):
        message = {"role": "user", "content": "a" * 400}

        assert compaction.estimate_tokens([message]) == 104

    def test_cjk_is_about_one_token_per_char(self):
        message = {"role": "user", "content": "字" * 100}

        assert compaction.estimate_tokens([message]) == 104

    def test_tool_calls_and_schemas_are_counted(self):
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"function": {"name": "add", "arguments": '{"a": 1}'}}
            ],
        }
        tools = [{"type": "function", "function": {"name": "add"}}]

        without = compaction.estimate_tokens([message])

        assert without > 4
        assert compaction.estimate_tokens([message], tools) > without

    def test_measured_usage_is_used_as_baseline(self):
        compactor = compaction.Compactor(compaction.Compaction(), None)
        history = [{"role": "user", "content": "嗨"}]
        compactor.record_usage(history, harness.Usage(1000, 50))
        history.append({"role": "user", "content": "字" * 10})

        assert compactor.estimate("", history, []) == 1050 + 14


class TestSettings:
    """設定檢查。"""

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"retain_ratio": 0.9},
            {"threshold_ratio": 1.5},
            {"context_window": 0},
            {"prune_chars": 100},
            {"summary_max_tokens": 0},
            {"max_overflow_retries": -1},
        ],
    )
    def test_invalid_settings_are_rejected(self, kwargs):
        with pytest.raises(ValueError):
            compaction.Compaction(**kwargs)


class TestAutoCompaction:
    """run 裡的自動壓縮。"""

    async def test_no_compaction_below_threshold(self, fake_model):
        model = fake_model([conftest.text_response("好")])
        settings = harness.Compaction(context_window=100_000)

        result = await harness.run(
            harness.Agent(name="a"), "嗨", compaction=settings, model_fn=model
        )

        assert result.output == "好"
        assert len(model.requests) == 1

    async def test_long_history_is_summarized_before_request(self, fake_model):
        session = sessions.Session()
        _long_history(session)
        model = fake_model([_summary_response(), conftest.text_response("好")])
        events = []

        async for event in harness.run_stream(
            harness.Agent(name="a", instructions="你是助手"),
            "新問題",
            session=session,
            compaction=harness.Compaction(context_window=2000),
            model_fn=model,
        ):
            events.append(event)

        summary_request, real_request = model.requests
        assert _is_summary_request(summary_request)
        assert summary_request["messages"][0] == {
            "role": "system",
            "content": "你是助手",
        }
        assert summary_request["params"]["max_tokens"] == 8000
        sent = real_request["messages"]
        assert sent[0]["role"] == "system"
        assert "<compacted-summary>" in sent[1]["content"]
        assert sent[-1] == {"role": "user", "content": "新問題"}
        assert len(sent) < len(session.messages)
        [event] = [e for e in events if e.type == "compaction"]
        result = event.data["result"]
        assert result.reason == "threshold"
        assert result.summarized > 0
        assert result.tokens_after < result.tokens_before

    async def test_summary_usage_is_added_to_run_usage(self, fake_model):
        session = sessions.Session()
        _long_history(session)
        model = fake_model([_summary_response(), conftest.text_response("好")])

        result = await harness.run(
            harness.Agent(name="a"),
            "新問題",
            session=session,
            compaction=harness.Compaction(context_window=2000),
            model_fn=model,
        )

        assert result.usage.input_tokens == 20

    async def test_original_messages_stay_in_session_file(
        self, fake_model, tmp_path
    ):
        session = sessions.create(tmp_path)
        _long_history(session)
        model = fake_model([_summary_response(), conftest.text_response("好")])
        await harness.run(
            harness.Agent(name="a"),
            "新問題",
            session=session,
            compaction=harness.Compaction(context_window=2000),
            model_fn=model,
        )

        reloaded = sessions.load(tmp_path, session.id)

        assert reloaded.messages[0]["content"].startswith("問題0")
        assert any(compaction.is_record(m) for m in reloaded.messages)
        history = compaction.model_history(reloaded.messages)
        assert "<compacted-summary>" in history[0]["content"]

    async def test_pruning_alone_can_avoid_summary(self, fake_model):
        big = "x" * 30_000

        @harness.tool
        def dump() -> str:
            """輸出很長的內容。"""
            return big

        model = fake_model(
            [
                conftest.tool_response(("dump", {})),
                conftest.text_response("看完了"),
                conftest.text_response("好"),
            ]
        )
        agent = harness.Agent(name="a", tools=[dump])
        session = sessions.Session()
        settings = harness.Compaction(context_window=4000)
        # 第一輪不壓縮：剛回傳、模型還沒看過的工具結果本來就不會修剪。
        await harness.run(agent, "讀", session=session, model_fn=model)

        events = []
        async for event in harness.run_stream(
            agent, "再來", session=session, compaction=settings, model_fn=model
        ):
            events.append(event)

        [event] = [e for e in events if e.type == "compaction"]
        assert event.data["result"].pruned == 1
        assert event.data["result"].summarized == 0
        assert not any(_is_summary_request(r) for r in model.requests)
        tool_message = model.requests[-1]["messages"][2]
        assert compaction.PRUNE_MARKER in tool_message["content"]
        assert len(tool_message["content"]) < 8192
        assert session.messages[2]["content"] == big

    async def test_unseen_tool_results_are_not_pruned(self, fake_model):
        big = "y" * 30_000

        @harness.tool
        def dump() -> str:
            """輸出很長的內容。"""
            return big

        model = fake_model(
            [
                conftest.tool_response(("dump", {})),
                _summary_response(),
                conftest.text_response("看完了"),
            ]
        )

        await harness.run(
            harness.Agent(name="a", tools=[dump]),
            "讀",
            compaction=harness.Compaction(context_window=4000),
            model_fn=model,
        )

        assert big in [m.get("content") for m in model.requests[-1]["messages"]]

    async def test_failed_summary_is_reported_and_run_continues(
        self, fake_model
    ):
        session = sessions.Session()
        _long_history(session)
        model = fake_model(
            [conftest.text_response(""), conftest.text_response("好")]
        )
        events = []

        async for event in harness.run_stream(
            harness.Agent(name="a"),
            "新問題",
            session=session,
            compaction=harness.Compaction(context_window=2000),
            model_fn=model,
        ):
            events.append(event)

        [failed] = [e for e in events if e.type == "compaction_failed"]
        assert "沒有回傳任何文字" in failed.data["error"]
        assert events[-1].data["result"].output == "好"
        assert not any(compaction.is_record(m) for m in session.messages)

    async def test_summary_longer_than_original_is_rejected(self, fake_model):
        session = sessions.Session()
        _long_history(session, turns=3, size=300)
        model = fake_model(
            [_summary_response("長" * 5000), conftest.text_response("好")]
        )
        events = []

        async for event in harness.run_stream(
            harness.Agent(name="a"),
            "新",
            session=session,
            compaction=harness.Compaction(context_window=1500),
            model_fn=model,
        ):
            events.append(event)

        [failed] = [e for e in events if e.type == "compaction_failed"]
        assert "沒有比原本的對話短" in failed.data["error"]

    async def test_auto_false_never_compacts(self, fake_model):
        session = sessions.Session()
        _long_history(session)
        model = fake_model([conftest.text_response("好")])

        await harness.run(
            harness.Agent(name="a"),
            "新",
            session=session,
            compaction=harness.Compaction(context_window=2000, auto=False),
            model_fn=model,
        )

        assert len(model.requests) == 1

    async def test_unknown_context_window_skips_proactive_compaction(
        self, fake_model, monkeypatch
    ):
        monkeypatch.setattr(
            compaction, "_lookup_context_window", lambda m: None
        )
        session = sessions.Session()
        _long_history(session, turns=20)
        model = fake_model([conftest.text_response("好")])

        await harness.run(
            harness.Agent(name="a"),
            "新",
            session=session,
            compaction=harness.Compaction(),
            model_fn=model,
        )

        assert len(model.requests) == 1

    async def test_compacted_skill_must_be_loaded_again(self, fake_model):
        @harness.tool
        def refund() -> str:
            """退款。"""
            return "ok"

        skill = harness.Skill(
            name="r", description="退款", instructions="步驟", tools=[refund]
        )
        agent = harness.Agent(name="a", skills=[skill])
        session = sessions.Session()
        first = fake_model(
            [
                conftest.tool_response(("load_skill", {"name": "r"})),
                conftest.text_response("載入了"),
            ]
        )
        await harness.run(agent, "退款", session=session, model_fn=first)
        _long_history(session)
        second = fake_model([_summary_response(), conftest.text_response("好")])

        await harness.run(
            agent,
            "再來",
            session=session,
            compaction=harness.Compaction(context_window=2000),
            model_fn=second,
        )

        names = [t["function"]["name"] for t in second.requests[-1]["tools"]]
        assert "refund" not in names


class TestOverflowRecovery:
    """模型回報超過上下文上限時壓縮後重試。"""

    async def test_overflow_compacts_and_retries_once(self):
        session = sessions.Session()
        _long_history(session)
        model = ScriptedModel(
            [
                OverflowError("too long"),
                _summary_response(),
                conftest.text_response("好"),
            ]
        )
        events = []

        async for event in harness.run_stream(
            harness.Agent(name="a"),
            "新問題",
            session=session,
            compaction=harness.Compaction(context_window=10**9),
            model_fn=model,
        ):
            events.append(event)

        [event] = [e for e in events if e.type == "compaction"]
        assert event.data["result"].reason == "overflow"
        retried = model.requests[-1]["messages"]
        # overflow 盡量壓縮：只保留最後一段（這輪的使用者訊息）。
        assert [m["content"] for m in retried[1:]] == ["新問題"]
        assert events[-1].data["result"].output == "好"

    async def test_overflow_is_raised_after_retries_are_used(self):
        session = sessions.Session()
        _long_history(session)
        model = ScriptedModel(
            [
                OverflowError("too long"),
                _summary_response(),
                OverflowError("still too long"),
            ]
        )

        with pytest.raises(OverflowError, match="still too long"):
            await harness.run(
                harness.Agent(name="a"),
                "新問題",
                session=session,
                compaction=harness.Compaction(context_window=10**9),
                model_fn=model,
            )

    async def test_overflow_without_compaction_is_raised(self):
        model = ScriptedModel([OverflowError("too long")])

        with pytest.raises(OverflowError):
            await harness.run(harness.Agent(name="a"), "嗨", model_fn=model)

    async def test_other_errors_are_not_retried(self):
        model = ScriptedModel([RuntimeError("rate limit")])

        with pytest.raises(RuntimeError):
            await harness.run(
                harness.Agent(name="a"),
                "嗨",
                compaction=harness.Compaction(),
                model_fn=model,
            )

        assert len(model.requests) == 1

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("This model's maximum context length is 8192 tokens", True),
            ("prompt is too long: 210000 tokens > 200000 maximum", True),
            ("Rate limit reached for tokens per min", False),
        ],
    )
    def test_is_context_overflow_reads_messages(self, message, expected):
        assert llm.is_context_overflow(RuntimeError(message)) is expected


class TestManualCompaction:
    """harness.compact。"""

    async def test_compact_summarizes_even_below_threshold(self, fake_model):
        session = sessions.Session()
        _long_history(session, turns=2, size=50)
        session.append({"role": "user", "content": "最新"})
        model = fake_model([_summary_response()])

        result = await harness.compact(
            harness.Agent(name="a"),
            session,
            settings=harness.Compaction(context_window=10**6),
            model_fn=model,
        )

        assert result.reason == "manual"
        assert result.summarized > 0
        assert _is_summary_request(model.requests[0])

    async def test_compact_returns_none_for_short_conversation(
        self, fake_model
    ):
        session = sessions.Session()
        session.append({"role": "user", "content": "嗨"})

        result = await harness.compact(
            harness.Agent(name="a"), session, model_fn=fake_model([])
        )

        assert result is None

    async def test_compact_raises_when_summary_fails(self, fake_model):
        session = sessions.Session()
        _long_history(session, turns=2, size=50)
        model = fake_model([conftest.text_response("")])

        with pytest.raises(harness.compaction.CompactionError):
            await harness.compact(
                harness.Agent(name="a"), session, model_fn=model
            )


class TestProjectConfig:
    """harness.yaml 的 compaction 區塊。"""

    @pytest.fixture
    def project_dir(self, tmp_path):
        root = tmp_path / "proj"
        scaffold.create_project(root)
        return root

    def _append(self, root, text):
        config = root / "harness.yaml"
        config.write_text(
            config.read_text(encoding="utf-8") + text, encoding="utf-8"
        )

    def test_compaction_is_on_by_default(self, project_dir):
        loaded = harness.load_project(project_dir)

        assert loaded.compaction == harness.Compaction()

    def test_compaction_can_be_turned_off(self, project_dir):
        self._append(project_dir, "compaction: false\n")

        assert harness.load_project(project_dir).compaction is None

    def test_compaction_settings_are_mapped(self, project_dir):
        self._append(
            project_dir,
            "compaction:\n  contextWindow: 32000\n  retainRatio: 0.2\n",
        )

        settings = harness.load_project(project_dir).compaction

        assert settings.context_window == 32000
        assert settings.retain_ratio == 0.2

    def test_unknown_setting_is_an_error(self, project_dir):
        self._append(project_dir, "compaction:\n  window: 1\n")

        with pytest.raises(ValueError, match="不認得的設定"):
            harness.load_project(project_dir)


# 兩個版本共用的測試資料：Python 版產生的壓縮紀錄，以及模型應該看到的
# 歷史。TypeScript 版的 tests/compaction.test.ts 讀同一份檔案，確保兩邊
# 推算的結果相同。內容改變時，用 HARNESS_UPDATE_FIXTURES=1 重新產生。
_FIXTURE_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures"
)


def _fixture_messages():
    call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path": "a.md"}'},
    }
    return [
        {"role": "user", "content": "舊問題"},
        {"role": "assistant", "content": "舊回答"},
        {"role": "user", "content": "讀 a.md"},
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "read_file",
            "content": "很長的內容",
        },
        {"role": "harness", "type": "prune", "results": {"call_1": "修剪後"}},
        {
            "role": "harness",
            "type": "compaction",
            "keep_from": 2,
            "summary": "## 使用者的需求與意圖\n- 舊問題",
            "reason": "threshold",
        },
        {"role": "assistant", "content": "讀完了"},
    ]


def test_typescript_fixture_is_up_to_date():
    messages = _fixture_messages()
    session_text = "".join(
        json.dumps(message, ensure_ascii=False) + "\n" for message in messages
    )
    history_text = (
        json.dumps(
            compaction.model_history(messages), ensure_ascii=False, indent=2
        )
        + "\n"
    )
    session_path = _FIXTURE_DIR / "compacted-session.jsonl"
    history_path = _FIXTURE_DIR / "compacted-history.json"
    if os.environ.get("HARNESS_UPDATE_FIXTURES"):
        session_path.write_text(session_text, encoding="utf-8")
        history_path.write_text(history_text, encoding="utf-8")

    assert session_path.read_text(encoding="utf-8") == session_text
    assert history_path.read_text(encoding="utf-8") == history_text
