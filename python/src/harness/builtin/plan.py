"""內建的計畫工具（toolset 名稱：plan）。

設計參考 Codex 的 update_plan 與 Claude Code 的 TodoWrite：agent 在多步驟
的工作開始時列出計畫，每完成一步就更新狀態。工具本身不存任何東西，最新
一次呼叫的參數就是目前的計畫（對話紀錄裡都看得到，接續對話時也還在），
使用者因此能隨時看到進度。
"""

from typing import Any

from harness import tools

PLAN_STATUSES = ("pending", "in_progress", "completed")
_MARKS = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}

_DESCRIPTION = (
    "列出或更新目前工作的計畫。每次都傳入完整的步驟清單與各自的狀態"
    "（pending、in_progress、completed），同一時間最多一個步驟是 "
    "in_progress。適合三個步驟以上的工作；簡單的問題不需要計畫。"
)

# 參數 schema 直接手寫，和 TypeScript 版完全相同（pydantic 產生的 schema
# 會用 $ref，有些模型供應商不支援）。
_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "explanation": {
            "description": "這次更新的簡短說明，例如為什麼調整計畫",
            "type": "string",
        },
        "plan": {
            "minItems": 1,
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "step": {
                        "type": "string",
                        "minLength": 1,
                        "description": "一句話描述這個步驟",
                    },
                    "status": {
                        "type": "string",
                        "enum": list(PLAN_STATUSES),
                        "description": "步驟狀態",
                    },
                },
                "required": ["step", "status"],
            },
            "description": "完整的步驟清單，依執行順序排列",
        },
    },
    "required": ["plan"],
}


def _validate(plan: Any) -> list[dict[str, str]]:
    """檢查計畫的格式，回傳整理過的步驟。"""
    if not isinstance(plan, list) or not plan:
        raise tools.ToolError("plan 必須是至少有一個步驟的清單")
    steps = []
    for index, item in enumerate(plan, start=1):
        if not isinstance(item, dict):
            raise tools.ToolError(f"第 {index} 個步驟必須是 {{step, status}}")
        step = str(item.get("step") or "").strip()
        status = item.get("status")
        if not step:
            raise tools.ToolError(f"第 {index} 個步驟缺少 step")
        if status not in PLAN_STATUSES:
            allowed = "、".join(PLAN_STATUSES)
            raise tools.ToolError(
                f"第 {index} 個步驟的 status 必須是 {allowed} 之一：{status}"
            )
        steps.append({"step": step, "status": status})
    return steps


def update_plan(plan: Any, explanation: str | None = None) -> str:
    """更新計畫，回傳整份計畫與完成進度。

    Args:
        plan: 完整的步驟清單，每個步驟是 {step, status}。
        explanation: 這次更新的簡短說明（只給使用者看，不影響結果）。

    Returns:
        以 [x]、[>]、[ ] 標示狀態的計畫。

    Raises:
        ToolError: 格式不對，或同時有多個進行中的步驟。
    """
    del explanation  # 只是讓使用者看到更新原因。
    steps = _validate(plan)
    active = sum(1 for item in steps if item["status"] == "in_progress")
    if active > 1:
        raise tools.ToolError(
            f"同一時間只能有一個步驟是 in_progress，目前有 {active} 個"
        )
    done = sum(1 for item in steps if item["status"] == "completed")
    lines = [f"計畫已更新（已完成 {done}/{len(steps)}）："]
    lines += [f"{_MARKS[item['status']]} {item['step']}" for item in steps]
    return "\n".join(lines)


update_plan_tool = tools.Tool(
    name="update_plan",
    description=_DESCRIPTION,
    parameters=_PARAMETERS,
    func=update_plan,
)

TOOLS = [update_plan_tool]
