/**
 * 網頁版的核心：管理一個專案裡所有對話的執行狀態。
 *
 * 每段對話可以同時各自執行。執行過程中的每一步都會變成 ServerEvent
 * 推給訂閱者（伺服器再用 SSE 轉給瀏覽器）；需要使用者同意的工具呼叫會
 * 停下來，等瀏覽器回覆 resolveApproval。
 */

import { randomUUID } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { type Agent, DEFAULT_MAX_TURNS } from "../agent.ts";
import { fileTools, resolvePath } from "../builtin/files.ts";
import { lastMessage } from "../compaction.ts";
import { runStream } from "../loop.ts";
import { complete, defaultModel, type ModelFn } from "../model.ts";
import {
  type Approver,
  PERMISSION_MODES,
  type PermissionMode,
  type Permissions,
} from "../permissions.ts";
import type { Project } from "../project.ts";
import {
  createSession,
  listSessionIds,
  loadSession,
  type Session,
} from "../session.ts";
import type { Tool } from "../tool.ts";
import type { RunContext, ToolCall } from "../types.ts";
import {
  type AgentOverrides,
  type ModelSettings,
  readAgentOverrides,
  readModelSettings,
  saveModelSettings,
  savePermissions,
} from "./config.ts";
import type {
  Activity,
  AgentInfo,
  Approval,
  ApprovalDecision,
  FilePreview,
  ModelTestResult,
  PermissionSettings,
  ProjectInfo,
  ServerEvent,
  SessionDetail,
  SessionStatus,
  SessionSummary,
} from "./protocol.ts";

/** 帶 HTTP 狀態碼的錯誤，伺服器會原樣回給瀏覽器。 */
export class WebError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export interface WorkspaceOptions {
  /** 模型函式，預設連到環境變數指定的端點；測試時可換成假的。 */
  model?: ModelFn;
}

type Listener = (event: ServerEvent) => void;

interface PendingApproval {
  approval: Approval;
  finish: (allowed: boolean) => void;
}

interface SessionState {
  session: Session;
  agent: string;
  title: string;
  status: SessionStatus;
  activity?: Activity;
  startedAt?: number;
  endedAt?: number;
  updatedAt: number;
  error?: string;
  toolErrors: number;
  stopReason?: string;
  partial: string;
  mode: PermissionMode | null;
  approvals: Map<string, PendingApproval>;
  /** 有值表示正在執行。 */
  controller?: AbortController;
}

const UNTITLED = "新對話";
const TITLE_LENGTH = 40;
const PREVIEW_LIMIT = 200_000;
const MODEL_TEST_TIMEOUT = 20_000;

export class Workspace {
  private readonly states = new Map<string, SessionState>();
  private readonly listeners = new Set<Listener>();
  private readonly model: ModelFn;
  private settings: ModelSettings;
  private readonly overrides: Record<string, AgentOverrides>;
  readonly project: Project;

  constructor(project: Project, options: WorkspaceOptions = {}) {
    this.project = project;
    this.model = options.model ?? complete;
    this.settings = readModelSettings(project.root);
    this.overrides = readAgentOverrides(project.root);
  }

  /** 訂閱事件，回傳取消訂閱的函式。 */
  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  projectInfo(): ProjectInfo {
    const { project } = this;
    const env = process.env;
    return {
      name: project.name,
      root: project.root,
      defaultAgent: project.defaultAgent,
      agents: Object.keys(project.agents).map((name) => this.agentInfo(name)),
      toolsets: Object.entries(project.toolsets).map(([name, tools]) => ({
        name,
        builtin: tools === fileTools,
        tools: tools.map((item) => ({
          name: item.name,
          description: item.description,
          needsApproval: item.needsApproval,
        })),
      })),
      permissions: this.permissionSettings(),
      model: {
        config: { ...this.settings },
        env: {
          model: env.HARNESS_MODEL ?? null,
          baseURL: env.HARNESS_BASE_URL ?? env.OPENAI_BASE_URL ?? null,
          apiKeySet: Boolean(env.HARNESS_API_KEY ?? env.OPENAI_API_KEY),
        },
      },
    };
  }

