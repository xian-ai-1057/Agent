/** Agent 的設定。 */

import type { Tool } from "./tool.ts";

/** 一個 agent：一段指示加上一組可用的工具。 */
export interface Agent {
  name: string;
  /** system prompt。 */
  instructions: string;
  tools: Tool[];
  /** 模型名稱，沒寫時使用環境變數 HARNESS_MODEL。 */
  model?: string;
  /** 額外傳給模型端點的參數（max_tokens…）。 */
  modelParams: Record<string, unknown>;
  /** 一次 run 最多呼叫模型幾次，避免無限迴圈。 */
  maxTurns: number;
  /** 給人看的用途說明。 */
  description: string;
}

export type AgentOptions = Pick<Agent, "name"> & Partial<Omit<Agent, "name">>;

export const DEFAULT_MAX_TURNS = 20;

/**
 * 建立 agent，沒給的欄位補上預設值。
 *
 * @throws 工具名稱重複時。
 */
export function defineAgent(options: AgentOptions): Agent {
  const tools = options.tools ?? [];
  const names = tools.map((item) => item.name);
  const duplicates = names.filter((name, i) => names.indexOf(name) !== i);
  if (duplicates.length > 0) {
    const list = [...new Set(duplicates)].join("、");
    throw new Error(`agent ${options.name} 有重複的工具名稱：${list}`);
  }
  return {
    name: options.name,
    instructions: options.instructions ?? "",
    tools,
    model: options.model,
    modelParams: options.modelParams ?? {},
    maxTurns: options.maxTurns ?? DEFAULT_MAX_TURNS,
    description: options.description ?? "",
  };
}
