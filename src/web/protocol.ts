/**
 * 網頁版伺服器與瀏覽器之間傳遞的資料格式。
 *
 * 這個檔案只有型別，前端也直接 import 它，兩邊的格式才不會走樣。
 */

import type { PermissionMode } from "../permissions.ts";
import type { Message, ToolResult } from "../types.ts";

export type { Message, PermissionMode, ToolResult };

/**
 * 對話目前的狀態。
 *   idle     這次啟動伺服器後還沒跑過
 *   running  正在跑
 *   waiting  等使用者確認工具呼叫
 *   done     跑完了
 *   error    出錯停下來（例如連不上模型）
 *   stopped  使用者按了停止
 */
export type SessionStatus =
  | "idle"
  | "running"
  | "waiting"
  | "done"
  | "error"
  | "stopped";

/** 正在跑的對話此刻在做什麼。 */
export interface Activity {
  kind: "thinking" | "tool" | "writing" | "waiting";
  /** kind 為 tool 或 waiting 時，相關的工具名稱。 */
  tool?: string;
  /**
   * 正在工作的 subagent（Python 版）；有多層時由外到內以 › 連接。
   * 沒有時表示是直接和使用者對話的 agent。
   */
  agent?: string;
}

/** subagent 內部呼叫的一個工具（Python 版）。 */
export interface SubagentStep {
  /** 畫面用的識別碼，在同一次 ask_<名稱> 呼叫裡不重複。 */
  id: string;
  /** subagent 自己的工具呼叫 id，用來對應權限確認。 */
  callId: string;
  /** 呼叫這個工具的 subagent。 */
  agent: string;
  /** 由外到內經過的 agent。 */
  path: string[];
  name: string;
  arguments: Record<string, unknown>;
  status: "running" | "done" | "error";
  result?: string;
}

/** 側欄與「工作中」列表需要的對話摘要。 */
export interface SessionSummary {
  id: string;
  /** 第一則使用者訊息的開頭，或使用者改過的名稱。 */
  title: string;
  agent: string;
  /**
   * 這段對話自己選的模型（Python 版）；沒有時跟著 agent 或專案的設定。
   * 下一輪開始時生效。
   */
  model?: string | null;
  status: SessionStatus;
  activity?: Activity;
  /** 這一輪開始的時間（毫秒）。 */
  startedAt?: number;
  endedAt?: number;
  /** 最後一次有動靜的時間（毫秒），用來排序。 */
  updatedAt: number;
  /** 出錯時的說明。 */
  error?: string;
  /** 最近一輪裡回報錯誤的工具呼叫數。 */
  toolErrors: number;
  /** 最近一輪正常結束的原因（end_turn、max_turns、blocked…）。 */
  stopReason?: string;
  /** 被防護規則擋下時的說明（Python 版；擋下的內容不會存進紀錄）。 */
  notice?: string;
  /** 正在等使用者決定的工具呼叫，讓列表可以直接允許或拒絕。 */
  approval?: Approval;
}

/** 寫檔類工具執行前，給使用者看的內容。 */
export interface FilePreview {
  path: string;
  /** 檔案原本的內容，新檔案時為 null。 */
  before: string | null;
  after: string;
}

/** 一個等使用者決定的工具呼叫。 */
export interface Approval {
  id: string;
  sessionId: string;
  callId: string;
  tool: string;
  arguments: Record<string, unknown>;
  preview?: FilePreview;
}

/**
 * 使用者對工具呼叫的決定。
 *   once    只允許這一次
 *   always  加進 harness.yaml 的 allow 規則，以後都允許
 *   deny    拒絕
 */
export type ApprovalDecision = "once" | "always" | "deny";

/** 一段對話的完整內容。 */
export interface SessionDetail {
  summary: SessionSummary;
  messages: Message[];
  /** 模型還在產生、尚未存進紀錄的文字。 */
  partial: string;
  approvals: Approval[];
  /** 這段對話自己的權限模式；null 表示跟著專案設定。 */
  mode: PermissionMode | null;
  /**
   * subagent 內部呼叫的工具，依上層 ask_<名稱> 呼叫的 id 分組（Python 版）。
   * subagent 的對話不存檔，伺服器重新啟動後就沒有了。
   */
  subagentSteps?: Record<string, SubagentStep[]>;
  /** 這段對話的選項（Python 版），見 SessionOptions。 */
  options?: SessionOptions;
  /**
   * 使用者對回覆的評價（Python 版），以 assistant 訊息在 messages 裡的
   * 索引為鍵。
   */
  feedback?: Record<string, Feedback>;
  /**
   * 產生每則回覆的模型（Python 版），以 assistant 訊息在 messages 裡的索引
   * 為鍵；這個功能加入之前的回覆沒有記錄。
   */
  replyModels?: Record<string, string>;
}

