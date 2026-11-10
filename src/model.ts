/**
 * 模型呼叫層：連接任何 OpenAI 相容的端點。
 *
 * LiteLLM Proxy、OpenRouter、Ollama、vLLM、OpenAI 本身都是這種端點，
 * 所以換模型只需要改端點網址與模型名稱。主迴圈只認得 ModelFn 這個
 * 介面，測試時可以換成假的模型函式，完全不需要連網。
 */

import OpenAI from "openai";
import type { ToolSchema } from "./tool.ts";
import type {
  Message,
  ModelResponse,
  ToolCall,
  ToolCallPayload,
  Usage,
} from "./types.ts";

/** 一次模型請求。 */
export interface ModelRequest {
  model: string;
  /** 完整的請求訊息（含 system）。 */
  messages: Message[];
  tools: ToolSchema[];
  /** 額外傳給端點的參數（max_tokens、reasoning_effort…）。 */
  params: Record<string, unknown>;
  signal?: AbortSignal;
}

/** 模型函式：依序產生文字片段，最後產生一個 ModelResponse。 */
export type ModelFn = (
  request: ModelRequest,
) => AsyncIterable<string | ModelResponse>;

/** 連線設定，沒給的欄位會讀環境變數。 */
export interface ModelOptions {
  /** 端點網址，預設讀 HARNESS_BASE_URL，再來是 OPENAI_BASE_URL。 */
  baseURL?: string;
  /** 金鑰，預設讀 HARNESS_API_KEY，再來是 OPENAI_API_KEY。 */
  apiKey?: string;
}

/** 串流回應裡的一個片段（只列出會用到的欄位）。 */
export interface StreamChunk {
  choices?: {
    delta?: {
      content?: string | null;
      tool_calls?: ToolCallDelta[];
      thinking_blocks?: ThinkingBlock[] | null;
    };
    finish_reason?: string | null;
  }[];
  usage?: { prompt_tokens?: number; completion_tokens?: number } | null;
}

interface ToolCallDelta {
  index?: number;
  id?: string;
  function?: { name?: string; arguments?: string };
  [key: string]: unknown;
}

interface ThinkingBlock {
  type?: string;
  thinking?: string;
  signature?: string;
  data?: string;
}

/**
 * 取得預設的模型名稱。
 *
 * @throws 沒有設定環境變數 HARNESS_MODEL 時。
 */
export function defaultModel(): string {
  const model = process.env.HARNESS_MODEL;
  if (!model) {
    throw new Error(
      "尚未設定模型：請在 harness.yaml 寫 model，或設定環境變數 HARNESS_MODEL",
    );
  }
  return model;
}

/** 建立連到指定端點的模型函式。 */
export function createModel(options: ModelOptions = {}): ModelFn {
  let client: OpenAI | undefined;
  return async function* complete(request) {
    client ??= createClient(options);
    const body = {
      ...request.params,
      model: request.model,
      messages: request.messages,
      stream: true,
      stream_options: { include_usage: true },
      ...(request.tools.length > 0 ? { tools: request.tools } : {}),
    } as unknown as OpenAI.Chat.ChatCompletionCreateParamsStreaming;
    const stream = await client.chat.completions
      .create(body, { signal: request.signal })
      .catch((error: unknown) => {
        throw explainConnectionError(error, client?.baseURL);
      });
    yield* accumulate(stream as AsyncIterable<StreamChunk>);
  };
}

/** 預設的模型函式，連線設定全部來自環境變數。 */
export const complete: ModelFn = createModel();

/**
 * 把串流片段組合成完整的回應。
 *
 * 文字片段會立刻往外送；工具呼叫的參數是分段到達的，要等串流結束才
 * 組合完成。
 */
