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
  /** 這段對話放在哪個專案（ChatCategory.id，Python 版）；沒有時是未分類。 */
  category?: string;
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
 *   once     只允許這一次
 *   session  這段對話之後都允許這個工具（Python 版；不寫進 harness.yaml）
 *   always   加進 harness.yaml 的 allow 規則，以後都允許
 *   deny     拒絕
 */
export type ApprovalDecision = "once" | "session" | "always" | "deny";

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
  /**
   * 這段對話的工作資料夾（FolderInfo.id）；沒有時是自己的「我的檔案」。
   * 訊息裡的附件、工具參數與 PRODUCED_MARKER 的路徑都相對於這個資料夾。
   */
  folder?: string | null;
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
  /** 權限確認可以選「這段對話都允許」（ApprovalDecision 的 session）。 */
  sessionApproval?: boolean;
  /** 多人帳號：登入、成員管理、每個人的資料分開（見 CurrentUser）。 */
  accounts?: boolean;
  /** 每段對話選工作資料夾（見 FolderInfo、SessionOptions.folder）。 */
  folders?: boolean;
  /**
   * GET /api/files/pdf?path=…&folder=…：把 Word、PowerPoint 檔轉成 PDF 送出
   * （伺服器用 LibreOffice 轉換並快取）。回 501 表示這台伺服器不能轉換，
   * 網頁改用瀏覽器裡的簡易預覽。
   */
  officePdf?: boolean;
  /**
   * 專案（對話的分類，見 ChatCategory）：GET/POST /api/categories、
   * PATCH/DELETE /api/categories/:id、PATCH /api/sessions/:id 的 category、
   * POST /api/sessions 的 category、POST /api/sessions/bulk（見 BulkRequest）。
   */
  categories?: boolean;
  /**
   * 存取群組（帳號模式）：管理者決定每位成員可以用哪些 agent、工具組與
   * 知識庫標籤（見 AccessGroup）。成員拿到的 ProjectInfo 已經只列出開放
   * 給他的部分。模型不歸群組管，見 ProjectInfo.models。
   */
  groups?: boolean;
  /**
   * 稽核紀錄（Python 版）：管理平台可以查詢與匯出登入、成員、群組、設定
   * 與工具核准的紀錄（見 AuditEntry）。
   */
  audit?: boolean;
}

/**
 * 使用者自己建立的專案，用來把對話分類整理（和 ChatGPT、Claude 的專案
 * 一樣；和 harness 的專案資料夾無關）。每個人只看得到自己的專案。刪除
 * 專案不會刪除裡面的對話，對話改成未分類。
 */
export interface ChatCategory {
  id: string;
  /** 1 到 50 個字，同一個人的專案名稱不能重複（不分大小寫）。 */
  name: string;
  createdAt: number;
  /** 裡面有幾段對話。 */
  sessionCount: number;
}

/**
 * POST /api/sessions/bulk：一次刪除或移動好幾段對話（最多 500 段）。
 * move 時 category 是目標專案，null 表示移出專案。
 */
export interface BulkRequest {
  action: "delete" | "move";
  ids: string[];
  category?: string | null;
}

/** 處理好的對話，以及略過的對話與原因（例如還在執行的對話不能刪除）。 */
export interface BulkResult {
  done: string[];
  skipped: { id: string; reason: string }[];
}

/**
 * 登入的人（Python 版）。
 *   admin   管理者：管理成員、模型連線、全站權限與工作資料夾
 *   member  成員：只能使用自己的對話、檔案與記憶
 */
export interface CurrentUser {
  id: string;
  username: string;
  displayName: string;
  role: "admin" | "member";
}

/**
 * 登入方式（Python 版）。
 *   local     只在本機使用、沒有建立任何帳號：不必登入，視為一位本機管理者
 *   accounts  要用帳號密碼登入；沒有登入時 API 一律回 401
 * POST /api/auth/login {username, password} 成功時設定 HttpOnly cookie 並回傳
 * CurrentUser；POST /api/auth/logout 登出；GET /api/auth/me 回傳 CurrentUser。
 * PUT /api/auth/password {current, next} 改自己的密碼。
 */