/**
 * 一段對話自己的選項（Python 版）。每一項都是 null 時跟著 agent 的設定。
 * PUT /api/sessions/:id/options 可以只送要改的欄位；建立對話時也可以帶。
 */
export interface SessionOptions {
  /** 這段對話用的模型，必須是 ProjectInfo.models 裡的名稱。 */
  model: string | null;
  /** 這段對話可以用的工具名稱；只能是 agent 原本就有的工具。null 表示全部。 */
  tools: string[] | null;
  /** 這段對話一定要用的 skill 名稱，會在送給模型的訊息裡提示它先讀取。 */
  skills: string[];
}

/** 使用者對一則回覆的評價。 */
export interface Feedback {
  rating: "up" | "down";
  /** 倒讚時勾選的原因。 */
  reasons?: string[];
  comment?: string;
}

/** 對話搜尋的一筆結果（Python 版）。 */
export interface SearchResult {
  id: string;
  title: string;
  /** 命中訊息內容時，含關鍵字的一小段文字；只命中標題時沒有。 */
  snippet?: string;
  updatedAt: number;
}

/** 工作資料夾裡的一個檔案或資料夾（Python 版）。 */
export interface FileEntry {
  /** 相對於工作資料夾的路徑，用 / 分隔。 */
  path: string;
  name: string;
  kind: "file" | "dir";
  /** 檔案大小（位元組）；資料夾沒有。 */
  size?: number;
  /** 最後修改時間（毫秒）。 */
  modifiedAt: number;
}

/** 可以在輸入框選的一個模型（Python 版）。 */
export interface ModelOption {
  /** 交給 litellm 的模型名稱。 */
  name: string;
  /** 顯示用的名稱。 */
  label: string;
  /** 供應商名稱，例如 Anthropic、公司自架。 */
  provider: string;
  /** 是否看得懂圖片；不知道時沒有。 */
  vision?: boolean;
  /** 是否為專案預設的模型。 */
  isDefault?: boolean;
}

/** 可以在 ＋ 選單選的一個 skill（Python 版）。 */
export interface SkillInfo {
  name: string;
  description: string;
  /** 內建 skill 或專案 skills/ 裡的。 */
  builtin: boolean;
}

/**
 * 伺服器支援哪些功能（Python 版才有）。沒有這個欄位或某一項是 false 時，
 * 前端要把對應的介面藏起來，TypeScript 版的伺服器才不會壞掉。
 */
export interface ServerFeatures {
  /** GET /api/files、/api/files/list：預覽與列出工作資料夾的檔案。 */
  files: boolean;
  /** PATCH /api/sessions/:id（改名）、DELETE /api/sessions/:id。 */
  sessionAdmin: boolean;
  /** GET /api/sessions/search?q=。 */
  search: boolean;
  /** PUT /api/sessions/:id/options：每段對話自己選模型、工具、skills。 */
  sessionOptions: boolean;
  /** PUT /api/sessions/:id/feedback：對回覆按讚或倒讚。 */
  feedback: boolean;
}

export interface ToolInfo {
  name: string;
  description: string;
  needsApproval: boolean;
}

export interface ToolsetInfo {
  name: string;
  builtin: boolean;
  tools: ToolInfo[];
}

/** 設定值從哪裡來：agent 檔案、harness.yaml、環境變數或預設值。 */
export type SettingSource = "agent" | "config" | "env" | "default";

export interface AgentInfo {
  name: string;
  description: string;
  tools: string[];
  /** 實際使用的模型名稱；都沒設定時為 null。 */
  model: string | null;
  modelSource: SettingSource;
  maxTurns: number;
  maxTurnsSource: SettingSource;
}

export interface PermissionSettings {
  mode: PermissionMode;
  allow: string[];
  ask: string[];
  deny: string[];
}

