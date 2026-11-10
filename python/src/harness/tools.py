"""把 Python 函式包成 agent 可以呼叫的工具。

給模型看的參數 schema 由函式的型別註解產生，工具與參數的說明則取自
Google 風格的 docstring，所以既有函式通常只要加上 @tool 就能使用。
"""

import asyncio
import dataclasses
import inspect
import json
import re
import typing
from collections.abc import Callable
from typing import Any

import pydantic

from harness import datatypes

_ARG_SECTIONS = ("Args:", "Arguments:", "Parameters:")
_OTHER_SECTIONS = (
    "Returns:",
    "Raises:",
    "Yields:",
    "Example:",
    "Examples:",
    "Attributes:",
    "Note:",
)
_ARG_PATTERN = re.compile(r"^\*{0,2}(\w+)\s*(?:\([^)]*\))?\s*[:：]\s*(.*)$")
_NO_OUTPUT = "(完成，沒有輸出)"


class ToolError(Exception):
    """工具刻意回報給模型的錯誤，訊息會原樣送回（不加例外類別名稱）。"""


@dataclasses.dataclass
class Tool:
    """一個可以給 agent 使用的工具。

    Tool 物件本身仍可像原本的函式一樣直接呼叫，所以加上 @tool 不會
    影響既有程式碼。

    Attributes:
        name: 工具名稱，模型用這個名稱呼叫。
        description: 給模型看的用途說明。
        parameters: 參數的 JSON schema。
        func: 實際執行的函式，同步或 async 皆可。
        needs_approval: 執行前是否需要使用者同意。
        args_model: 用來驗證參數的 pydantic 模型。
        context_param: 要填入 RunContext 的參數名稱，沒有則為 None。
    """

    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., Any]
    needs_approval: bool = False
    args_model: type[pydantic.BaseModel] | None = None
    context_param: str | None = None

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """直接呼叫原本的函式。"""
        return self.func(*args, **kwargs)

    def to_schema(self) -> dict[str, Any]:
        """轉成 OpenAI function calling 格式的工具定義。

        Returns:
            可以直接放進模型請求 tools 欄位的 dict。
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def tool(
    func: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    needs_approval: bool = False,
) -> Any:
    """把函式包成 Tool，可當裝飾器或一般函式使用。

    Example:
        @tool
        def add(a: int, b: int) -> int:
            ...

        @tool(needs_approval=True)
        def send_mail(to: str, body: str) -> str:
            ...

        report_tool = tool(legacy.build_report, name="build_report")

    Args:
        func: 要包裝的函式。
        name: 工具名稱，預設用函式名稱。
        description: 工具說明，預設用 docstring 的摘要。
        needs_approval: 執行前是否需要使用者同意。

    Returns:
        Tool 物件；不帶 func 呼叫時回傳裝飾器。
    """

    def wrap(target: Callable[..., Any]) -> Tool:
        return _build_tool(target, name, description, needs_approval)

    if func is None:
        return wrap
    return wrap(func)


def parse_docstring(doc: str | None) -> tuple[str, dict[str, str]]:
    """從 Google 風格的 docstring 取出摘要與各參數的說明。

    Args:
        doc: 函式的 docstring。

    Returns:
        (摘要, {參數名稱: 說明})。
    """
    if not doc:
        return "", {}
    summary: list[str] = []
    arg_docs: dict[str, str] = {}
    section = "summary"
    base_indent: int | None = None
    current: str | None = None
    for line in inspect.cleandoc(doc).splitlines():
        stripped = line.strip()
        if stripped in _ARG_SECTIONS:
            section = "args"
            continue
        if stripped in _OTHER_SECTIONS:
            section = "other"
            continue
        if section == "summary":
            summary.append(line)
            continue
        if section != "args" or not stripped:
            continue
        indent = len(line) - len(line.lstrip())
        if base_indent is None:
            base_indent = indent
        match = _ARG_PATTERN.match(stripped)
        if indent == base_indent and match:
            current = match.group(1)
            arg_docs[current] = match.group(2).strip()
        elif current:
            arg_docs[current] = f"{arg_docs[current]} {stripped}".strip()
    return "\n".join(summary).strip(), arg_docs


def format_output(value: Any) -> str:
    """把工具的回傳值轉成要送回模型的文字。

    Args:
        value: 工具函式的回傳值。

    Returns:
        字串原樣回傳，其他型別轉成 JSON。
    """
    if value is None:
        return _NO_OUTPUT
    if isinstance(value, str):
        return value or _NO_OUTPUT
    if isinstance(value, pydantic.BaseModel):
        return value.model_dump_json()
    return json.dumps(value, ensure_ascii=False, default=str)


async def invoke(
    target: Tool,
    arguments: dict[str, Any],
    context: datatypes.RunContext,
) -> str:
    """驗證參數並執行工具。

    同步函式會放到執行緒裡跑，避免卡住其他同時進行的工具。

    Args:
        target: 要執行的工具。
        arguments: 模型給的參數。
        context: 這次 run 的環境資訊。

    Returns:
        要送回模型的文字。

    Raises:
        pydantic.ValidationError: 參數不符合 schema。
    """
    kwargs = dict(arguments)
    if target.args_model is not None:
        kwargs = dict(target.args_model.model_validate(arguments))
    if target.context_param:
        kwargs[target.context_param] = context
    if inspect.iscoroutinefunction(target.func):
        value = await target.func(**kwargs)
    else:
        value = await asyncio.to_thread(target.func, **kwargs)
    return format_output(value)


def _build_tool(
    func: Callable[..., Any],
    name: str | None,
    description: str | None,
    needs_approval: bool,
) -> Tool:
    """依函式簽名與 docstring 建立 Tool。"""
    summary, arg_docs = parse_docstring(func.__doc__)
    hints = typing.get_type_hints(func)
    fields: dict[str, Any] = {}
    context_param = None
    for param_name, param in inspect.signature(func).parameters.items():
        annotation = hints.get(param_name, Any)
        if annotation is datatypes.RunContext:
            context_param = param_name
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            raise TypeError(
                f"工具 {func.__name__} 不支援 *args / **kwargs 參數"
            )
        default = ... if param.default is param.empty else param.default
        fields[param_name] = (
            annotation,
            pydantic.Field(default, description=arg_docs.get(param_name)),
        )
    tool_name = name or func.__name__
    args_model = pydantic.create_model(f"{tool_name}_args", **fields)
    return Tool(
        name=tool_name,
        description=description or summary or tool_name,
        parameters=_clean_schema(args_model.model_json_schema()),
        func=func,
        needs_approval=needs_approval,
        args_model=args_model,
        context_param=context_param,
    )


def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """拿掉 pydantic 自動加上、對模型沒有幫助的 title 欄位。"""
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
    schema.setdefault("properties", {})
    return schema
