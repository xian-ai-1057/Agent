/** 呼叫 harness web 的 JSON API。 */

import type {
  ApprovalDecision,
  ConnectionSettings,
  CurrentUser,
  Feedback,
  FileEntry,
  IssuedPassword,
  MemberInfo,
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
  ) =>
    request<SessionSummary>("POST", "/sessions", {
      text,
      agent,
      attachments,
      ...(options ? { options } : {}),
    }),
  send: (id: string, text: string, attachments: string[] = []) =>
    request<SessionSummary>("POST", `/sessions/${id}/messages`, {
      text,
      attachments,
    }),
  /** folder 是要放進的工作資料夾（FolderInfo.id）；沒有時是「我的檔案」。 */
  upload: async (file: File, folder?: string | null): Promise<UploadedFile> => {
    const form = new FormData();
    form.append("file", file, file.name);
    const query = folder ? `?folder=${encodeURIComponent(folder)}` : "";
    const response = await fetch(`/api/uploads${query}`, {
      method: "POST",
      headers: { "X-Harness-Upload": "1" },
      body: form,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      checkUnauthorized(response.status, "/uploads", data);
      const message = (data as { error?: string }).error ?? response.statusText;
      throw new ApiError(response.status, message);
    }
    const [uploaded] = (data as { files: UploadedFile[] }).files;
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

  // 以下只有 Python 版的伺服器支援，先看 ProjectInfo.features 再呼叫。
  renameSession: (id: string, title: string) =>
    request<SessionSummary>("PATCH", `/sessions/${id}`, { title }),
  deleteSession: (id: string) => request("DELETE", `/sessions/${id}`),
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
};

function folderQuery(folder?: string | null): string {
  return folder ? `&folder=${encodeURIComponent(folder)}` : "";
}

/**
 * 工作資料夾裡一個檔案的網址（Python 版），可以直接放進 <img>、<iframe>
 * 或用 fetch 讀取文字。download 為 true 時瀏覽器會下載而不是顯示。
 * folder 是 FolderInfo.id（通常是這段對話的資料夾）；沒有時伺服器用
 * 這個人自己的「我的檔案」。
 */
export function fileUrl(
  path: string,
  download = false,
  folder?: string | null,
): string {
  const query = `path=${encodeURIComponent(path)}${folderQuery(folder)}`;
  return `/api/files?${query}${download ? "&download=1" : ""}`;
}