export type AuthMode = "local" | "accounts";

/** 成員管理頁看到的一位成員（只有管理者能讀，GET /api/admin/users）。 */
export interface MemberInfo extends CurrentUser {
  disabled: boolean;
  /** 建立時間（毫秒）。 */
  createdAt: number;
  /** 最後一次使用的時間（毫秒）；沒有登入過時沒有。 */
  lastSeenAt?: number;
}

/**
 * 新增成員或重設密碼的結果。密碼由伺服器產生，只會回傳這一次，
 * 請管理者交給成員；成員第一次登入後可以自己改。
 *   POST  /api/admin/users {username, displayName, role}
 *   PATCH /api/admin/users/:id {displayName?, role?, disabled?} → MemberInfo
 *   POST  /api/admin/users/:id/password
 */
export interface IssuedPassword {
  user: MemberInfo;
  password: string;
}

/**
 * 存取群組可以控制的資源類別。模型不在這裡：誰能用哪些模型、額度與限流
 * 由模型中台（LiteLLM Proxy）決定。
 */
export type AccessKind = "agents" | "toolsets" | "knowledge";

/**
 * 存取群組（Python 版帳號模式，只有管理者能讀寫）。每一類資源是名稱清單，
 * 或 null 表示全部。每位成員自動屬於內建的「所有人」（id 為 "everyone"，
 * members 是空的、不能刪除或改名），能用的資源是所屬群組的聯集；管理者
 * 不受限制。新建的群組沒有給的類別是空的（什麼都不能用）。
 *   GET    /api/admin/groups → {groups: AccessGroup[], catalog: GroupCatalog}
 *   POST   /api/admin/groups {name, description?, members?, agents?, …}
 *   PATCH  /api/admin/groups/:id {name?, description?, members?, agents?, …}
 *   DELETE /api/admin/groups/:id
 */
export interface AccessGroup {
  id: string;
  name: string;
  description: string;
  builtin: boolean;
  /** 成員的 id（MemberInfo.id）。 */
  members: string[];
  agents: string[] | null;
  toolsets: string[] | null;
  /** 可以搜尋的知識庫標籤；沒有標籤的文件所有人都搜尋得到。 */
  knowledge: string[] | null;
}

/** 群組頁可以勾選的資源。 */
export interface GroupCatalog {
  agents: { name: string; description: string }[];
  toolsets: { name: string; tools: string[] }[];
  knowledge: string[];
  /** 專案有沒有知識庫。 */
  hasKnowledge: boolean;
}

export interface GroupList {
  groups: AccessGroup[];
  catalog: GroupCatalog;
}

/**
 * 一筆稽核紀錄（Python 版，只有管理者能讀）。紀錄只能新增，不能修改或
 * 刪除；每一筆有前一筆的雜湊，串成一條鏈（匯出的檔案裡有雜湊）。
 *   GET /api/admin/audit?category&actor&since&until&before&limit → AuditPage
 *   GET /api/admin/audit/verify → AuditVerification
 *   GET /api/admin/audit/export?format=csv|jsonl&… → 下載檔案（匯出本身也會記一筆）
 */
export interface AuditEntry {
  /** 流水號，從 1 開始，越新越大。 */
  seq: number;
  /** 時間（毫秒）。 */
  at: number;
  /** 做這件事的人；登入失敗等不知道是誰時都是空字串。 */
  actor: { id: string; username: string };
  /** 動作代碼，例如 auth.login、member.update、approval.decide。 */
  action: string;
  /** 動作的中文名稱。 */
  label: string;
  /** 對象：帳號、群組名稱或工具名稱。 */
  target: string;
  /** 其他資訊，內容依動作而不同。 */
  detail: Record<string, unknown>;
  /** 請求的來源 IP。 */
  address: string;
}

/** 稽核紀錄的查詢條件；時間是毫秒，until 不包含。 */
export interface AuditFilters {
  /** 分類：auth、member、group、settings、approval、audit。 */
  category?: string;
  /** 帳號名稱（做這件事的人或對象），部分符合即可。 */
  actor?: string;
  since?: number;
  until?: number;
}