export async function* accumulate(
  chunks: AsyncIterable<StreamChunk>,
): AsyncGenerator<string | ModelResponse> {
  let content = "";
  let finishReason = "stop";
  const usage: Usage = { inputTokens: 0, outputTokens: 0 };
  const calls: ToolCallPayload[] = [];
  const thinking = new ThinkingCollector();

  for await (const chunk of chunks) {
    if (chunk.usage) {
      usage.inputTokens = chunk.usage.prompt_tokens ?? 0;
      usage.outputTokens = chunk.usage.completion_tokens ?? 0;
    }
    const choice = chunk.choices?.[0];
    if (!choice) continue;
    if (choice.finish_reason) finishReason = choice.finish_reason;
    const delta = choice.delta;
    if (!delta) continue;
    if (delta.content) {
      content += delta.content;
      yield delta.content;
    }
    for (const callDelta of delta.tool_calls ?? []) {
      mergeToolCall(calls, callDelta);
    }
    thinking.add(delta.thinking_blocks);
  }

  // index 不連續時陣列會有空位，先濾掉。
  const toolCalls = calls.filter(Boolean);
  const message: Message = {
    role: "assistant",
    content: content || (toolCalls.length > 0 ? null : ""),
  };
  if (toolCalls.length > 0) message.tool_calls = toolCalls;
  if (thinking.blocks.length > 0) message.thinking_blocks = thinking.blocks;
  yield {
    message,
    toolCalls: toolCalls.map(parseCall),
    usage,
    finishReason,
  };
}

/** 連不上端點時，把 SDK 的籠統訊息換成指出網址的說明。 */
function explainConnectionError(error: unknown, baseURL?: string): unknown {
  if (!(error instanceof OpenAI.APIConnectionError)) return error;
  return new Error(
    `無法連線到模型端點 ${baseURL}，請確認 HARNESS_BASE_URL 與端點是否已啟動`,
    { cause: error },
  );
}

function createClient(options: ModelOptions): OpenAI {
  const baseURL =
    options.baseURL ??
    process.env.HARNESS_BASE_URL ??
    process.env.OPENAI_BASE_URL;
  const apiKey =
    options.apiKey ?? process.env.HARNESS_API_KEY ?? process.env.OPENAI_API_KEY;
  if (!baseURL && !apiKey) {
    throw new Error(
      "尚未設定模型端點：請設定 HARNESS_BASE_URL（例如 LiteLLM Proxy 的網址）" +
        "與 HARNESS_API_KEY",
    );
  }
  // 本機端點（Ollama 等）不需要金鑰，但 SDK 要求一定要有值。
  return new OpenAI({ baseURL, apiKey: apiKey ?? "not-needed" });
}

/** 把一個工具呼叫片段併入目前累積的結果。 */
function mergeToolCall(calls: ToolCallPayload[], delta: ToolCallDelta): void {
  // 多數端點用 index 指出片段屬於哪個呼叫；少數端點不給 index，這時
  // 帶有 id 的片段視為新的呼叫，否則接在最後一個呼叫後面。
  const fallback = delta.id ? calls.length : Math.max(calls.length - 1, 0);
  const index = delta.index ?? fallback;
  const { index: _, id, function: fn, ...extra } = delta;
  let call = calls[index];
  if (!call) {
    call = { id: "", type: "function", function: { name: "", arguments: "" } };
    calls[index] = call;
  }
  // 保留端點額外給的欄位（例如 Gemini 必須傳回的簽章）。
  Object.assign(call, extra, { type: "function" });
  if (id) call.id = id;
  if (fn?.name) call.function.name += fn.name;
  if (fn?.arguments) call.function.arguments += fn.arguments;
}

/** 把工具呼叫的參數字串解析成物件。 */
function parseCall(payload: ToolCallPayload): ToolCall {
  const base = { id: payload.id, name: payload.function.name };
  const raw = payload.function.arguments || "{}";
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    return { ...base, arguments: {}, parseError: (error as Error).message };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { ...base, arguments: {}, parseError: "參數必須是 JSON 物件" };
  }
  return { ...base, arguments: parsed as Record<string, unknown> };
}

/**
 * 組合分段到達的 thinking 區塊。
 *
 * LiteLLM 串流 Claude 的回應時，thinking 的文字與簽章是分開送的：先是
 * 多個只有文字的片段，最後一個片段帶簽章。Claude 要求下一次請求原樣
 * 傳回這些區塊，所以要組回完整的形狀。
 */
class ThinkingCollector {
  blocks: ThinkingBlock[] = [];
  private text = "";

  add(fragments: ThinkingBlock[] | null | undefined): void {
    for (const fragment of fragments ?? []) {
      if (fragment.type === "redacted_thinking") {
        this.text = "";
        if (fragment.data) {
          this.blocks.push({ type: "redacted_thinking", data: fragment.data });
        }
        continue;
      }
      if (fragment.thinking) this.text += fragment.thinking;
      if (fragment.signature) {
        this.blocks.push({
          type: "thinking",
          thinking: this.text,
          signature: fragment.signature,
        });
        this.text = "";
      }
    }
  }
}
