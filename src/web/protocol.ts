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
}

/** 側欄與「工作中」列表需要的對話摘要。 */
export interface SessionSummary {
  id: string;
  /** 第一則使用者訊息的開頭。 */
  title: string;
  agent: string;
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
  defaultAgent: string;
  agents: AgentInfo[];
  toolsets: ToolsetInfo[];
  permissions: PermissionSettings;
  model: {
    /** harness.yaml 裡的值，沒寫時為 null。 */
    config: { model: string | null; maxTurns: number | null };
    env: { model: string | null; baseURL: string | null; apiKeySet: boolean };
  };
}

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
  | {
      type: "approval_resolved";
      sessionId: string;
      approvalId: string;
      decision: ApprovalDecision;
    }
  | { type: "run_end"; sessionId: string; stopReason: string }
  | { type: "run_error"; sessionId: string; message: string }
  | { type: "run_stopped"; sessionId: string }
  | { type: "project" };
