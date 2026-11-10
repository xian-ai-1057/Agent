"""subagents 與 handoffs 的測試。"""

import asyncio

import pytest

import harness
from harness import delegation
from tests import conftest


def _contents(model, index):
    """第 index 次模型請求裡每則訊息的 (role, content)。"""
    messages = model.requests[index]["messages"]
    return [(message["role"], message["content"]) for message in messages]


def _tool_names(model, index):
    """第 index 次模型請求裡提供給模型的工具名稱。"""
    schemas = model.requests[index]["tools"]
    return [schema["function"]["name"] for schema in schemas]


@pytest.fixture
def lookup_tool():
    """研究員專用的查詢工具，會記錄是哪個 agent 呼叫的。"""
    callers = []

    @harness.tool
    def lookup(context: harness.RunContext, keyword: str) -> str:
        """查詢資料。"""
        callers.append(context.agent_name)
        return f"{keyword} 的資料"

    lookup.callers = callers
    return lookup


class TestSubagents:
    """主 agent 把工作交給 subagent。"""

    def _agents(self, lookup_tool, **researcher_options):
        researcher = harness.Agent(
            name="researcher",
            instructions="你是研究員",
            description="查資料並整理重點",
            tools=[lookup_tool],
            **researcher_options,
        )
        main = harness.Agent(
            name="main", instructions="你是主持人", subagents=[researcher]
        )
        return main, researcher

    def _script(self):
        return [
            conftest.tool_response(("ask_researcher", {"task": "查十月銷售"})),
            conftest.tool_response(("lookup", {"keyword": "十月"})),
            conftest.text_response("十月賣了 192,000"),
            conftest.text_response("研究員說十月賣了 192,000"),
        ]

    async def test_run_offers_subagent_as_tool_with_its_description(
        self, fake_model, lookup_tool
    ):
        main, _ = self._agents(lookup_tool)
        model = fake_model([conftest.text_response("好")])

        await harness.run(main, "嗨", model_fn=model)

        schema = model.requests[0]["tools"][0]["function"]
        assert schema["name"] == "ask_researcher"
        assert "查資料並整理重點" in schema["description"]
        assert schema["parameters"]["required"] == ["task"]

    async def test_subagent_works_in_fresh_conversation(
        self, fake_model, lookup_tool
    ):
        main, _ = self._agents(lookup_tool)
        model = fake_model(self._script())

        await harness.run(main, "十月賣得如何？", model_fn=model)

        assert _contents(model, 1) == [
            ("system", "你是研究員"),
            ("user", "查十月銷售"),
        ]

    async def test_subagent_reply_returns_to_parent_as_tool_result(
        self, fake_model, lookup_tool
    ):
        main, _ = self._agents(lookup_tool)
        model = fake_model(self._script())

        result = await harness.run(main, "十月賣得如何？", model_fn=model)

        assert _contents(model, 3)[-1] == ("tool", "十月賣了 192,000")
        assert result.output == "研究員說十月賣了 192,000"
        assert result.agent_name == "main"

    async def test_subagent_work_stays_out_of_parent_conversation(
        self, fake_model, lookup_tool
    ):
        main, _ = self._agents(lookup_tool)
        model = fake_model(self._script())

        result = await harness.run(main, "十月賣得如何？", model_fn=model)

        roles = [message["role"] for message in result.messages]
        assert roles == ["user", "assistant", "tool", "assistant"]

    async def test_subagent_uses_its_own_tools_and_model(
        self, fake_model, lookup_tool
    ):
        main, _ = self._agents(lookup_tool, model="openai/small")
        main.model = "openai/big"
        model = fake_model(self._script())

        await harness.run(main, "十月賣得如何？", model_fn=model)

        assert _tool_names(model, 1) == ["lookup"]
        assert model.requests[0]["model"] == "openai/big"
        assert model.requests[1]["model"] == "openai/small"
        assert lookup_tool.callers == ["researcher"]

    async def test_run_adds_subagent_usage_to_total(
        self, fake_model, lookup_tool
    ):
        main, _ = self._agents(lookup_tool)
        model = fake_model(self._script())

        result = await harness.run(main, "十月賣得如何？", model_fn=model)

        assert result.usage.input_tokens == 40
        assert result.turns == 2

    async def test_hooks_see_subagent_tool_calls_but_one_run(
        self, fake_model, lookup_tool
    ):
        main, _ = self._agents(lookup_tool)
        hooks = harness.Hooks()
        seen = []
        hooks.add("run_start", lambda text, context: seen.append("start"))
        hooks.add("run_end", lambda result, context: seen.append("end"))
        hooks.add(
            "pre_tool_use",
            lambda call, context: seen.append(
                f"{context.agent_name}:{call.name}"
            ),
        )

        await harness.run(
            main, "十月？", hooks=hooks, model_fn=fake_model(self._script())
        )

        assert seen == [
            "start",
            "main:ask_researcher",
            "researcher:lookup",
            "end",
        ]

    async def test_permissions_apply_inside_subagent(
        self, fake_model, lookup_tool
    ):
        main, _ = self._agents(lookup_tool)
        model = fake_model(self._script())

        await harness.run(
            main,
            "十月？",
            permissions=harness.Permissions(deny=["lookup"]),
            model_fn=model,
        )

        assert lookup_tool.callers == []
        assert "權限設定不允許" in _contents(model, 2)[-1][1]

    async def test_parent_is_told_when_subagent_runs_out_of_turns(
        self, fake_model, lookup_tool
    ):
        main, _ = self._agents(lookup_tool, max_turns=1)
        model = fake_model(
            [
                conftest.tool_response(("ask_researcher", {"task": "查"})),
                conftest.tool_response(("lookup", {"keyword": "十月"})),
                conftest.text_response("研究員沒查完"),
            ]
        )

        await harness.run(main, "十月？", model_fn=model)

        content = _contents(model, 2)[-1][1]
        assert content.startswith("[錯誤] researcher 沒有完成工作（max_turns）")

    async def test_subagent_tools_stop_at_max_depth(self, fake_model):
        looper = harness.Agent(name="looper")
        looper.subagents.append(looper)
        depth = delegation.MAX_SUBAGENT_DEPTH
        script = [
            conftest.tool_response(("ask_looper", {"task": "再往下"}))
            for _ in range(depth)
        ]
        script += [conftest.text_response("到底了")] * (depth + 1)
        model = fake_model(script)

        await harness.run(looper, "開始", model_fn=model)

        assert [_tool_names(model, i) for i in range(depth + 1)] == [
            ["ask_looper"]
        ] * depth + [[]]

    async def test_run_rejects_tool_name_clash_with_subagent(self, fake_model):
        @harness.tool(name="ask_researcher")
        def clash() -> str:
            """撞名的工具。"""
            return ""

        main = harness.Agent(
            name="main",
            tools=[clash],
            subagents=[harness.Agent(name="researcher")],
        )

        with pytest.raises(ValueError, match="工具名稱重複：ask_researcher"):
            await harness.run(main, "嗨", model_fn=fake_model([]))


