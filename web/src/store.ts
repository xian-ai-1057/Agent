/**
 * 前端的狀態：專案資訊、所有對話的摘要、打開過的對話內容。
 *
 * 伺服器透過 SSE 推送事件，這裡把事件套用到狀態上；每次重新連線或
 * 一輪結束時，再跟伺服器拿一次完整內容，修正可能漏掉的部分。
 */

import { useSyncExternalStore } from "react";
import type {
  FilePreview,
  Message,
  ProjectInfo,
  ServerEvent,
  SessionDetail,
  SessionSummary,
} from "../../src/web/protocol.ts";
import { api } from "./api.ts";
import { navigate } from "./router.ts";

export interface State {
  project?: ProjectInfo;
  sessions: Record<string, SessionSummary>;
  details: Record<string, SessionDetail>;
  /** 以工具呼叫 id 為鍵，保留確認時看到的檔案內容（確認後伺服器就不再提供）。 */
  previews: Record<string, FilePreview>;
  connected: boolean;
  /** 載入專案資訊失敗時的說明。 */
  loadError?: string;
}

type Listener = () => void;

let state: State = {
  sessions: {},
  details: {},
  previews: {},
  connected: false,
};
const listeners = new Set<Listener>();

function setState(change: (current: State) => State): void {
  state = change(state);
  for (const listener of listeners) listener();
}

export function getState(): State {
  return state;
}

/** 取用狀態的一部分；選出的值改變時重新繪製。 */
export function useStore<T>(select: (current: State) => T): T {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => select(state),
  );
}

export async function refreshProject(): Promise<void> {
  try {
    const project = await api.project();
    setState((s) => ({ ...s, project, loadError: undefined }));
  } catch (error) {
    setState((s) => ({ ...s, loadError: (error as Error).message }));
  }
}

export async function refreshSessions(): Promise<void> {
  const list = await api.sessions();
  setState((s) => ({
    ...s,
    sessions: Object.fromEntries(list.map((item) => [item.id, item])),
  }));
}

/** 重新讀取一段對話的完整內容。 */
export async function loadSession(id: string): Promise<void> {
  const detail = await api.session(id);
  setState((s) => ({
    ...s,
    sessions: { ...s.sessions, [id]: detail.summary },
    details: { ...s.details, [id]: detail },
    previews: { ...s.previews, ...previewsOf(detail) },
  }));
}

/** 送出訊息後立刻放進畫面，不必等伺服器的事件。 */
export function upsertSummary(summary: SessionSummary): void {
  setState((s) => {
    const detail = s.details[summary.id];
    return {
      ...s,
      sessions: { ...s.sessions, [summary.id]: summary },
      details: detail
        ? { ...s.details, [summary.id]: { ...detail, summary } }
        : s.details,
    };
  });
}

/** 連上 SSE；斷線時 EventSource 會自己重連。 */
export function connect(): () => void {
  const source = new EventSource("/api/events");
  source.onmessage = (message) => {
    handle(JSON.parse(message.data) as ServerEvent);
  };
  source.onerror = () => setState((s) => ({ ...s, connected: false }));
  return () => source.close();
}

function handle(event: ServerEvent): void {
  if (event.type === "hello") {
    setState((s) => ({ ...s, connected: true }));
    void refreshProject();
    void refreshSessions();
    for (const id of Object.keys(state.details)) void loadSession(id);
    return;
  }
  if (event.type === "project") {
    void refreshProject();
    return;
  }
  if (event.type === "session") {
    upsertSummary(event.summary);
    return;
  }
  const id = event.sessionId;
  if (event.type === "session_deleted") {
    forget(id);
    return;
  }
  if (event.type === "approval" && event.approval.preview) {
    const preview = event.approval.preview;
    setState((s) => ({
      ...s,
      previews: { ...s.previews, [event.approval.callId]: preview },
    }));
  }
  const detail = state.details[id];
  if (!detail) {
    // 剛建立的對話可能還在讀取內容，讀到的內容又早於這個步驟；subagent
    // 的步驟不會在 run_end 前重新讀取，所以正在看的對話要再讀一次。
    if (event.type === "subagent_step" && location.pathname === `/s/${id}`) {
      void loadSession(id);
    }
    return;
  }
  const next = apply(detail, event);
  setState((s) => ({ ...s, details: { ...s.details, [id]: next } }));
  if (
    event.type === "run_end" ||
    event.type === "run_error" ||
    event.type === "run_stopped" ||
    event.type === "session_reload"
  ) {
    void loadSession(id);
  }
}

