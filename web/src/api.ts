/** 呼叫 harness web 的 JSON API。 */

import type {
  ApprovalDecision,
  ConnectionSettings,
  Feedback,
  FileEntry,
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

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
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
  upload: async (file: File): Promise<UploadedFile> => {
    const form = new FormData();
    form.append("file", file, file.name);
    const response = await fetch("/api/uploads", {
      method: "POST",
      headers: { "X-Harness-Upload": "1" },
      body: form,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
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
  listFiles: (dir = "") =>
    request<FileEntry[]>("GET", `/files/list?dir=${encodeURIComponent(dir)}`),
};

/**
 * 工作資料夾裡一個檔案的網址（Python 版），可以直接放進 <img>、<iframe>
 * 或用 fetch 讀取文字。download 為 true 時瀏覽器會下載而不是顯示。
 */
export function fileUrl(path: string, download = false): string {
  const query = `path=${encodeURIComponent(path)}`;
  return `/api/files?${query}${download ? "&download=1" : ""}`;
}
