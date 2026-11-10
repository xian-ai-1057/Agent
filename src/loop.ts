/** 主迴圈：呼叫模型 → 執行工具 → 把結果送回，直到模型不再呼叫工具。 */

import type { Agent } from "./agent.ts";
import { Hooks } from "./hooks.ts";
import { complete, defaultModel, type ModelFn } from "./model.ts";
import {
  askPermission,
  checkPermission,
  definePermissions,
  type Permissions,
} from "./permissions.ts";
import { Session } from "./session.ts";
import { formatOutput, type Tool, ToolError, toToolSchema } from "./tool.ts";
import type {
  Event,
  Message,
  ModelResponse,
  RunContext,
  RunResult,
  ToolCall,
  ToolResult,
  Usage,
} from "./types.ts";

export interface RunOptions {
  /** 對話紀錄。傳入同一個 session 就能延續對話；不傳則是一次性的對話。 */
  session?: Session;
  hooks?: Hooks;
  /** 工具的權限設定，預設為 ask 模式。 */
  permissions?: Permissions;
  /** 任意物件，工具可從 context.deps 取得。 */
  deps?: unknown;
  /** 專案根目錄，預設為目前目錄。 */
  workdir?: string;
  /** 模型函式，預設連到環境變數指定的端點；測試時可換成假的。 */
  model?: ModelFn;
  /** 用來中斷執行（例如使用者按下 Ctrl-C）。 */
  signal?: AbortSignal;
}

const INTERRUPTED = "工具執行被中斷，沒有結果";

/**
 * 執行 agent 並逐步產生事件，適合接到聊天介面。
 * 最後一個事件一定是 run_end。
 */
export async function* runStream(
  agent: Agent,
  userInput: string,
  options: RunOptions = {},
): AsyncGenerator<Event> {
  const session = options.session ?? new Session();
  const hooks = options.hooks ?? new Hooks();
  const permissions = options.permissions ?? definePermissions();
  const model = options.model ?? complete;
  const context: RunContext = {
    agentName: agent.name,
    workdir: options.workdir ?? process.cwd(),
    sessionId: session.id,
    deps: options.deps,
    signal: options.signal,
  };
  const toolsByName = new Map(agent.tools.map((item) => [item.name, item]));
  const schemas = agent.tools.map(toToolSchema);

  closeDanglingToolCalls(session);
  await hooks.emit("runStart", userInput, context);
  session.append({ role: "user", content: userInput });

  const usage: Usage = { inputTokens: 0, outputTokens: 0 };
  let output = "";
  let stopReason = "max_turns";
  let turns = 0;
  while (turns < agent.maxTurns) {
    options.signal?.throwIfAborted();
    turns += 1;
    let response: ModelResponse | undefined;
    const request = {
      model: agent.model ?? defaultModel(),
      messages: buildRequest(agent, session.messages),
      tools: schemas,
      params: agent.modelParams,
      signal: options.signal,
    };
    for await (const item of model(request)) {
      if (typeof item === "string") yield { type: "text_delta", text: item };
      else response = item;
    }
    if (!response) throw new Error("模型函式沒有回傳 ModelResponse");
    usage.inputTokens += response.usage.inputTokens;
    usage.outputTokens += response.usage.outputTokens;
    session.append(response.message);
    yield { type: "message", message: response.message };

    if (response.toolCalls.length === 0) {
      output = response.message.content ?? "";
      stopReason = toStopReason(response.finishReason);
      break;
    }

    for (const call of response.toolCalls) yield { type: "tool_call", call };
    const results = await executeCalls(
      response.toolCalls,
      toolsByName,
      hooks,
      permissions,
      context,
    );
    for (const result of results) {
      session.append(toToolMessage(result));
      yield { type: "tool_result", result };
    }
  }

  const result: RunResult = {
    output,
    messages: session.messages,
    usage,
    turns,
    stopReason,
  };
  await hooks.emit("runEnd", result, context);
  yield { type: "run_end", result };
}

/** 執行 agent 直到結束，回傳最後結果。 */
export async function run(
  agent: Agent,
  userInput: string,
  options: RunOptions = {},
): Promise<RunResult> {
  let result: RunResult | undefined;
  for await (const event of runStream(agent, userInput, options)) {
    if (event.type === "run_end") result = event.result;
  }
  if (!result) throw new Error("run 沒有產生結果");
  return result;
}

