/**
 * 把對話紀錄（OpenAI 格式的訊息）整理成畫面上的區塊。
 *
 * 一輪 = 一則使用者訊息 + agent 接著做的所有事。agent 的部分依序是
 * 文字段落與「步驟群組」（連續的工具呼叫收在一起，可以展開收合）。
 */

import {
  type Approval,
  ATTACHMENT_MARKER,
  type FilePreview,
  type Message,
  type SessionDetail,
  type SubagentStep,
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
  /** subagent 呼叫的工具：是哪個 subagent。 */
  agent?: string;
  /** 交給 subagent 的步驟：subagent 在裡面呼叫的工具。 */
  children?: Step[];
}

export type Block =
  | {
      kind: "text";
      key: string;
      text: string;
      streaming: boolean;
      /** 這段文字是 messages 裡第幾則（評價回覆時用）；還在產生的文字沒有。 */
      messageIndex?: number;
    }
  | { kind: "notice"; key: string; text: string }
  | { kind: "steps"; key: string; steps: Step[] };

export interface Turn {
  key: string;
  /** 使用者訊息；從紀錄中間開始（例如重試）時可能沒有。 */
  user?: string;
  /** 使用者訊息附上的檔案路徑。 */
  attachments: string[];
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
const COMPACTED = "對話已壓縮：較早的內容濃縮成摘要，agent 之後看到的是摘要。";
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
      turn = { key: "t0", blocks: [], files: [], attachments: [] };
      turns.push(turn);
    }
    return turn;
  };

  detail.messages.forEach((message, index) => {
    // Python 版會在紀錄裡寫入自己的紀錄（壓縮、修剪），role 是 harness。
    const role: string = message.role;
    if (role === "harness") {
      if ((message as { type?: string }).type === "compaction") {
        current().blocks.push({
          kind: "notice",
          key: `c${index}`,
          text: COMPACTED,
        });
      }
      return;
    }
    if (message.role === "user") {
      const { text, attachments } = splitAttachments(message.content ?? "");
      turn = {
        key: `t${index}`,
        user: text,
        attachments,
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
          messageIndex: index,
        });
      }
      for (const call of message.tool_calls ?? []) {
        const step = toStep(call, approvals.get(call.id), running);
        const children = detail.subagentSteps?.[call.id];
        if (children?.length) {
          step.children = children.map((child) =>
            toChildStep(child, approvals, running),
          );
        }
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

/**
 * 把使用者訊息拆成文字與附件。伺服器把附件接在訊息最後，每個一行，
 * 以 ATTACHMENT_MARKER 開頭，路徑之後可能還有給模型的說明。
 */
export function splitAttachments(content: string): {
  text: string;
  attachments: string[];
} {
  const lines = content.split("\n");
  const attachments: string[] = [];
  while (lines.at(-1)?.startsWith(ATTACHMENT_MARKER)) {
    const line = lines.pop() ?? "";
    const path = line.slice(ATTACHMENT_MARKER.length).split(/[\s（(]/)[0];
    if (path) attachments.unshift(path);
  }
  return { text: lines.join("\n").trimEnd(), attachments };
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

/** 使用者附上的檔案，同一個路徑只留一次，新的在前面。 */
export function collectAttachments(turns: Turn[]): string[] {
  const seen = new Set<string>();
  for (const turn of [...turns].reverse()) {
    for (const path of turn.attachments) seen.add(path);
  }
  return [...seen];
}

/** 一輪裡 agent 的回覆：所有文字段落接起來，以及最後一則的索引。 */
export function replyOf(turn: Turn): { text: string; index?: number } {
  const texts = turn.blocks.filter(
    (block): block is Extract<Block, { kind: "text" }> =>
      block.kind === "text" && !block.streaming,
  );
  return {
    text: texts.map((block) => block.text.trim()).join("\n\n"),
    index: texts.at(-1)?.messageIndex,
  };
}

/**
 * 一組步驟目前的整體狀態，用在群組標題。
 *
 * active 是正在進行的步驟；交給 subagent 的步驟還在跑時，取 subagent
 * 裡面正在進行的那一個，標題才看得到它實際在做什麼。
 */
export function summarizeSteps(steps: Step[]): {
  active?: Step;
  errors: number;
} {
  const active = findActive(steps);
  const errors = steps.filter((step) => step.status === "error").length;
  return { active, errors };
}

function findActive(steps: Step[] | undefined): Step | undefined {
  const active = steps?.find(
    (step) => step.status === "running" || step.status === "waiting",
  );
  if (!active) return undefined;
  return findActive(active.children) ?? active;
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

/**
 * 步驟列上的結果預覽：縮成一行，太長就截斷。
 *
 * 工具常回傳排版過的 JSON，第一行只有 `{` 或 `[`，所以 JSON 先壓成
 * 一行；其他文字取第一個非空白的行。
 */
export function resultPreview(text: string | undefined, limit = 80): string {
  const trimmed = (text ?? "").trim();
  const line = compactJson(trimmed) ?? firstNonEmptyLine(trimmed);
  return line.length > limit ? `${line.slice(0, limit)}…` : line;
}

/** 是 JSON 物件或陣列時回傳單行的寫法，否則回傳 undefined。 */
function compactJson(text: string): string | undefined {
  if (!text.startsWith("{") && !text.startsWith("[")) return undefined;
  try {
    return JSON.stringify(JSON.parse(text));
  } catch {
    return undefined;
  }
}

function firstNonEmptyLine(text: string): string {
  return (
    text
      .split("\n")
      .map((line) => line.trim())
      .find((line) => line !== "") ?? ""
  );
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

function toChildStep(
  child: SubagentStep,
  approvals: Map<string, Approval>,
  running: boolean,
): Step {
  let status: StepStatus = child.status;
  const approval = approvals.get(child.callId);
  const waiting = approval?.tool === child.name;
  if (child.status === "running") {
    if (!running) status = "interrupted";
    else if (waiting) status = "waiting";
  }
  return {
    id: child.id,
    name: child.name,
    args: child.arguments,
    rawArgs: JSON.stringify(child.arguments),
    result: child.result,
    status,
    approval: status === "waiting" ? approval : undefined,
    agent: child.agent,
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
