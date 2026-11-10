/**
 * 把對話紀錄（OpenAI 格式的訊息）整理成畫面上的區塊。
 *
 * 一輪 = 一則使用者訊息 + agent 接著做的所有事。agent 的部分依序是
 * 文字段落與「步驟群組」（連續的工具呼叫收在一起，可以展開收合）。
 */

import type {
  Approval,
  FilePreview,
  Message,
  SessionDetail,
} from "../../src/web/protocol.ts";

export type StepStatus =
  | "running"
  | "waiting"
  | "done"
  | "error"
  | "interrupted";

export interface Step {
  id: string;
  name: string;
  args: Record<string, unknown>;
  /** 參數不是合法 JSON 時的原始字串。 */
  rawArgs: string;
  result?: string;
  status: StepStatus;
  approval?: Approval;
}

export type Block =
  | { kind: "text"; key: string; text: string; streaming: boolean }
  | { kind: "steps"; key: string; steps: Step[] };

export interface Turn {
  key: string;
  /** 使用者訊息；從紀錄中間開始（例如重試）時可能沒有。 */
  user?: string;
  blocks: Block[];
  files: FileChange[];
}

export type FileStatus =
  | "pending"
  | "writing"
  | "written"
  | "denied"
  | "failed";

/** 寫檔類工具（參數有 path 與 content）造成的檔案變更。 */
export interface FileChange {
  callId: string;
  path: string;
  before: string | null | undefined;
  after: string;
  status: FileStatus;
  approval?: Approval;
}

const ERROR_PREFIX = "[錯誤] ";
const DENIED_MARK = "使用者沒有同意";

export function buildTurns(
  detail: SessionDetail,
  previews: Record<string, FilePreview>,
): Turn[] {
  const running =
    detail.summary.status === "running" || detail.summary.status === "waiting";
  const approvals = new Map(detail.approvals.map((a) => [a.callId, a]));
  const steps = new Map<string, Step>();
  const turns: Turn[] = [];
  let turn: Turn | undefined;

  const current = (): Turn => {
    if (!turn) {
      turn = { key: "t0", blocks: [], files: [] };
      turns.push(turn);
    }
    return turn;
  };

  detail.messages.forEach((message, index) => {
    if (message.role === "user") {
      turn = {
        key: `t${index}`,
        user: message.content ?? "",
        blocks: [],
        files: [],
      };
      turns.push(turn);
      return;
    }
    if (message.role === "assistant") {
      const target = current();
      if (message.content?.trim()) {
        target.blocks.push({
          kind: "text",
          key: `m${index}`,
          text: message.content,
          streaming: false,
        });
      }
      for (const call of message.tool_calls ?? []) {
        const step = toStep(call, approvals.get(call.id), running);
        steps.set(step.id, step);
        const last = target.blocks.at(-1);
        if (last?.kind === "steps") last.steps.push(step);
        else {
          target.blocks.push({
            kind: "steps",
            key: `s${index}`,
            steps: [step],
          });
        }
      }
      return;
    }
    if (message.role === "tool" && message.tool_call_id) {
      const step = steps.get(message.tool_call_id);
      if (!step) return;
      const content = message.content ?? "";
      const failed = content.startsWith(ERROR_PREFIX);
      step.result = failed ? content.slice(ERROR_PREFIX.length) : content;
      step.status = failed ? "error" : "done";
      step.approval = undefined;
    }
  });

  if (detail.partial) {
    current().blocks.push({
      kind: "text",
      key: "partial",
      text: detail.partial,
      streaming: true,
    });
  }

  for (const item of turns) {
    for (const block of item.blocks) {
      if (block.kind !== "steps") continue;
      for (const step of block.steps) {
        const change = toFileChange(step, previews[step.id]);
        if (change) item.files.push(change);
      }
    }
  }
  return turns;
}

/** 一段對話裡所有的檔案變更，同一個路徑只留最新的一次。 */
export function collectFiles(turns: Turn[]): FileChange[] {
  const byPath = new Map<string, FileChange>();
  for (const turn of turns) {
    for (const change of turn.files) {
      byPath.delete(change.path);
      byPath.set(change.path, change);
    }
  }
  return [...byPath.values()].reverse();
}

/** 一組步驟目前的整體狀態，用在群組標題。 */
export function summarizeSteps(steps: Step[]): {
  active?: Step;
  errors: number;
} {
  const active = steps.find(
    (step) => step.status === "running" || step.status === "waiting",
  );
  const errors = steps.filter((step) => step.status === "error").length;
  return { active, errors };
}

/** 把參數縮成一行給人看。 */
export function formatArgs(step: Step, limit = 120): string {
  const entries = Object.entries(step.args);
  if (entries.length === 0) return step.rawArgs === "{}" ? "" : step.rawArgs;
  const text = entries
    .map(([key, value]) => `${key}=${short(value)}`)
    .join(", ");
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

/** 結果的第一行，太長就截斷。 */
export function firstLine(text: string | undefined, limit = 80): string {
  const line = (text ?? "").trim().split("\n")[0] ?? "";
  return line.length > limit ? `${line.slice(0, limit)}…` : line;
}

function short(value: unknown): string {
  if (typeof value === "string") {
    const line = value.split("\n")[0] ?? "";
    const more = value.includes("\n") || line.length > 40;
    return JSON.stringify(more ? `${line.slice(0, 40)}…` : line);
  }
  return JSON.stringify(value);
}

function toStep(
  call: NonNullable<Message["tool_calls"]>[number],
  approval: Approval | undefined,
  running: boolean,
): Step {
  const rawArgs = call.function.arguments || "{}";
  let args: Record<string, unknown> = {};
  try {
    const parsed = JSON.parse(rawArgs);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      args = parsed as Record<string, unknown>;
    }
  } catch {
    // 參數不是 JSON 時保留原文，主迴圈會回報錯誤。
  }
  let status: StepStatus = running ? "running" : "interrupted";
  if (approval) status = "waiting";
  return {
    id: call.id,
    name: call.function.name,
    args,
    rawArgs,
    status,
    approval,
  };
}

function toFileChange(
  step: Step,
  preview: FilePreview | undefined,
): FileChange | undefined {
  const { path, content } = step.args;
  if (typeof path !== "string" || typeof content !== "string") return;
  let status: FileStatus = "writing";
  if (step.status === "waiting") status = "pending";
  else if (step.status === "done") status = "written";
  else if (step.status === "error") {
    status = step.result?.includes(DENIED_MARK) ? "denied" : "failed";
  } else if (step.status === "interrupted") status = "failed";
  return {
    callId: step.id,
    path,
    before: preview ? preview.before : undefined,
    after: content,
    status,
    approval: step.approval,
  };
}
