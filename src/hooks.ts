/**
 * Hooks：在主迴圈的固定時間點插入自己的邏輯。
 *
 *   runStart(userInput, context)        使用者訊息送出前。
 *   preToolUse(call, context)           工具執行前。回傳 deny() 攔截，
 *                                       或 allow() 略過使用者確認；也可以
 *                                       直接修改 call.arguments。
 *   postToolUse(call, result, context)  工具執行後，可修改 result.output。
 *   runEnd(result, context)             整次 run 結束後。
 *
 * 處理函式同步或 async 皆可。
 */

import type { RunContext, RunResult, ToolCall, ToolResult } from "./types.ts";

/** preToolUse 處理函式對一次工具呼叫的決定。 */
export type Decision = { action: "allow" } | { action: "deny"; reason: string };

type MaybePromise<T> = T | Promise<T>;

export interface HookHandlers {
  runStart: (userInput: string, context: RunContext) => MaybePromise<void>;
  preToolUse: (
    call: ToolCall,
    context: RunContext,
    // biome-ignore lint/suspicious/noConfusingVoidType: 允許不回傳任何值
  ) => MaybePromise<Decision | void>;
  postToolUse: (
    call: ToolCall,
    result: ToolResult,
    context: RunContext,
  ) => MaybePromise<void>;
  runEnd: (result: RunResult, context: RunContext) => MaybePromise<void>;
}

export type HookEvent = keyof HookHandlers;

/** 允許這次工具呼叫，並略過使用者確認。 */
export function allow(): Decision {
  return { action: "allow" };
}

/** 拒絕這次工具呼叫，原因會送回給模型。 */
export function deny(reason: string): Decision {
  return { action: "deny", reason };
}

/** 保存各事件的處理函式。 */
export class Hooks {
  private handlers: { [E in HookEvent]: HookHandlers[E][] } = {
    runStart: [],
    preToolUse: [],
    postToolUse: [],
    runEnd: [],
  };

  /**
   * 註冊處理函式，可以串接呼叫。
   *
   * @example
   * hooks.on("preToolUse", (call) => {
   *   if (call.name === "delete_report") return deny("不允許刪除報告");
   * });
   */
  on<E extends HookEvent>(event: E, handler: HookHandlers[E]): this {
    const list = this.handlers[event];
    if (!list) throw new Error(`沒有 ${event} 這個事件`);
    list.push(handler);
    return this;
  }

  /** 依註冊順序呼叫某個事件的所有處理函式，回傳它們的回傳值。 */
  async emit<E extends HookEvent>(
    event: E,
    ...args: Parameters<HookHandlers[E]>
  ): Promise<Awaited<ReturnType<HookHandlers[E]>>[]> {
    const results = [];
    for (const handler of this.handlers[event]) {
      const run = handler as (...values: unknown[]) => unknown;
      results.push(await run(...args));
    }
    return results as Awaited<ReturnType<HookHandlers[E]>>[];
  }
}
