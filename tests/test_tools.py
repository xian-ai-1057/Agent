"""tools 模組的測試。"""

import enum
from typing import Literal

import pydantic
import pytest

import harness
from harness import tools


class Color(enum.Enum):
    RED = "red"
    BLUE = "blue"


class Filters(pydantic.BaseModel):
    keyword: str
    limit: int = 10


def _schema_of(annotation):
    """建立只有一個參數 value 的工具，回傳該參數的 schema。"""

    def func(value):
        return value

    func.__annotations__ = {"value": annotation}
    return harness.tool(func).parameters["properties"]["value"]


class TestSchema:
    """從型別註解產生 JSON schema。"""

    @pytest.mark.parametrize(
        "annotation, expected_type",
        [
            pytest.param(str, "string", id="str"),
            pytest.param(int, "integer", id="int"),
            pytest.param(float, "number", id="float"),
            pytest.param(bool, "boolean", id="bool"),
            pytest.param(list[str], "array", id="list"),
            pytest.param(dict[str, int], "object", id="dict"),
        ],
    )
    def test_tool_maps_basic_types(self, annotation, expected_type):
        assert _schema_of(annotation)["type"] == expected_type

    def test_tool_maps_literal_to_enum(self):
        assert _schema_of(Literal["a", "b"])["enum"] == ["a", "b"]

    def test_tool_supports_enum_class(self):
        @harness.tool
        def paint(color: Color) -> str:
            """上色。"""
            return color.value

        assert "$defs" in paint.parameters

    def test_tool_marks_parameters_without_default_as_required(self):
        @harness.tool
        def search(query: str, limit: int = 5) -> str:
            """搜尋。"""
            return query

        assert search.parameters["required"] == ["query"]
        assert search.parameters["properties"]["limit"]["default"] == 5

    def test_tool_without_parameters_has_empty_properties(self):
        @harness.tool
        def ping() -> str:
            """測試連線。"""
            return "pong"

        assert ping.parameters["properties"] == {}

    def test_tool_reads_descriptions_from_docstring(self, add_tool):
        assert add_tool.description == "把兩個整數相加。"
        assert add_tool.parameters["properties"]["a"]["description"] == (
            "第一個數。"
        )

    def test_tool_hides_context_parameter_from_schema(self):
        @harness.tool
        def whoami(context: harness.RunContext, verbose: bool = False) -> str:
            """回報目前的 agent。"""
            return context.agent_name

        assert list(whoami.parameters["properties"]) == ["verbose"]
        assert whoami.context_param == "context"

    def test_tool_accepts_name_and_description_overrides(self):
        def legacy_function(x: int) -> int:
            return x

        wrapped = harness.tool(
            legacy_function, name="double", description="加倍"
        )

        assert wrapped.name == "double"
        assert wrapped.description == "加倍"

    def test_tool_rejects_var_keyword_parameters(self):
        with pytest.raises(TypeError, match="kwargs"):

            @harness.tool
            def bad(**kwargs) -> str:
                """不支援。"""
                return ""

    def test_to_schema_uses_openai_function_format(self, add_tool):
        schema = add_tool.to_schema()

        assert schema["type"] == "function"
        assert schema["function"]["name"] == "add"

    def test_tool_stays_callable_like_original_function(self, add_tool):
        assert add_tool(1, 2) == 3


class TestParseDocstring:
    """Google 風格 docstring 的解析。"""

    def test_parse_docstring_returns_summary_and_args(self):
        doc = """查詢訂單。

        可以查詢任何狀態的訂單。

        Args:
            order_id: 訂單編號。
            verbose (bool): 是否顯示明細，
                預設不顯示。

        Returns:
            訂單內容。
        """

        summary, args = tools.parse_docstring(doc)

        assert summary == "查詢訂單。\n\n可以查詢任何狀態的訂單。"
        assert args == {
            "order_id": "訂單編號。",
            "verbose": "是否顯示明細， 預設不顯示。",
        }

    def test_parse_docstring_handles_missing_docstring(self):
        assert tools.parse_docstring(None) == ("", {})


class TestInvoke:
    """工具的執行。"""

    async def test_invoke_runs_sync_function(self, add_tool, context):
        assert await tools.invoke(add_tool, {"a": 1, "b": 2}, context) == "3"

    async def test_invoke_runs_async_function(self, context):
        @harness.tool
        async def greet(name: str) -> str:
            """打招呼。"""
            return f"哈囉 {name}"

        assert await tools.invoke(greet, {"name": "小明"}, context) == (
            "哈囉 小明"
        )

    async def test_invoke_coerces_argument_types(self, add_tool, context):
        assert await tools.invoke(add_tool, {"a": "1", "b": 2}, context) == "3"

    async def test_invoke_raises_on_missing_argument(self, add_tool, context):
        with pytest.raises(pydantic.ValidationError):
            await tools.invoke(add_tool, {"a": 1}, context)

    async def test_invoke_injects_run_context(self, context):
        @harness.tool
        def whoami(context: harness.RunContext) -> str:
            """回報目前的 agent。"""
            return context.agent_name

        assert await tools.invoke(whoami, {}, context) == "test"

    async def test_invoke_passes_nested_model_instance(self, context):
        @harness.tool
        def search(filters: Filters) -> str:
            """搜尋。"""
            return f"{filters.keyword}:{filters.limit}"

        output = await tools.invoke(
            search, {"filters": {"keyword": "報告"}}, context
        )

        assert output == "報告:10"


class TestFormatOutput:
    """回傳值轉成文字。"""

    @pytest.mark.parametrize(
        "value, expected",
        [
            pytest.param("文字", "文字", id="str"),
            pytest.param({"名稱": "甲"}, '{"名稱": "甲"}', id="dict"),
            pytest.param([1, 2], "[1, 2]", id="list"),
            pytest.param(Filters(keyword="x"), None, id="pydantic"),
        ],
    )
    def test_format_output_serializes_value(self, value, expected):
        output = tools.format_output(value)

        if expected is None:
            assert '"keyword":"x"' in output
        else:
            assert output == expected

    @pytest.mark.parametrize("value", [None, ""])
    def test_format_output_never_returns_empty_string(self, value):
        assert tools.format_output(value)
