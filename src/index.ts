/**
 * Harness：把既有專案的功能包成工具，交給聊天 agent 使用。
 *
 * @example
 * import { defineAgent, run, tool } from "agent-harness";
 * import { z } from "zod";
 *
 * const add = tool({
 *   name: "add",
 *   description: "把兩個整數相加。",
 *   parameters: z.object({ a: z.number(), b: z.number() }),
 *   execute: ({ a, b }) => a + b,
 * });
 * const agent = defineAgent({
 *   name: "calc",
 *   instructions: "你是計算機。",
 *   tools: [add],
 * });
 * const result = await run(agent, "1 + 2 是多少？");
 * console.log(result.output);
 */

export { type Agent, type AgentOptions, defineAgent } from "./agent.ts";
export { fileTools } from "./builtin/files.ts";
export { isRecord, lastMessage, modelHistory } from "./compaction.ts";
export {
  allow,
  type Decision,
  deny,
  type HookEvent,
  type HookHandlers,
  Hooks,
} from "./hooks.ts";
export { formatArguments, type RunOptions, run, runStream } from "./loop.ts";
export {
  connectMcpServer,
  type McpConnection,
  type McpServerConfig,
} from "./mcp.ts";
export {
  createModel,
  type ModelFn,
  type ModelOptions,
  type ModelRequest,
} from "./model.ts";
export {
  type Approver,
  definePermissions,
  type PermissionMode,
  type Permissions,
} from "./permissions.ts";
export { loadProject, type Project } from "./project.ts";
export {
  createSession,
  latestSession,
  listSessionIds,
  loadSession,
  Session,
} from "./session.ts";
export {
  type JsonSchema,
  type Tool,
  ToolError,
  type ToolSchema,
  tool,
} from "./tool.ts";
export type {
  Event,
  Message,
  ModelResponse,
  RunContext,
  RunResult,
  ToolCall,
  ToolResult,
  Usage,
} from "./types.ts";