export interface ProjectInfo {
  name: string;
  root: string;
  /** 伺服器是哪個版本，沒有時視為 typescript。 */
  runtime?: "typescript" | "python";
  defaultAgent: string;
  agents: AgentInfo[];
  toolsets: ToolsetInfo[];
  permissions: PermissionSettings;
  /**
   * 可以上傳檔案時才有（Python 版）。上傳用 POST /api/uploads
   * （multipart/form-data，要帶 X-Harness-Upload: 1 標頭），送出訊息時
   * 把回傳的 path 放進 attachments。
   */
  uploads?: { maxBytes: number };
  /** 伺服器支援哪些功能；TypeScript 版沒有這個欄位。 */
  features?: ServerFeatures;
  /** 輸入框可以選的模型（Python 版），預設模型排第一個。 */
  models?: ModelOption[];
  /** ＋ 選單可以選的 skills（Python 版）。 */
  skills?: SkillInfo[];
  model: {
    /** harness.yaml 裡的值，沒寫時為 null。 */
    config: { model: string | null; maxTurns: number | null };
    env: {
      model: string | null;
      baseURL: string | null;
      apiKeySet: boolean;
      /** 有設定的 *_API_KEY 環境變數名稱（Python 版直接連供應商時用）。 */
      keyVars?: string[];
      /** 都沒設定模型時使用的預設模型（Python 版）。 */
      fallbackModel?: string;
    };
    /**
     * 可以在網頁上設定連線時才有（Python 版）：選供應商、填金鑰與模型，
     * 用 PUT /api/settings/connection 儲存。
     */
    connection?: ConnectionInfo;
  };
}

/** 一家可以直接連線的模型供應商。 */
export interface ProviderInfo {
  /** 代號，也是模型名稱的開頭（anthropic/...）。 */
  id: string;
  label: string;
  defaultModel: string;
  needsKey: boolean;
  /** 金鑰的環境變數名稱；不需要金鑰時為 null。 */
  keyVar: string | null;
  /** 金鑰是否已經設定（不會送出金鑰的內容）。 */
  keySet: boolean;
  /** 可以自訂伺服器位址時（Ollama），目前的位址；否則為 null。 */
  baseURL: string | null;
}

/** 連線設定的狀態。 */
export interface ConnectionInfo {
  /** 預設 agent 的模型需要的金鑰或端點是否已經設定。 */
  ready: boolean;
  /** 目前使用的供應商代號、"endpoint"（OpenAI 相容端點），或清單以外時為 null。 */
  provider: string | null;
  providers: ProviderInfo[];
}

/** 連 OpenAI 相容端點時，連線設定裡的供應商代號。 */
export const ENDPOINT_PROVIDER = "endpoint";

/** PUT /api/settings/connection 的內容；apiKey、baseURL 留空表示沿用目前的值。 */
export interface ConnectionSettings {
  provider: string;
  apiKey: string;
  baseURL: string;
  model: string;
}

/** 上傳完成的檔案。 */
export interface UploadedFile {
  name: string;
  /** 相對於專案根目錄的路徑，送出訊息時放進 attachments。 */
  path: string;
  size: number;
}

/**
 * 訊息附件的說明行：伺服器把附件接在使用者訊息後面，每個一行，
 * 開頭是這個標記，接著是路徑（不含空白），後面可能還有給模型的說明。
 */
export const ATTACHMENT_MARKER = "[附件] ";

/** 測試模型連線的結果。 */
export interface ModelTestResult {
  ok: boolean;
  model?: string;
  message?: string;
}

/** 伺服器透過 SSE 推給瀏覽器的事件。 */
export type ServerEvent =
  | { type: "hello" }
  | { type: "session"; summary: SessionSummary }
  | { type: "run_start"; sessionId: string; text: string | null }
  | { type: "text_delta"; sessionId: string; text: string }
  | { type: "message"; sessionId: string; message: Message }
  | {
      type: "tool_result";
      sessionId: string;
      result: ToolResult;
      message: Message;
    }
  | { type: "approval"; sessionId: string; approval: Approval }
  /** subagent 呼叫了工具或拿到結果（Python 版）；依 step.id 新增或更新。 */
  | {
      type: "subagent_step";
      sessionId: string;
      /** 上層 ask_<名稱> 工具呼叫的 id。 */
      callId: string;
      step: SubagentStep;
    }
  | {
      type: "approval_resolved";
      sessionId: string;
      approvalId: string;
      decision: ApprovalDecision;
    }
  | { type: "run_end"; sessionId: string; stopReason: string }
  | { type: "run_error"; sessionId: string; message: string }
  | { type: "run_stopped"; sessionId: string }
  /** 對話紀錄被改寫（例如 Python 版壓縮對話），要重新讀取。 */
  | { type: "session_reload"; sessionId: string }
  /** 對話被刪除（Python 版）。 */
  | { type: "session_deleted"; sessionId: string }
  | { type: "project" };