/**
 * 把一個事件套用到對話內容上。
 *
 * SSE 與讀取對話內容是兩條連線，事件可能比讀到的內容早或晚到，所以
 * 加訊息前先確認最後一則不是同一則。
 */
function apply(detail: SessionDetail, event: ServerEvent): SessionDetail {
  switch (event.type) {
    case "run_start":
      return {
        ...detail,
        partial: "",
        messages:
          event.text === null
            ? detail.messages
            : append(detail.messages, { role: "user", content: event.text }),
      };
    case "text_delta":
      return { ...detail, partial: detail.partial + event.text };
    case "message":
    case "tool_result":
      return {
        ...detail,
        partial: "",
        messages: append(detail.messages, event.message),
      };
    case "approval":
      return { ...detail, approvals: [...detail.approvals, event.approval] };
    case "subagent_step": {
      const all = detail.subagentSteps ?? {};
      const steps = all[event.callId] ?? [];
      const index = steps.findIndex((step) => step.id === event.step.id);
      const next =
        index === -1
          ? [...steps, event.step]
          : steps.map((step, i) => (i === index ? event.step : step));
      return {
        ...detail,
        subagentSteps: { ...all, [event.callId]: next },
      };
    }
    case "approval_resolved":
      return {
        ...detail,
        approvals: detail.approvals.filter((a) => a.id !== event.approvalId),
      };
    case "run_end":
    case "run_error":
    case "run_stopped":
      return { ...detail, partial: "", approvals: [] };
    default:
      return detail;
  }
}

function append(messages: Message[], message: Message): Message[] {
  const last = messages.at(-1);
  if (last && JSON.stringify(last) === JSON.stringify(message)) return messages;
  return [...messages, message];
}

function previewsOf(detail: SessionDetail): Record<string, FilePreview> {
  const found: Record<string, FilePreview> = {};
  for (const approval of detail.approvals) {
    if (approval.preview) found[approval.callId] = approval.preview;
  }
  return found;
}

/** 改了這段對話的權限模式後，先更新畫面。 */
export function setDetailMode(id: string, mode: SessionDetail["mode"]): void {
  setState((s) => {
    const detail = s.details[id];
    if (!detail) return s;
    return { ...s, details: { ...s.details, [id]: { ...detail, mode } } };
  });
}

/**
 * 把一段對話從畫面上拿掉；正在看這段對話時回到新對話。
 * 刪除成功後呼叫，伺服器的 session_deleted 事件也會呼叫，重複呼叫沒關係。
 */
function forget(id: string): void {
  if (location.pathname === `/s/${id}`) navigate("/");
  if (!state.sessions[id] && !state.details[id]) return;
  setState((s) => {
    const { [id]: _summary, ...sessions } = s.sessions;
    const { [id]: _detail, ...details } = s.details;
    return { ...s, sessions, details };
  });
}

/**
 * 改對話名稱（Python 版）：先改畫面，伺服器回報失敗時改回原本的名稱並
 * 丟出錯誤。名稱沒變或是空白時什麼都不做。
 */
export async function renameSession(id: string, title: string): Promise<void> {
  const before = state.sessions[id];
  const next = title.trim();
  if (!before || !next || next === before.title) return;
  upsertSummary({ ...before, title: next });
  try {
    upsertSummary(await api.renameSession(id, next));
  } catch (error) {
    const current = state.sessions[id];
    if (current) upsertSummary({ ...current, title: before.title });
    throw error;
  }
}

/**
 * 刪除對話（Python 版）：伺服器確認刪除後才從畫面拿掉，失敗時丟出錯誤、
 * 畫面不動。不先拿掉：伺服器可能拒絕（例如對話還在執行），先換頁會讓
 * 使用者離開一段其實沒被刪掉的對話。正在看這段對話時會回到新對話。
 */
export async function deleteSession(id: string): Promise<void> {
  await api.deleteSession(id);
  forget(id);
}
