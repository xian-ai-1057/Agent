/**
 * 內建的計畫工具（toolset 名稱：plan）。
 *
 * 設計參考 Codex 的 update_plan 與 Claude Code 的 TodoWrite：agent 在
 * 多步驟的工作開始時列出計畫，每完成一步就更新狀態。工具本身不存任何
 * 東西，最新一次呼叫的參數就是目前的計畫（對話紀錄裡都看得到，接續
 * 對話時也還在），使用者因此能隨時看到進度。
 */

import { z } from "zod";
import { type Tool, ToolError, tool } from "../tool.ts";

export const PLAN_STATUSES = ["pending", "in_progress", "completed"] as const;
const MARKS: Record<(typeof PLAN_STATUSES)[number], string> = {
  pending: "[ ]",
  in_progress: "[>]",
  completed: "[x]",
};

export const updatePlanTool = tool({
  name: "update_plan",
  description:
    "列出或更新目前工作的計畫。每次都傳入完整的步驟清單與各自的狀態" +
    "（pending、in_progress、completed），同一時間最多一個步驟是 " +
    "in_progress。適合三個步驟以上的工作；簡單的問題不需要計畫。",
  parameters: z.object({
    explanation: z
      .string()
      .optional()
      .describe("這次更新的簡短說明，例如為什麼調整計畫"),
    plan: z
      .array(
        z.object({
          step: z.string().min(1).describe("一句話描述這個步驟"),
          status: z.enum(PLAN_STATUSES).describe("步驟狀態"),
        }),
      )
      .min(1)
      .describe("完整的步驟清單，依執行順序排列"),
  }),
  execute: ({ plan }) => {
    const active = plan.filter((item) => item.status === "in_progress");
    if (active.length > 1) {
      throw new ToolError(
        `同一時間只能有一個步驟是 in_progress，目前有 ${active.length} 個`,
      );
    }
    const done = plan.filter((item) => item.status === "completed").length;
    const lines = plan.map((item) => `${MARKS[item.status]} ${item.step}`);
    return [`計畫已更新（已完成 ${done}/${plan.length}）：`, ...lines].join(
      "\n",
    );
  },
});

export const planTools: Tool[] = [updatePlanTool];
