/** 呼叫 harness web 的 JSON API。 */

import type {
  AccessGroup,
  ApprovalDecision,
  AuditFilters,
  AuditPage,
  AuditVerification,
  BulkRequest,
  BulkResult,
  ChatCategory,
  ConnectionSettings,
  CurrentUser,
  Feedback,
  FileEntry,
  GroupList,
  IssuedPassword,
  KnowledgeAdminList,
  KnowledgeDocuments,
  KnowledgeHit,
  KnowledgeSettings,
  MemberInfo,
  ModelChoice,
  ModelTestResult,
  PermissionMode,
  PermissionSettings,
  ProjectInfo,
  SearchResult,
  SessionDetail,
  SessionOptions,
  SessionSummary,
  UploadedFile,
} from "../../src/web/protocol.ts";
import { LOCAL_KEY_REQUIRED } from "../../src/web/protocol.ts";
import { auditQuery } from "./components/admin/auditFormat.ts";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/**
 * 登入過期或還沒登入（多人帳號模式回 401）時要做的事，由 store 設定。
 * 登入與改密碼本身回 401 只是帳號或密碼不對，不算登入過期。
 */
let onUnauthorized: ((localKey: boolean) => void) | undefined;
const AUTH_ATTEMPTS = new Set(["/auth/login", "/auth/password"]);

export function setUnauthorizedHandler(
  handler: (localKey: boolean) => void,
): void {
  onUnauthorized = handler;
}

function checkUnauthorized(status: number, path: string, data: unknown): void {
  if (status !== 401 || AUTH_ATTEMPTS.has(path)) return;
  const code = (data as { code?: unknown } | null)?.code;
  onUnauthorized?.(code === LOCAL_KEY_REQUIRED);
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    checkUnauthorized(response.status, path, data);
    const message = (data as { error?: string }).error ?? response.statusText;
    throw new ApiError(response.status, message);
  }
  return data as T;
}

export const api = {
  project: () => request<ProjectInfo>("GET", "/project"),
  sessions: () => request<SessionSummary[]>("GET", "/sessions"),
  session: (id: string) => request<SessionDetail>("GET", `/sessions/${id}`),
  createSession: (
    text: string,
    agent: string,
    attachments: string[] = [],
    options?: Partial<SessionOptions>,
    category?: string,
  ) =>
    request<SessionSummary>("POST", "/sessions", {
      text,
      agent,
      attachments,
      ...(options ? { options } : {}),
      ...(category ? { category } : {}),
    }),
  send: (id: string, text: string, attachments: string[] = []) =>
    request<SessionSummary>("POST", `/sessions/${id}/messages`, {
      text,
      attachments,
    }),
  /** folder 是要放進的工作資料夾（FolderInfo.id）；沒有時是「我的檔案」。 */
  upload: async (file: File, folder?: string | null): Promise<UploadedFile> => {
    const query = folder ? `?folder=${encodeURIComponent(folder)}` : "";
    const [uploaded] = await sendFile<UploadedFile>(`/uploads${query}`, file);
    if (!uploaded) throw new ApiError(500, "上傳失敗");
    return uploaded;
  },
  retry: (id: string) =>
    request<SessionSummary>("POST", `/sessions/${id}/retry`, {}),
  stop: (id: string) => request("POST", `/sessions/${id}/stop`, {}),
  setMode: (id: string, mode: PermissionMode | null) =>
    request("PUT", `/sessions/${id}/mode`, { mode }),
  decide: (sessionId: string, approvalId: string, decision: ApprovalDecision) =>
    request("POST", `/sessions/${sessionId}/approvals/${approvalId}`, {
      decision,
    }),
  savePermissions: (settings: PermissionSettings) =>
    request("PUT", "/settings/permissions", settings),
  saveModel: (model: string | null, maxTurns: number | null) =>
    request("PUT", "/settings/model", { model, maxTurns }),
  saveConnection: (settings: ConnectionSettings) =>
    request("PUT", "/settings/connection", settings),
  testModel: () => request<ModelTestResult>("POST", "/settings/model/test", {}),
  discoverModels: (settings: Omit<ConnectionSettings, "model">) =>
    request<{ models: ModelChoice[] }>(
      "POST",
      "/settings/models/discover",
      settings,
    ),
  saveModelChoices: (models: string[]) =>
    request("PUT", "/settings/models", { models }),

  // 以下只有 Python 版的伺服器支援，先看 ProjectInfo.features 再呼叫。
  renameSession: (id: string, title: string) =>
    request<SessionSummary>("PATCH", `/sessions/${id}`, { title }),
  deleteSession: (id: string) => request("DELETE", `/sessions/${id}`),
  moveSession: (id: string, category: string | null) =>
    request<SessionSummary>("PATCH", `/sessions/${id}`, { category }),
  bulkSessions: (body: BulkRequest) =>
    request<BulkResult>("POST", "/sessions/bulk", body),
  categories: () => request<ChatCategory[]>("GET", "/categories"),
  createCategory: (name: string) =>
    request<ChatCategory>("POST", "/categories", { name }),
  renameCategory: (id: string, name: string) =>
    request<ChatCategory>("PATCH", `/categories/${id}`, { name }),
  deleteCategory: (id: string) => request("DELETE", `/categories/${id}`),
  searchSessions: (q: string) =>
    request<SearchResult[]>(
      "GET",
      `/sessions/search?q=${encodeURIComponent(q)}`,
    ),
  setOptions: (id: string, options: Partial<SessionOptions>) =>
    request<SessionOptions>("PUT", `/sessions/${id}/options`, options),
  setFeedback: (id: string, index: number, feedback: Feedback | null) =>
    request("PUT", `/sessions/${id}/feedback`, { index, feedback }),
  listFiles: (dir = "", folder?: string | null) =>
    request<FileEntry[]>(
      "GET",
      `/files/list?dir=${encodeURIComponent(dir)}${folderQuery(folder)}`,
    ),

  // 帳號（Python 版，ProjectInfo.features.accounts 為 true 時）。
  login: (username: string, password: string) =>
    request<CurrentUser>("POST", "/auth/login", { username, password }),
  logout: () => request("POST", "/auth/logout", {}),
  me: () => request<CurrentUser>("GET", "/auth/me"),
  changePassword: (current: string, next: string) =>
    request("PUT", "/auth/password", { current, next }),
  members: () => request<MemberInfo[]>("GET", "/admin/users"),
  addMember: (
    username: string,
    displayName: string,
    role: CurrentUser["role"],
  ) =>
    request<IssuedPassword>("POST", "/admin/users", {
      username,
      displayName,
      role,
    }),
  updateMember: (
    id: string,
    changes: Partial<Pick<MemberInfo, "displayName" | "role" | "disabled">>,
  ) => request<MemberInfo>("PATCH", `/admin/users/${id}`, changes),
  resetPassword: (id: string) =>
    request<IssuedPassword>("POST", `/admin/users/${id}/password`, {}),

  // 存取群組（features.groups）。
  groups: () => request<GroupList>("GET", "/admin/groups"),
  createGroup: (group: GroupChanges) =>
    request<AccessGroup>("POST", "/admin/groups", group),
  updateGroup: (id: string, changes: GroupChanges) =>
    request<AccessGroup>("PATCH", `/admin/groups/${id}`, changes),
  deleteGroup: (id: string) => request("DELETE", `/admin/groups/${id}`),

  // 知識庫（features.knowledgeBases）。
  knowledge: () => request<KnowledgeAdminList>("GET", "/knowledge"),
  createKnowledge: (base: { id: string } & KnowledgeSettings) =>
    request<{ id: string }>("POST", "/knowledge", base),
  updateKnowledge: (
    id: string,
    changes: KnowledgeSettings & { maintainers?: string[] },
  ) => request<{ id: string }>("PATCH", `/knowledge/${id}`, changes),
  deleteKnowledge: (id: string) => request("DELETE", `/knowledge/${id}`),
  knowledgeDocuments: (id: string) =>
    request<KnowledgeDocuments>("GET", `/knowledge/${id}/documents`),
  /** replace 是要換掉的文件路徑；沒有時新增（同名的已經存在時失敗）。 */
  uploadKnowledge: (
    id: string,
    file: File,
    options: { dir?: string; replace?: string } = {},
  ) => {
    const query = new URLSearchParams();
    if (options.dir) query.set("dir", options.dir);
    if (options.replace) query.set("replace", options.replace);
    const suffix = query.size ? `?${query}` : "";
    return sendFile<{ path: string; size: number }>(
      `/knowledge/${id}/documents${suffix}`,
      file,
    );
  },
  deleteKnowledgeDocument: (id: string, path: string) =>
    request(
      "DELETE",
      `/knowledge/${id}/documents?path=${encodeURIComponent(path)}`,
    ),
  searchKnowledge: (id: string, query: string, topK = 5) =>
    request<{ hits: KnowledgeHit[] }>("POST", `/knowledge/${id}/search`, {
      query,
      topK,
    }),
  // 稽核紀錄（features.audit）。
  audit: (filters: AuditFilters, before?: number) =>
    request<AuditPage>(
      "GET",
      `/admin/audit?${auditQuery(filters, { before, limit: 50 })}`,
    ),
  verifyAudit: () => request<AuditVerification>("GET", "/admin/audit/verify"),
};

