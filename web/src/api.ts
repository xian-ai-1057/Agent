/** 呼叫 harness web 的 JSON API。 */

import type {
  ApprovalDecision,
  ConnectionSettings,
  ModelTestResult,
  PermissionMode,
  PermissionSettings,
  ProjectInfo,
  SessionDetail,
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
  createSession: (text: string, agent: string, attachments: string[] = []) =>
    request<SessionSummary>("POST", "/sessions", { text, agent, attachments }),
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
};