  /** 所有對話的摘要，最近有動靜的排前面。 */
  listSessions(): SessionSummary[] {
    for (const id of listSessionIds(this.project.sessionsDir)) this.find(id);
    return [...this.states.values()]
      .map((state) => this.summary(state))
      .sort((a, b) => b.updatedAt - a.updatedAt);
  }

  /** @throws WebError 找不到這段對話時。 */
  getSession(id: string): SessionDetail {
    const state = this.require(id);
    return {
      summary: this.summary(state),
      messages: state.session.messages,
      partial: state.partial,
      approvals: [...state.approvals.values()].map((item) => item.approval),
      mode: state.mode,
    };
  }

  /**
   * 送出一則訊息並在背景開始執行。沒給 sessionId 時建立新的對話。
   *
   * @throws WebError 訊息是空的、agent 不存在，或這段對話還在執行時。
   */
  startRun(input: {
    sessionId?: string;
    text: string;
    agent?: string;
  }): SessionSummary {
    const text = input.text.trim();
    if (!text) throw new WebError(400, "訊息不可以是空的");
    const state = input.sessionId
      ? this.require(input.sessionId)
      : this.create(input.agent ?? this.project.defaultAgent);
    this.assertIdle(state);
    if (state.title === UNTITLED) state.title = toTitle(text);
    void this.execute(state, text);
    return this.summary(state);
  }

  /**
   * 不加新訊息，從對話紀錄的最後一則接著跑（上一輪出錯後重試）。
   *
   * @throws WebError 對話還在執行，或最後一則已經是 agent 的回覆時。
   */
  retry(id: string): SessionSummary {
    const state = this.require(id);
    this.assertIdle(state);
    const last = lastMessage(state.session.messages);
    if (last?.role !== "user" && last?.role !== "tool") {
      throw new WebError(400, "這段對話沒有需要重試的內容");
    }
    void this.execute(state, null);
    return this.summary(state);
  }

  /** 停止執行；等待中的確認一律視為拒絕。 */
  stop(id: string): void {
    this.require(id).controller?.abort();
  }

  /** 設定這段對話自己的權限模式；null 表示跟著專案設定。 */
  setMode(id: string, mode: PermissionMode | null): void {
    if (mode !== null && !PERMISSION_MODES.includes(mode)) {
      throw new WebError(400, `沒有 ${mode} 這個權限模式`);
    }
    this.require(id).mode = mode;
  }

  /** @throws WebError 找不到這個等待中的確認時。 */
  resolveApproval(
    sessionId: string,
    approvalId: string,
    decision: ApprovalDecision,
  ): void {
    const state = this.require(sessionId);
    const pending = state.approvals.get(approvalId);
    if (!pending) throw new WebError(404, "這個確認已經處理過，或已經失效");
    if (decision === "always") this.allowAlways(pending.approval.tool);
    this.emit({ type: "approval_resolved", sessionId, approvalId, decision });
    pending.finish(decision !== "deny");
  }

  /** 更新權限設定並寫回 harness.yaml，下一次工具呼叫就生效。 */
  updatePermissions(input: PermissionSettings): void {
    if (!PERMISSION_MODES.includes(input.mode)) {
      throw new WebError(400, `沒有 ${input.mode} 這個權限模式`);
    }
    const next: PermissionSettings = {
      mode: input.mode,
      allow: cleanPatterns(input.allow),
      ask: cleanPatterns(input.ask),
      deny: cleanPatterns(input.deny),
    };
    savePermissions(this.project.root, next);
    Object.assign(this.project.permissions, next);
    this.emit({ type: "project" });
  }