class TestSubagentStreaming:
    """串流時看得到 subagent 內部的進度。"""

    def _agents(self, *tools, helper=None):
        researcher = harness.Agent(
            name="researcher",
            instructions="你是研究員",
            tools=list(tools),
            subagents=[helper] if helper else [],
        )
        main = harness.Agent(
            name="main", instructions="你是主持人", subagents=[researcher]
        )
        return main

    async def _events(self, agent, model):
        return [
            event
            async for event in harness.run_stream(
                agent, "十月？", model_fn=model
            )
        ]

    @staticmethod
    def _describe(event):
        if event.type != "subagent":
            return event.type
        path = "/".join(event.data["path"])
        return f"{path}:{event.data['event'].type}"

    async def test_subagent_events_arrive_between_call_and_result(
        self, fake_model, lookup_tool
    ):
        main = self._agents(lookup_tool)
        model = fake_model(TestSubagents()._script())

        events = await self._events(main, model)

        kinds = [self._describe(event) for event in events]
        assert kinds == [
            "message",
            "tool_call",
            "researcher:message",
            "researcher:tool_call",
            "researcher:tool_result",
            "researcher:text_delta",
            "researcher:message",
            "researcher:run_end",
            "tool_result",
            "text_delta",
            "message",
            "run_end",
        ]
        progress = [event for event in events if event.type == "subagent"]
        assert {event.data["call_id"] for event in progress} == {
            "call_0_ask_researcher"
        }
        assert progress[1].data["agent"] == "researcher"
        assert progress[1].data["event"].data["call"].name == "lookup"
        assert progress[3].data["event"].data["text"] == "十月賣了 192,000"

    async def test_progress_is_streamed_while_subagent_is_still_working(
        self, fake_model
    ):
        # 工具要等外面收到 subagent 的 tool_call 才會結束；如果進度要等
        # 工具做完才送出，這裡會卡住。
        seen = asyncio.Event()

        @harness.tool
        async def slow_lookup(keyword: str) -> str:
            """查詢資料。"""
            await seen.wait()
            return "資料"

        main = self._agents(slow_lookup)
        model = fake_model(
            [
                conftest.tool_response(("ask_researcher", {"task": "查"})),
                conftest.tool_response(("slow_lookup", {"keyword": "十月"})),
                conftest.text_response("查到了"),
                conftest.text_response("完成"),
            ]
        )

        async def consume():
            async for event in harness.run_stream(main, "查", model_fn=model):
                if (
                    event.type == "subagent"
                    and event.data["event"].type == "tool_call"
                ):
                    seen.set()
                if event.type == "run_end":
                    return event.data["result"]

        result = await asyncio.wait_for(consume(), timeout=5)

        assert result.output == "完成"

    async def test_nested_subagent_events_carry_full_path(
        self, fake_model, lookup_tool
    ):
        helper = harness.Agent(
            name="helper", instructions="你是助手", tools=[lookup_tool]
        )
        main = self._agents(helper=helper)
        model = fake_model(
            [
                conftest.tool_response(("ask_researcher", {"task": "查"})),
                conftest.tool_response(("ask_helper", {"task": "幫忙查"})),
                conftest.tool_response(("lookup", {"keyword": "十月"})),
                conftest.text_response("helper 查到了"),
                conftest.text_response("researcher 整理好了"),
                conftest.text_response("完成"),
            ]
        )

        events = await self._events(main, model)

        nested = [
            event
            for event in events
            if event.type == "subagent" and event.data["agent"] == "helper"
        ]
        assert nested
        assert {tuple(event.data["path"]) for event in nested} == {
            ("researcher", "helper")
        }
        assert {event.data["call_id"] for event in nested} == {
            "call_0_ask_researcher"
        }
        calls = [
            event.data["event"].data["call"].name
            for event in nested
            if event.data["event"].type == "tool_call"
        ]
        assert calls == ["lookup"]

    async def test_parallel_subagents_are_told_apart_by_call_id(
        self, fake_model
    ):
        first = harness.Agent(name="first", instructions="一")
        second = harness.Agent(name="second", instructions="二")
        main = harness.Agent(
            name="main", instructions="主", subagents=[first, second]
        )
        model = fake_model(
            [
                conftest.tool_response(
                    ("ask_first", {"task": "甲"}),
                    ("ask_second", {"task": "乙"}),
                ),
                conftest.text_response("一號完成"),
                conftest.text_response("二號完成"),
                conftest.text_response("都好了"),
            ]
        )

        events = await self._events(main, model)

        ends = {
            event.data["call_id"]: event.data["agent"]
            for event in events
            if event.type == "subagent"
            and event.data["event"].type == "run_end"
        }
        assert ends == {
            "call_0_ask_first": "first",
            "call_1_ask_second": "second",
        }