/** 匯出稽核紀錄的網址（瀏覽器直接下載）。 */
export function auditExportUrl(
  filters: AuditFilters,
  format: "csv" | "jsonl",
): string {
  return `/api/admin/audit/export?${auditQuery(filters, { format })}`;
}

/** 用 multipart 上傳一個檔案（要帶 X-Harness-Upload 標頭），回傳 files。 */
async function sendFile<T>(path: string, file: File): Promise<T[]> {
  const form = new FormData();
  form.append("file", file, file.name);
  const response = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "X-Harness-Upload": "1" },
    body: form,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    checkUnauthorized(response.status, path, data);
    const message = (data as { error?: string }).error ?? response.statusText;
    throw new ApiError(response.status, message);
  }
  return (data as { files?: T[] }).files ?? [];
}

/** 新增或修改群組時送出的欄位。 */
export type GroupChanges = Partial<Omit<AccessGroup, "id" | "builtin">>;

function folderQuery(folder?: string | null): string {
  return folder ? `&folder=${encodeURIComponent(folder)}` : "";
}

/**
 * 工作資料夾裡一個檔案的網址（Python 版），可以直接放進 <img>、<iframe>
 * 或用 fetch 讀取文字。download 為 true 時瀏覽器會下載而不是顯示。
 * folder 是 FolderInfo.id（通常是這段對話的資料夾）；沒有時伺服器用
 * 這個人自己的「我的檔案」。
 */
/** 把 Word、PowerPoint 檔轉成 PDF 的網址（features.officePdf）。 */
export function officePdfUrl(path: string, folder?: string | null): string {
  return `/api/files/pdf?path=${encodeURIComponent(path)}${folderQuery(folder)}`;
}

export function fileUrl(
  path: string,
  download = false,
  folder?: string | null,
): string {
  const query = `path=${encodeURIComponent(path)}${folderQuery(folder)}`;
  return `/api/files?${query}${download ? "&download=1" : ""}`;
}
