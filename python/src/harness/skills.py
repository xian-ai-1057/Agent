"""Skills：需要時才展開的能力單元。

一個 skill 是一段任務指示，加上（選用的）一組工具與參考檔案。agent
平常只看得到每個 skill 的名稱與一句說明，判斷用得到時才呼叫
load_skill 讀進完整指示，skill 的工具也在這時才開放使用。這樣 agent
可以掛很多 skill，又不會讓 system prompt 塞滿用不到的說明。

專案裡的格式（一個資料夾一個 skill）：

    skills/
    └── refund/
        ├── SKILL.md        開頭是設定，內文是指示
        └── policy.md       參考檔案，agent 用 read_skill_file 讀取

SKILL.md：

    ---
    name: refund
    description: 處理退款申請，依退款政策判斷能不能退
    tools: [find_order, issue_refund]    # 選用，tools/ 裡的工具名稱
    ---
    1. 先用 find_order 查訂單……

目前載入了哪些 skill 不另外存檔，而是從對話紀錄裡成功的 load_skill
呼叫推算，所以接續對話時 skill 的工具仍然可用。
"""

import dataclasses
import pathlib

from harness import datatypes
from harness import tools as tools_lib

SKILL_FILE = "SKILL.md"
LOAD_TOOL = "load_skill"
READ_TOOL = "read_skill_file"
MAX_READ_CHARS = 50_000


@dataclasses.dataclass(eq=False)
class Skill:
    """一個 skill。

    Attributes:
        name: skill 名稱，load_skill 用這個名稱載入。
        description: 一句話說明什麼時候該用，agent 靠它決定要不要載入。
        instructions: 載入後交給 agent 的完整指示。
        tools: 載入後才開放的工具。
        root: skill 資料夾，裡面的其他檔案可以用 read_skill_file 讀取；
            沒有參考檔案時為 None。
    """

    name: str
    description: str
    instructions: str
    tools: list[tools_lib.Tool] = dataclasses.field(default_factory=list)
    root: pathlib.Path | None = None

    def resource_files(self) -> list[str]:
        """列出 skill 資料夾裡的參考檔案（不含 SKILL.md）。

        Returns:
            相對於 skill 資料夾的路徑，依名稱排序。
        """
        if self.root is None:
            return []
        found = []
        for path in self.root.rglob("*"):
            relative = path.relative_to(self.root)
            hidden = any(
                part.startswith(".") or part == "__pycache__"
                for part in relative.parts
            )
            if path.is_file() and path.name != SKILL_FILE and not hidden:
                found.append(relative.as_posix())
        return sorted(found)


def prompt_section(skills: list[Skill]) -> str:
    """產生要接在 system prompt 後面的 skill 清單。

    Args:
        skills: agent 可用的 skill。

    Returns:
        列出名稱與說明的文字；沒有 skill 時為空字串。
    """
    if not skills:
        return ""
    lines = [
        "# 可用的技能（skills）",
        "",
        f"遇到符合下列說明的工作時，先呼叫 {LOAD_TOOL} 載入該技能的完整"
        "指示再照著做；載入後才能使用它的專屬工具。不相關的工作不要"
        "載入。",
        "",
    ]
    lines += [f"- {item.name}：{item.description}" for item in skills]
    return "\n".join(lines)


def make_tools(skills: list[Skill]) -> list[tools_lib.Tool]:
    """建立載入 skill 與讀取參考檔案的工具。

    Args:
        skills: agent 可用的 skill。

    Returns:
        load_skill，以及有參考檔案時的 read_skill_file。
    """
    by_name = {item.name: item for item in skills}

    def find(name: str) -> Skill:
        if name not in by_name:
            available = "、".join(by_name)
            raise tools_lib.ToolError(
                f"沒有 {name} 這個技能，可用的有：{available}"
            )
        return by_name[name]

    def load_skill(name: str) -> str:
        """載入一個技能的完整指示，並開放它的專屬工具。

        Args:
            name: 技能名稱（system prompt 技能清單裡的名稱）。
        """
        target = find(name)
        parts = [f"# 技能：{target.name}", "", target.instructions]
        if target.tools:
            names = "、".join(item.name for item in target.tools)
            parts += ["", f"現在可以使用的專屬工具：{names}"]
        files = target.resource_files()
        if files:
            parts += [
                "",
                f"參考檔案（需要時用 {READ_TOOL} 讀取）：",
                *[f"- {path}" for path in files],
            ]
        return "\n".join(parts)

    def read_skill_file(name: str, path: str) -> str:
        """讀取技能資料夾裡的參考檔案。

        Args:
            name: 技能名稱。
            path: 參考檔案的路徑（load_skill 列出的路徑）。
        """
        target = find(name)
        if path not in target.resource_files():
            raise tools_lib.ToolError(
                f"技能 {name} 沒有 {path} 這個參考檔案，先用 {LOAD_TOOL} "
                "查看有哪些檔案"
            )
        text = (target.root / path).read_text(encoding="utf-8")
        if len(text) > MAX_READ_CHARS:
            omitted = len(text) - MAX_READ_CHARS
            return (
                f"{text[:MAX_READ_CHARS]}\n"
                f"[檔案過長，後面還有 {omitted} 個字元沒有顯示]"
            )
        return text

    made = [tools_lib.tool(load_skill, name=LOAD_TOOL)]
    if any(item.root is not None for item in skills):
        made.append(tools_lib.tool(read_skill_file, name=READ_TOOL))
    return made


def loaded_skills(
    skills: list[Skill], messages: list[datatypes.Message]
) -> list[Skill]:
    """依對話紀錄找出已經成功載入的 skill。

    Args:
        skills: agent 可用的 skill。
        messages: 目前為止的對話紀錄。

    Returns:
        已載入的 skill，順序與 skills 相同。
    """
    load_calls: set[str] = set()
    loaded: set[str] = set()
    for message in messages:
        for call in message.get("tool_calls") or []:
            if (call.get("function") or {}).get("name") == LOAD_TOOL:
                load_calls.add(call.get("id"))
        if message.get("role") != "tool":
            continue
        if message.get("tool_call_id") not in load_calls:
            continue
        content = str(message.get("content") or "")
        # 成功的結果一定以「# 技能：名稱」開頭，失敗的結果則有錯誤前綴。
        for item in skills:
            if content.startswith(f"# 技能：{item.name}\n"):
                loaded.add(item.name)
    return [item for item in skills if item.name in loaded]
