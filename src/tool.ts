/**
 * 定義 agent 可以呼叫的工具。
 *
 * 參數可以用 zod schema（會自動驗證並推導出 execute 的參數型別），也
 * 可以直接寫 JSON schema。
 */

import { z } from "zod";
import type { RunContext } from "./types.ts";

export type JsonSchema = Record<string, unknown>;

/** 送給模型的工具定義（OpenAI function calling 格式）。 */
export interface ToolSchema {
  type: "function";
  function: { name: string; description: string; parameters: JsonSchema };
}

/** 一個可以給 agent 使用的工具。 */
export interface Tool {
  /** 工具名稱，模型用這個名稱呼叫。 */
  name: string;
  /** 給模型看的用途說明。模型靠它決定什麼時候呼叫。 */
  description: string;
  /** 參數的 JSON schema。 */
  parameters: JsonSchema;
  /** 執行前是否需要使用者同意。 */
  needsApproval: boolean;
  /** 驗證模型給的參數，不合格時丟出錯誤。 */
  parse(args: Record<string, unknown>): unknown;
  // biome-ignore lint/suspicious/noExplicitAny: 參數型別由各工具自己決定
  execute(args: any, context: RunContext): unknown;
}

/** 用 zod schema 定義參數的工具。 */
export interface ZodToolDefinition<Schema extends z.ZodType> {
  name: string;
  description: string;
  parameters: Schema;
  needsApproval?: boolean;
  execute(args: z.infer<Schema>, context: RunContext): unknown;
}

/** 用 JSON schema 定義參數（或沒有參數）的工具。 */
export interface PlainToolDefinition {
  name: string;
  description: string;
  parameters?: JsonSchema;
  needsApproval?: boolean;
  // biome-ignore lint/suspicious/noExplicitAny: JSON schema 無法推導型別
  execute(args: any, context: RunContext): unknown;
}

const NO_OUTPUT = "(完成，沒有輸出)";
const EMPTY_PARAMETERS: JsonSchema = { type: "object", properties: {} };

/** 工具刻意回報給模型的錯誤，訊息會原樣送回。 */
export class ToolError extends Error {
  override name = "ToolError";
}

/**
 * 建立工具。
 *
 * @example
 * const add = tool({
 *   name: "add",
 *   description: "把兩個整數相加。",
 *   parameters: z.object({
 *     a: z.number().int().describe("第一個數"),
 *     b: z.number().int().describe("第二個數"),
 *   }),
 *   execute: ({ a, b }) => a + b,
 * });
 */
export function tool<Schema extends z.ZodType>(
  definition: ZodToolDefinition<Schema>,
): Tool;
export function tool(definition: PlainToolDefinition): Tool;
export function tool(
  definition: ZodToolDefinition<z.ZodType> | PlainToolDefinition,
): Tool {
  if (!definition.name || typeof definition.execute !== "function") {
    throw new TypeError("工具必須有 name 與 execute");
  }
  const base = {
    name: definition.name,
    description: definition.description || definition.name,
    needsApproval: definition.needsApproval ?? false,
    execute: definition.execute as Tool["execute"],
  };
  const parameters = definition.parameters;
  if (isZodSchema(parameters)) {
    return {
      ...base,
      parameters: toJsonSchema(parameters),
      parse: (args) => parseWithZod(parameters, args),
    };
  }
  return {
    ...base,
    parameters: parameters ?? EMPTY_PARAMETERS,
    parse: (args) => args,
  };
}

/** 判斷一個值是不是可以當成工具使用的物件。 */
export function isToolLike(value: unknown): value is PlainToolDefinition {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.name === "string" &&
    typeof candidate.execute === "function"
  );
}

/** 轉成送給模型的工具定義。 */
export function toToolSchema(target: Tool): ToolSchema {
  return {
    type: "function",
    function: {
      name: target.name,
      description: target.description,
      parameters: target.parameters,
    },
  };
}

/** 把工具的回傳值轉成要送回模型的文字。 */
export function formatOutput(value: unknown): string {
  if (value === undefined || value === null || value === "") return NO_OUTPUT;
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function isZodSchema(value: unknown): value is z.ZodType {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as { safeParse?: unknown }).safeParse === "function"
  );
}

function toJsonSchema(schema: z.ZodType): JsonSchema {
  // io: "input" 讓有預設值的參數不會被標成必填。
  const full = z.toJSONSchema(schema, { io: "input" }) as JsonSchema;
  const { $schema: _, ...rest } = full;
  return rest;
}

function parseWithZod(schema: z.ZodType, args: unknown): unknown {
  const parsed = schema.safeParse(args);
  if (!parsed.success) {
    throw new ToolError(`參數不符合格式：\n${z.prettifyError(parsed.error)}`);
  }
  return parsed.data;
}