  /** 更新 harness.yaml 的 model 與 maxTurns，下一輪開始生效。 */
  updateModelSettings(input: ModelSettings): void {
    const model = input.model?.trim() || null;
    const maxTurns = input.maxTurns;
    if (maxTurns !== null && !(Number.isInteger(maxTurns) && maxTurns > 0)) {
      throw new WebError(400, "每次最多回合必須是正整數");
    }
    this.settings = { model, maxTurns };
    saveModelSettings(this.project.root, this.settings);
    this.emit({ type: "project" });
  }

  /** 用預設 agent 的模型送一個很短的請求，確認端點連得上。 */
  async testModel(): Promise<ModelTestResult> {
    const agent = this.agentFor(this.project.defaultAgent);
    let model: string;
    try {
      model = agent.model ?? defaultModel();
    } catch (error) {
      return { ok: false, message: describeError(error) };
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), MODEL_TEST_TIMEOUT);
    try {
      const request = {
        model,
        messages: [{ role: "user" as const, content: "ping" }],
        tools: [],
        params: agent.modelParams,
        signal: controller.signal,
      };
      for await (const _ of this.model(request)) break;
      return { ok: true, model };
    } catch (error) {
      const message = controller.signal.aborted
        ? `${MODEL_TEST_TIMEOUT / 1000} 秒內沒有回應`
        : describeError(error);
      return { ok: false, model, message };
    } finally {
      clearTimeout(timer);
      controller.abort();
    }
  }

  /** 停止所有正在執行的對話。 */
  close(): void {
    for (const state of this.states.values()) state.controller?.abort();
  }

  private async execute(state: SessionState, text: string | null) {
    const controller = new AbortController();
    const { signal } = controller;
    const sessionId = state.session.id;
    Object.assign(state, {
      controller,
      status: "running",
      activity: { kind: "thinking" },
      startedAt: Date.now(),
      endedAt: undefined,
      error: undefined,
      toolErrors: 0,
      stopReason: undefined,
      partial: "",
    });
    this.emit({ type: "run_start", sessionId, text });
    this.touch(state);
    try {
      const events = runStream(this.agentFor(state.agent), text, {
        session: state.session,
        permissions: this.permissionsFor(state),
        workdir: this.project.root,
        model: this.model,
        signal,
      });
      for await (const event of events) {
        if (event.type === "text_delta") {
          state.partial += event.text;
          this.emit({ type: "text_delta", sessionId, text: event.text });
          this.setActivity(state, { kind: "writing" });
        } else if (event.type === "message") {
          state.partial = "";
          this.emit({ type: "message", sessionId, message: event.message });
        } else if (event.type === "tool_call") {
          this.setActivity(state, { kind: "tool", tool: event.call.name });
        } else if (event.type === "tool_result") {
          if (event.result.isError) state.toolErrors += 1;
          // 主迴圈先把結果存進紀錄才產生事件，所以最後一則就是它。
          const message = state.session.messages.at(-1);
          if (message) {
            this.emit({
              type: "tool_result",
              sessionId,
              result: event.result,
              message,
            });
          }
          this.setActivity(state, { kind: "thinking" });
        } else if (event.type === "run_end") {
          state.status = "done";
          state.stopReason = event.result.stopReason;
          this.emit({
            type: "run_end",
            sessionId,
            stopReason: event.result.stopReason,
          });
        }
      }
    } catch (error) {
      if (signal.aborted) {
        state.status = "stopped";
        this.emit({ type: "run_stopped", sessionId });
      } else {
        state.status = "error";
        state.error = describeError(error);
        this.emit({ type: "run_error", sessionId, message: state.error });
      }
    } finally {
      state.controller = undefined;
      for (const pending of state.approvals.values()) pending.finish(false);
      state.activity = undefined;
      state.partial = "";
      state.endedAt = Date.now();
      this.touch(state);
    }
  }

  /**
   * 這段對話使用的權限設定：規則永遠讀專案目前的值，所以在設定頁或
   * 「以後都允許」改過的規則，執行中的對話也會立刻套用。
   */
  private permissionsFor(state: SessionState): Permissions {
    const shared = this.project.permissions;
    return {
      get mode() {
        return state.mode ?? shared.mode;
      },
      get allow() {
        return shared.allow;
      },
      get deny() {
        return shared.deny;
      },
      get ask() {
        return shared.ask;
      },
      approver: this.approver(state),
    };
  }

  private approver(state: SessionState): Approver {
    return (call, target) =>
      new Promise<boolean>((resolve) => {
        const signal = state.controller?.signal;
        const approval: Approval = {
          id: randomUUID(),
          sessionId: state.session.id,
          callId: call.id,
          tool: target.name,
          arguments: call.arguments,
          preview: this.preview(call, state),
        };
        const onAbort = () => finish(false);
        const finish = (allowed: boolean) => {
          if (!state.approvals.delete(approval.id)) return;
          signal?.removeEventListener("abort", onAbort);
          if (state.controller) {
            state.status = "running";
            this.setActivity(state, { kind: "tool", tool: target.name }, true);
          }
          resolve(allowed);
        };
        if (signal?.aborted) return resolve(false);
        signal?.addEventListener("abort", onAbort, { once: true });
        state.approvals.set(approval.id, { approval, finish });
        state.status = "waiting";
        state.activity = { kind: "waiting", tool: target.name };
        this.emit({ type: "approval", sessionId: state.session.id, approval });
        this.touch(state);
      });
  }

  /** 寫檔類的呼叫（參數有 path 與 content）附上檔案原本的內容。 */
  private preview(
    call: ToolCall,
    state: SessionState,
  ): FilePreview | undefined {
    const { path, content } = call.arguments;
    if (typeof path !== "string" || typeof content !== "string") return;
    const context: RunContext = {
      agentName: state.agent,
      workdir: this.project.root,
      sessionId: state.session.id,
      deps: undefined,
    };
    try {
      const target = resolvePath(context, path);
      const before = existsSync(target)
        ? readFileSync(target, "utf8").slice(0, PREVIEW_LIMIT)
        : null;
      return { path, before, after: content };
    } catch {
      return undefined;
    }
  }

  private allowAlways(toolName: string): void {
    const current = this.permissionSettings();
    if (current.allow.includes(toolName)) return;
    this.updatePermissions({ ...current, allow: [...current.allow, toolName] });
  }

  private permissionSettings(): PermissionSettings {
    const { mode, allow, ask, deny } = this.project.permissions;
    return { mode, allow: [...allow], ask: [...ask], deny: [...deny] };
  }

  /** 套用網頁上改過的 harness.yaml 設定；agent 檔案自己寫的值優先。 */
  private agentFor(name: string): Agent {
    const base =
      this.project.agents[name] ??
      (this.project.agents[this.project.defaultAgent] as Agent);
    const own = this.overrides[base.name] ?? {};
    return {
      ...base,
      model: own.model ?? this.settings.model ?? undefined,
      maxTurns: own.maxTurns ?? this.settings.maxTurns ?? DEFAULT_MAX_TURNS,
    };
  }

  private agentInfo(name: string): AgentInfo {
    const agent = this.agentFor(name);
    const own = this.overrides[name] ?? {};
    const envModel = process.env.HARNESS_MODEL;
    let modelSource: AgentInfo["modelSource"] = "default";
    if (own.model) modelSource = "agent";
    else if (this.settings.model) modelSource = "config";
    else if (envModel) modelSource = "env";
    let maxTurnsSource: AgentInfo["maxTurnsSource"] = "default";
    if (own.maxTurns) maxTurnsSource = "agent";
    else if (this.settings.maxTurns) maxTurnsSource = "config";
    return {
      name,
      description: agent.description,
      tools: agent.tools.map((item: Tool) => item.name),
      model: agent.model ?? envModel ?? null,
      modelSource,
      maxTurns: agent.maxTurns,
      maxTurnsSource,
    };
  }

  private create(agent: string): SessionState {
    if (!this.project.agents[agent]) {
      throw new WebError(400, `沒有 ${agent} 這個 agent`);
    }
    const session = createSession(this.project.sessionsDir);
    mkdirSync(this.project.sessionsDir, { recursive: true });
    writeFileSync(
      metaPath(this.project.sessionsDir, session.id),
      JSON.stringify({ agent }),
      "utf8",
    );
    return this.track(session, agent, Date.now());
  }

  private find(id: string): SessionState | undefined {
    const known = this.states.get(id);
    if (known) return known;
    const dir = this.project.sessionsDir;
    if (!/^[\w-]+$/.test(id) || !existsSync(join(dir, `${id}.jsonl`))) return;
    const session = loadSession(dir, id);
    const updatedAt = statSync(join(dir, `${id}.jsonl`)).mtimeMs;
    return this.track(session, readMetaAgent(dir, id) ?? "", updatedAt);
  }

  private require(id: string): SessionState {
    const state = this.find(id);
    if (!state) throw new WebError(404, `找不到對話：${id}`);
    return state;
  }

  private track(session: Session, agent: string, updatedAt: number) {
    const firstUser = session.messages.find((m) => m.role === "user");
    const state: SessionState = {
      session,
      agent: this.project.agents[agent] ? agent : this.project.defaultAgent,
      title: firstUser?.content ? toTitle(firstUser.content) : UNTITLED,
      status: "idle",
      updatedAt,
      toolErrors: 0,
      partial: "",
      mode: null,
      approvals: new Map(),
    };
    this.states.set(session.id, state);
    return state;
  }

  private assertIdle(state: SessionState): void {
    if (state.controller) {
      throw new WebError(409, "這段對話還在執行，請等它結束或先停止");
    }
  }

  /** 狀態變了才通知，避免每個文字片段都送一次摘要。 */
  private setActivity(state: SessionState, activity: Activity, force = false) {
    const same =
      state.activity?.kind === activity.kind &&
      state.activity?.tool === activity.tool;
    state.activity = activity;
    if (!same || force) this.touch(state);
  }

  private touch(state: SessionState): void {
    state.updatedAt = Date.now();
    this.emit({ type: "session", summary: this.summary(state) });
  }

  private summary(state: SessionState): SessionSummary {
    return {
      id: state.session.id,
      title: state.title,
      agent: state.agent,
      status: state.status,
      activity: state.activity,
      startedAt: state.startedAt,
      endedAt: state.endedAt,
      updatedAt: state.updatedAt,
      error: state.error,
      toolErrors: state.toolErrors,
      stopReason: state.stopReason,
      approval: state.approvals.values().next().value?.approval,
    };
  }

  private emit(event: ServerEvent): void {
    for (const listener of this.listeners) listener(event);
  }
}

function toTitle(text: string): string {
  const line = text.trim().split("\n")[0] ?? "";
  return line.length > TITLE_LENGTH ? `${line.slice(0, TITLE_LENGTH)}…` : line;
}

function cleanPatterns(list: unknown): string[] {
  if (!Array.isArray(list)) return [];
  const items = list
    .filter((item): item is string => typeof item === "string")
    .map((item) => item.trim())
    .filter(Boolean);
  return [...new Set(items)];
}

function metaPath(directory: string, id: string): string {
  return join(directory, `${id}.meta.json`);
}

/** 讀取對話用的 agent 名稱（存在 session 旁邊的 .meta.json）。 */
function readMetaAgent(directory: string, id: string): string | undefined {
  try {
    const meta = JSON.parse(readFileSync(metaPath(directory, id), "utf8"));
    return typeof meta.agent === "string" ? meta.agent : undefined;
  } catch {
    return undefined;
  }
}

function describeError(error: unknown): string {
  if (!(error instanceof Error)) return String(error);
  return error.name === "Error"
    ? error.message
    : `${error.name}: ${error.message}`;
}