/** 把工具呼叫的參數轉成適合顯示給人看的短字串。 */
export function formatArguments(call: ToolCall, limit = 200): string {
  const text = JSON.stringify(call.arguments);
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

/** 在對話紀錄前面加上 system prompt。 */
function buildRequest(agent: Agent, messages: Message[]): Message[] {
  if (!agent.instructions) return [...messages];
  return [{ role: "system", content: agent.instructions }, ...messages];
}

function toStopReason(finishReason: string): string {
  if (finishReason === "stop" || finishReason === "tool_calls") {
    return "end_turn";
  }
  return finishReason === "length" ? "max_tokens" : finishReason;
}

function toToolMessage(result: ToolResult): Message {
  return {
    role: "tool",
    tool_call_id: result.callId,
    name: result.name,
    content: result.isError ? `[錯誤] ${result.output}` : result.output,
  };
}

/**
 * 替上次中斷時沒有結果的工具呼叫補上結果。
 *
 * 模型要求每個工具呼叫都要有對應的結果，否則下一次請求會失敗；使用者
 * 在工具執行途中按 Ctrl-C 就會留下這種狀態。
 */
function closeDanglingToolCalls(session: Session): void {
  const answered = new Set(
    session.messages
      .filter((message) => message.role === "tool")
      .map((message) => message.tool_call_id),
  );
  for (const message of [...session.messages]) {
    for (const call of message.tool_calls ?? []) {
      if (answered.has(call.id)) continue;
      session.append(
        toToolMessage({
          callId: call.id,
          name: call.function.name,
          output: INTERRUPTED,
          isError: true,
        }),
      );
    }
  }
}

/**
 * 執行同一輪的所有工具呼叫，結果順序與呼叫順序相同。
 *
 * 權限確認一個一個來（避免同時跳出多個詢問），通過的工具再同時執行。
 */
async function executeCalls(
  calls: ToolCall[],
  toolsByName: Map<string, Tool>,
  hooks: Hooks,
  permissions: Permissions,
  context: RunContext,
): Promise<ToolResult[]> {
  const rejected = new Map<ToolCall, ToolResult>();
  for (const call of calls) {
    const rejection = await authorize(
      call,
      toolsByName,
      hooks,
      permissions,
      context,
    );
    if (rejection) rejected.set(call, rejection);
  }
  return Promise.all(
    calls.map((call) => {
      const rejection = rejected.get(call);
      if (rejection) return rejection;
      // authorize 已確認過工具存在。
      const target = toolsByName.get(call.name) as Tool;
      return execute(call, target, hooks, context);
    }),
  );
}

/** 檢查一次工具呼叫能不能執行；不能的話回傳要送回模型的錯誤結果。 */
async function authorize(
  call: ToolCall,
  toolsByName: Map<string, Tool>,
  hooks: Hooks,
  permissions: Permissions,
  context: RunContext,
): Promise<ToolResult | undefined> {
  const target = toolsByName.get(call.name);
  if (!target) return unknownTool(call, [...toolsByName.keys()]);
  if (call.parseError) {
    return failure(call, `參數不是合法的 JSON：${call.parseError}`);
  }

  const decisions = await hooks.emit("preToolUse", call, context);
  let hookAllowed = false;
  for (const decision of decisions) {
    if (decision?.action === "deny") {
      return failure(call, `這次呼叫被拒絕：${decision.reason}`);
    }
    if (decision?.action === "allow") hookAllowed = true;
  }

  const verdict = checkPermission(permissions, target);
  if (verdict === "deny") {
    return failure(call, "權限設定不允許使用這個工具，請改用其他做法");
  }
  if (verdict === "ask" && !hookAllowed) {
    if (!(await askPermission(permissions, call, target))) {
      return failure(call, "使用者沒有同意這次呼叫，請不要重試同樣的操作");
    }
  }
  return undefined;
}

/** 執行工具，並把任何失敗轉成模型看得懂的錯誤結果。 */
async function execute(
  call: ToolCall,
  target: Tool,
  hooks: Hooks,
  context: RunContext,
): Promise<ToolResult> {
  let result: ToolResult;
  try {
    const args = target.parse(call.arguments);
    const value = await target.execute(args, context);
    result = {
      callId: call.id,
      name: call.name,
      output: formatOutput(value),
      isError: false,
    };
  } catch (error) {
    // 工具是任意的專案程式碼，任何錯誤都要回報給模型讓它自行修正，
    // 不能讓整個對話中斷。
    result = failure(call, describeError(error));
  }
  await hooks.emit("postToolUse", call, result, context);
  return result;
}

function describeError(error: unknown): string {
  if (!(error instanceof Error)) return String(error);
  if (error instanceof ToolError || error.name === "Error") {
    return error.message;
  }
  return `${error.name}: ${error.message}`;
}

function unknownTool(call: ToolCall, available: string[]): ToolResult {
  const list = available.join("、") || "（沒有任何工具）";
  return failure(call, `沒有 ${call.name} 這個工具，可用的有：${list}`);
}

function failure(call: ToolCall, message: string): ToolResult {
  return { callId: call.id, name: call.name, output: message, isError: true };
}
