/** 從 update_plan 工具呼叫取出計畫，做成清單。 */

import type { Message } from "../../../../src/web/protocol.ts";

export type PlanStatus = "pending" | "in_progress" | "completed";

export interface PlanItem {
  step: string;
  status: PlanStatus;
}

const STATUSES: readonly string[] = ["pending", "in_progress", "completed"];

/**
 * 解析 update_plan 的 plan 參數。格式不對的項目略過；一項都沒有時回傳
 * undefined。
 */
export function parsePlan(value: unknown): PlanItem[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const items: PlanItem[] = [];
  for (const entry of value) {
    if (!entry || typeof entry !== "object") continue;
    const { step, status } = entry as { step?: unknown; status?: unknown };
    if (typeof step !== "string" || step.trim() === "") continue;
    items.push({
      step: step.trim(),
      status:
        typeof status === "string" && STATUSES.includes(status)
          ? (status as PlanStatus)
          : "pending",
    });
  }
  return items.length > 0 ? items : undefined;
}

/** 對話裡最後一次 update_plan 呼叫的計畫；沒有時回傳 undefined。 */
export function latestPlan(messages: Message[]): PlanItem[] | undefined {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const calls = messages[i]?.tool_calls ?? [];
    for (let j = calls.length - 1; j >= 0; j -= 1) {
      const call = calls[j];
      if (call?.function.name !== "update_plan") continue;
      const plan = planFromArguments(call.function.arguments);
      if (plan) return plan;
    }
  }
  return undefined;
}

/** 從工具呼叫的參數（JSON 字串或已解析的物件）取出計畫。 */
export function planFromArguments(args: unknown): PlanItem[] | undefined {
  let parsed = args;
  if (typeof args === "string") {
    try {
      parsed = JSON.parse(args);
    } catch {
      return undefined;
    }
  }
  if (!parsed || typeof parsed !== "object") return undefined;
  return parsePlan((parsed as { plan?: unknown }).plan);
}

/** 已完成幾項。 */
export function planProgress(items: PlanItem[]): {
  done: number;
  total: number;
} {
  return {
    done: items.filter((item) => item.status === "completed").length,
    total: items.length,
  };
}
