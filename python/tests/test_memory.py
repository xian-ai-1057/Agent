"""長期記憶的測試。"""

import pytest

import harness
from harness import memory
from harness import sessions
from tests import conftest


@pytest.fixture
def store(tmp_path):
    """存在臨時資料夾裡的記憶。"""
    return memory.MemoryStore(tmp_path / "memory" / "user.json")


def _system_prompt(model, index):
    return model.requests[index]["messages"][0]["content"]


def _tool_names(model, index):
    return [item["function"]["name"] for item in model.requests[index]["tools"]]


class TestMemoryStore:
    """記憶檔案的讀寫。"""

    def test_items_is_empty_before_anything_is_saved(self, store):
        assert store.items() == []

    def test_add_persists_across_instances(self, store):
        store.add("  偏好表格  ")

        reopened = memory.MemoryStore(store.path)

        assert [item.content for item in reopened.items()] == ["偏好表格"]

    def test_remove_deletes_only_that_item(self, store):
        first = store.add("一")
        store.add("二")

        removed = store.remove(first.id)

        assert removed.content == "一"
        assert [item.content for item in store.items()] == ["二"]

    def test_remove_unknown_id_raises_key_error(self, store):
        with pytest.raises(KeyError):
            store.remove("nope")

    def test_clear_returns_count_and_empties_store(self, store):
        store.add("一")
        store.add("二")

        assert store.clear() == 2
        assert store.items() == []

    @pytest.mark.parametrize(
        ("content", "message"), [("   ", "空的"), ("字" * 501, "最多")]
    )
    def test_add_rejects_invalid_content(self, store, content, message):
        with pytest.raises(ValueError, match=message):
            store.add(content)

    def test_add_rejects_when_full(self, store, monkeypatch):
        monkeypatch.setattr(memory, "MAX_ITEMS", 1)
        store.add("一")

        with pytest.raises(ValueError, match="上限"):
            store.add("二")


class TestMemoryInRun:
    """把記憶傳給 run。"""

    async def test_memories_are_in_system_prompt(self, store, fake_model):
        item = store.add("使用者叫小明")
        model = fake_model([conftest.text_response("嗨小明")])

        await harness.run(
            harness.Agent(name="a", instructions="你是助手"),
            "嗨",
            memory=store,
            model_fn=model,
        )

        system = _system_prompt(model, 0)
        assert system.startswith("你是助手")
        assert f"[{item.id}] 使用者叫小明" in system
        assert {"remember", "forget"} <= set(_tool_names(model, 0))

    async def test_remember_is_visible_in_next_request(self, store, fake_model):
        model = fake_model(
            [
                conftest.tool_response(("remember", {"content": "喜歡貓"})),
                conftest.text_response("記住了"),
            ]
        )

        await harness.run(
            harness.Agent(name="a"),
            "記住我喜歡貓",
            memory=store,
            model_fn=model,
        )

        assert [item.content for item in store.items()] == ["喜歡貓"]
        assert "喜歡貓" not in _system_prompt(model, 0)
        assert "喜歡貓" in _system_prompt(model, 1)

    async def test_forget_removes_memory(self, store, fake_model):
        item = store.add("住台北")
        model = fake_model(
            [
                conftest.tool_response(("forget", {"memory_id": item.id})),
                conftest.text_response("忘了"),
            ]
        )

        await harness.run(
            harness.Agent(name="a"), "忘掉我住哪", memory=store, model_fn=model
        )

        assert store.items() == []

    async def test_forget_unknown_id_is_reported_to_model(
        self, store, fake_model
    ):
        model = fake_model(
            [
                conftest.tool_response(("forget", {"memory_id": "nope"})),
                conftest.text_response("找不到"),
            ]
        )

        await harness.run(
            harness.Agent(name="a"), "忘掉", memory=store, model_fn=model
        )

        content = model.requests[1]["messages"][-1]["content"]
        assert "找不到識別碼為 nope 的記憶" in content

    async def test_memory_is_shared_across_sessions(self, store, fake_model):
        store.add("偏好英文回覆")
        model = fake_model([conftest.text_response("ok")])

        await harness.run(
            harness.Agent(name="a"),
            "hi",
            session=sessions.Session(),
            memory=store,
            model_fn=model,
        )

        assert "偏好英文回覆" in _system_prompt(model, 0)

    async def test_no_memory_tools_without_store(self, fake_model):
        model = fake_model([conftest.text_response("ok")])

        await harness.run(harness.Agent(name="a"), "hi", model_fn=model)

        assert model.requests[0]["tools"] == []

    async def test_subagent_does_not_get_memory(self, store, fake_model):
        helper = harness.Agent(name="helper", instructions="你是研究員")
        main = harness.Agent(name="main", subagents=[helper])
        model = fake_model(
            [
                conftest.tool_response(("ask_helper", {"task": "查"})),
                conftest.text_response("查到了"),
                conftest.text_response("完成"),
            ]
        )

        await harness.run(main, "幫我查", memory=store, model_fn=model)

        assert _system_prompt(model, 1) == "你是研究員"
        assert model.requests[1]["tools"] == []