class TestHandoffs:
    """把整段對話轉交給另一個 agent。"""

    def _agents(self, add_tool):
        billing = harness.Agent(
            name="billing",
            instructions="你是帳務專員",
            description="處理帳單與退款",
            tools=[add_tool],
            model="openai/billing",
        )
        front = harness.Agent(
            name="front", instructions="你是總機", handoffs=[billing]
        )
        return front, billing

    def _script(self):
        return [
            conftest.tool_response(("transfer_to_billing", {})),
            conftest.text_response("我是帳務，請說"),
        ]

    async def test_run_offers_handoff_as_tool_without_parameters(
        self, fake_model, add_tool
    ):
        front, _ = self._agents(add_tool)
        model = fake_model([conftest.text_response("好")])

        await harness.run(front, "嗨", model_fn=model)

        schema = model.requests[0]["tools"][0]["function"]
        assert schema["name"] == "transfer_to_billing"
        assert "處理帳單與退款" in schema["description"]
        assert schema["parameters"]["properties"] == {}

    async def test_handoff_switches_instructions_tools_and_model(
        self, fake_model, add_tool
    ):
        front, _ = self._agents(add_tool)
        model = fake_model(self._script())

        await harness.run(front, "我要退款", model_fn=model)

        assert _contents(model, 1)[0] == ("system", "你是帳務專員")
        assert _tool_names(model, 1) == ["add"]
        assert model.requests[1]["model"] == "openai/billing"

    async def test_new_agent_sees_whole_conversation(
        self, fake_model, add_tool
    ):
        front, _ = self._agents(add_tool)
        model = fake_model(self._script())

        await harness.run(front, "我要退款", model_fn=model)

        roles = [role for role, _ in _contents(model, 1)]
        assert roles == ["system", "user", "assistant", "tool"]
        assert _contents(model, 1)[1] == ("user", "我要退款")

    async def test_run_reports_final_agent_and_emits_handoff_event(
        self, fake_model, add_tool
    ):
        front, _ = self._agents(add_tool)
        events = [
            event
            async for event in harness.run_stream(
                front, "我要退款", model_fn=fake_model(self._script())
            )
        ]

        handoffs = [event.data for event in events if event.type == "handoff"]
        assert handoffs == [{"from": "front", "to": "billing"}]
        assert events[-1].data["result"].agent_name == "billing"
        assert events[-1].data["result"].output == "我是帳務，請說"

    async def test_next_run_continues_with_agent_that_took_over(
        self, fake_model, add_tool
    ):
        front, _ = self._agents(add_tool)
        session = harness.Session()
        model = fake_model(self._script() + [conftest.text_response("已退款")])
        await harness.run(front, "我要退款", session=session, model_fn=model)

        result = await harness.run(
            front, "訂單 123", session=session, model_fn=model
        )

        assert _contents(model, 2)[0] == ("system", "你是帳務專員")
        assert result.agent_name == "billing"

    async def test_agents_can_hand_conversation_back(
        self, fake_model, add_tool
    ):
        front, billing = self._agents(add_tool)
        billing.handoffs.append(front)
        model = fake_model(
            [
                conftest.tool_response(("transfer_to_billing", {})),
                conftest.tool_response(("transfer_to_front", {})),
                conftest.text_response("我是總機，還需要什麼？"),
            ]
        )

        result = await harness.run(front, "我要退款", model_fn=model)

        assert _contents(model, 2)[0] == ("system", "你是總機")
        assert result.agent_name == "front"

    async def test_other_tool_calls_in_same_turn_still_run(
        self, fake_model, add_tool
    ):
        billing = harness.Agent(name="billing", instructions="你是帳務專員")
        front = harness.Agent(
            name="front", tools=[add_tool], handoffs=[billing]
        )
        model = fake_model(
            [
                conftest.tool_response(
                    ("add", {"a": 1, "b": 2}), ("transfer_to_billing", {})
                ),
                conftest.text_response("我是帳務"),
            ]
        )

        await harness.run(front, "嗨", model_fn=model)

        tool_results = [c for role, c in _contents(model, 1) if role == "tool"]
        assert tool_results[0] == "3"
        assert _contents(model, 1)[0] == ("system", "你是帳務專員")

    async def test_only_first_handoff_in_a_turn_takes_effect(self, fake_model):
        first = harness.Agent(name="first", instructions="我是第一個")
        second = harness.Agent(name="second", instructions="我是第二個")
        front = harness.Agent(name="front", handoffs=[first, second])
        model = fake_model(
            [
                conftest.tool_response(
                    ("transfer_to_first", {}), ("transfer_to_second", {})
                ),
                conftest.text_response("好"),
            ]
        )

        result = await harness.run(front, "嗨", model_fn=model)

        assert result.agent_name == "first"
        assert "一次只能轉交給一個 agent" in _contents(model, 1)[-1][1]

    async def test_hook_can_block_handoff(self, fake_model, add_tool):
        front, _ = self._agents(add_tool)
        hooks = harness.Hooks()
        hooks.add("pre_tool_use", lambda call, context: harness.deny("先別轉"))
        model = fake_model(
            [
                conftest.tool_response(("transfer_to_billing", {})),
                conftest.text_response("那我來處理"),
            ]
        )

        result = await harness.run(front, "嗨", hooks=hooks, model_fn=model)

        assert result.agent_name == "front"
        assert _contents(model, 1)[0] == ("system", "你是總機")

    async def test_handoff_tool_name_replaces_unsupported_characters(
        self, fake_model
    ):
        target = harness.Agent(name="帳務 a.b")
        front = harness.Agent(name="front", handoffs=[target])
        model = fake_model([conftest.text_response("好")])

        await harness.run(front, "嗨", model_fn=model)

        assert _tool_names(model, 0) == ["transfer_to____a_b"]