export interface AuditPage {
  /** 新的在前；下一頁用最後一筆的 seq 當 before。 */
  entries: AuditEntry[];
  /** 符合條件的總筆數。 */
  total: number;
  /** 分類代碼 → 中文名稱。 */
  categories: Record<string, string>;
}

/** 從第一筆開始檢查雜湊鏈的結果；brokenAt 是第一筆對不上的流水號。 */
export interface AuditVerification {
  ok: boolean;
  checked: number;
  brokenAt?: number;
}

/**
 * 一個工作資料夾（Python 版）。agent 的檔案工具、執行指令與沙盒都只看得到
 * 這段對話選的資料夾；上傳的檔案也存在裡面。
 *   private  每個人自己的「我的檔案」，只有本人看得到。本機模式（AuthMode
 *            為 local）沒有帳號，只有這一個，路徑是專案資料夾本身，而且
 *            ServerFeatures.folders 為 false。
 *   shared   管理者在 harness.yaml 的 workspaces 開放的資料夾；升級前的舊
 *            對話另有一個 id 為 "legacy" 的「舊資料」資料夾，只有接手舊資料
 *            的管理者看得到。
 * 資料夾在對話開始時決定；之後 PUT /options 改 folder 會被拒絕（400）。
 */
export interface FolderInfo {
  id: string;
  name: string;
  kind: "private" | "shared";
  /** false 時 agent 只能讀，不能寫入或修改。 */
  writable: boolean;
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
  /** 登入方式與目前登入的人（Python 版）。 */
  auth?: { mode: AuthMode; user: CurrentUser };
  /** 這個人可以使用的工作資料夾，預設的（自己的「我的檔案」）排第一個。 */
  folders?: FolderInfo[];
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
  /**
   * POST /api/settings/models/discover {provider, baseURL, apiKey} 可以列出
   * 供應商或端點的模型（Python 版，見 ModelChoice）。換成別的端點網址時
   * 伺服器不會送出存好的金鑰。
   */
  canDiscover?: boolean;
  /**
   * harness.yaml 的 models：開放在對話中切換的模型名稱（Python 版）。
   * PUT /api/settings/models {models: string[]} 整份換掉。
   */
  choices?: string[];
}

/** 供應商或端點提供的一個模型（POST /api/settings/models/discover）。 */
export interface ModelChoice {
  /** 寫進設定的模型名稱（litellm 格式；端點則是端點上的名稱）。 */
  name: string;
  label: string;
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

/**
 * 沙盒產生檔案的說明行（Python 版）：run_python 執行後新增或修改的檔案，
 * 伺服器接在工具結果後面，每個一行，開頭是這個標記，接著是相對於專案
 * 根目錄的路徑。網頁用它顯示檔案卡片與縮圖。
 */
export const PRODUCED_MARKER = "[產生的檔案] ";

/**
 * 不必登入的本機模式（Python 版）每次啟動會產生一把鑰匙，放在終端機顯示
 * 的網址上（?key=…）。沒有帶鑰匙時 API 回 401，錯誤內容的 code 是這個
 * 值：要請使用者重新打開終端機上的網址，而不是顯示登入畫面。
 */
export const LOCAL_KEY_REQUIRED = "local_key_required";

/** 使用者拒絕工具呼叫時，交給模型的工具結果（網頁上改顯示「你拒絕了這個動作」）。 */
export const DENIED_RESULT = "使用者沒有同意這次呼叫，請不要重試同樣的操作";

/**
 * 伺服器重新啟動時，正在跑或等待確認的那一輪會中斷（Python 版）：
 * 摘要的 status 為 stopped、stopReason 為這個值，notice 說明原因；
 * POST /api/sessions/:id/retry 可以接著做。
 */
export const INTERRUPTED_STOP_REASON = "interrupted";

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
  | { type: "project" }
  /** 專案（ChatCategory）有變動：重新讀取 GET /api/categories。 */
  | { type: "categories" };
