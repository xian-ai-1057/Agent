/**
 * 框架共用的資料型別。
 *
 * 對話訊息一律使用 OpenAI chat 格式（LiteLLM Proxy、OpenRouter、Ollama
 * 等相容端點都說這個格式），所以 Message 的欄位名稱沿用該格式的寫法。
 */

/** 訊息裡的一次工具呼叫（OpenAI 格式）。 */
export interface ToolCallPayload {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
  [key: string]: unknown;
}

/** 一則對話訊息（OpenAI 格式）。 */
export interface Message {
  role: "system" | "user" | "assistant" | "tool";
  content: string | null;
  tool_calls?: ToolCallPayload[];
  tool_call_id?: string;
  name?: string;
  /** Claude 的 thinking 區塊，必須原樣傳回給模型。 */
  thinking_blocks?: unknown[];
}

/** 模型提出的一次工具呼叫。 */
export interface ToolCall {
  /** 模型給這次呼叫的識別碼，回傳結果時要對應。 */
  id: string;
  name: string;
  /** 已解析的參數。 */
  arguments: Record<string, unknown>;
  /** 參數不是合法 JSON 時的錯誤訊息。 */
  parseError?: string;
}

/** 一次工具呼叫的結果。 */
export interface ToolResult {
  callId: string;
  name: string;
  /** 要送回模型的文字。 */
  output: string;
  /** 是否為失敗（被拒絕、找不到工具、執行出錯）。 */
  isError: boolean;
}

/** Token 用量。 */
export interface Usage {
  inputTokens: number;
  outputTokens: number;
}

/** 模型一次回應的完整內容。 */
export interface ModelResponse {
  /** assistant 訊息，原樣存進對話紀錄。 */
  message: Message;
  toolCalls: ToolCall[];
  usage: Usage;
  /** 模型停止的原因（stop、length…）。 */
  finishReason: string;
}

/** 一次 run 的結果。 */
export interface RunResult {
  /** agent 最後的文字回覆。 */
  output: string;
  /** 這次 run 結束後的完整對話紀錄。 */
  messages: Message[];
  /** 這次 run 累計的 token 用量。 */
  usage: Usage;
  /** 呼叫模型的次數。 */
  turns: number;
  /** end_turn、max_turns、max_tokens，或模型回報的其他原因。 */
  stopReason: string;
}

/** 執行期間傳給工具與 hooks 的環境資訊。 */
export interface RunContext {
  agentName: string;
  /** 專案根目錄，內建檔案工具只能存取這個目錄底下。 */
  workdir: string;
  sessionId: string;
  /** 呼叫 run 時傳進來的任意物件（資料庫連線、使用者資訊…）。 */
  deps: unknown;
  /** 使用者中斷時會觸發，長時間執行的工具可以監聽它提早結束。 */
  signal?: AbortSignal;
}

/** 主迴圈串流出來的事件。 */
export type Event =
  | { type: "text_delta"; text: string }
  | { type: "message"; message: Message }
  | { type: "tool_call"; call: ToolCall }
  | { type: "tool_result"; result: ToolResult }
  | { type: "run_end"; result: RunResult };