class TestActiveAgent:
    """從對話紀錄推算目前由誰負責。"""

    def _handoff(self, name, content="已轉交"):
        return {"role": "tool", "name": name, "content": content}

    def test_active_agent_is_entry_without_handoffs(self):
        entry = harness.Agent(name="front")

        assert delegation.active_agent(entry, []) is entry

    def test_active_agent_follows_last_successful_handoff(self):
        billing = harness.Agent(name="billing")
        tech = harness.Agent(name="tech")
        entry = harness.Agent(name="front", handoffs=[billing, tech])
        messages = [
            self._handoff("transfer_to_billing"),
            self._handoff("transfer_to_tech"),
            self._handoff("transfer_to_billing", "[錯誤] 這次呼叫被拒絕"),
        ]

        assert delegation.active_agent(entry, messages) is tech

    def test_active_agent_finds_agents_reachable_through_chain(self):
        deep = harness.Agent(name="deep")
        middle = harness.Agent(name="middle", handoffs=[deep])
        entry = harness.Agent(name="front", handoffs=[middle])
        middle.handoffs.append(entry)

        active = delegation.active_agent(
            entry, [self._handoff("transfer_to_deep")]
        )

        assert active is deep

    def test_active_agent_falls_back_when_target_no_longer_exists(self):
        entry = harness.Agent(name="front")

        active = delegation.active_agent(
            entry, [self._handoff("transfer_to_removed")]
        )

        assert active is entry
