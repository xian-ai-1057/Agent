/**
 * 把對話紀錄（OpenAI 格式的訊息）整理成畫面上的區塊。
 *
 * 一輪 = 一則使用者訊息 + agent 接著做的所有事。agent 的部分依序是
 * 文字段落與「步驟群組」（連續的工具呼叫收在一起，可以展開收合）。
 *
 * 工具結果後面的 PRODUCED_MARKER 行（沙盒產生的檔案）會從結果拿掉，
 * 收進這一輪的 produced，畫面在回覆底下顯示成縮圖或檔案卡片。
 */

import {
  type Approval,
  ATTACHMENT_MARKER,
  DENIED_RESULT,
  type FilePreview,
  type Message,
  PRODUCED_MARKER,
  type SessionDetail,
  type SubagentStep,
} from "../../src/web/protocol.ts";

/** denied：使用者拒絕了這次呼叫（不算工具錯誤）。 */
export type StepStatus =
  | "running"
  | "waiting"
  | "done"
  | "error"
  | "denied"
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
  /**
   * 出錯的步驟之後已經自己修正（後面同一個工具成功了，或這一輪正常
   * 做完並且有回覆）。畫面預設不顯示它的錯誤內容。
   */
  recovered?: boolean;
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
  /** 沙盒在這一輪產生的檔案（相對於專案根目錄），依出現順序、不重複。 */
  produced: string[];
}

export type FileStatus =
  | "pending"
  | "writing"
  | "written"
  | "denied"
  | "failed";

/** 寫檔類工具（參數有 path 與 content，或是 edit_file）造成的檔案變更。 */
export interface FileChange {
  callId: string;
  path: string;
  before: string | null | undefined;
  after: string;
  status: FileStatus;
  approval?: Approval;
  /** edit_file 的修改（不是整個檔案重寫）。 */
  edited?: boolean;
  /**
   * 沒有確認時的檔案內容可以比較（例如重新讀取紀錄後），before 與 after
   * 只是 edit_file 參數裡被取代的那一段。
   */
  fragment?: boolean;
}

const ERROR_PREFIX = "[錯誤] ";
const COMPACTED = "對話已壓縮：較早的內容濃縮成摘要，agent 之後看到的是摘要。";

export function buildTurns(
  detail: SessionDetail,
  previews: Record<string, FilePreview>,
): Turn[] {
  const running =
    detail.summary.status === "running" || detail.summary.status === "waiting";
  const approvals = new Map(detail.approvals.map((a) => [a.callId, a]));
  const steps = new Map<string, Step>();
  /** 每個步驟屬於哪一輪，工具結果產生的檔案記在那一輪。 */
  const stepTurns = new Map<string, Turn>();
  const turns: Turn[] = [];
  let turn: Turn | undefined;

  const current = (): Turn => {
    if (!turn) {
      turn = {
        key: "t0",
        blocks: [],
        files: [],
        attachments: [],
        produced: [],
      };
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
        produced: [],
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
            toChildStep(child, approvals, running, target.produced),
          );
        }
        steps.set(step.id, step);
        stepTurns.set(step.id, target);
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
      const { text, produced } = splitProduced(message.content ?? "");
      const failed = text.startsWith(ERROR_PREFIX);
      step.result = failed ? text.slice(ERROR_PREFIX.length) : text;
      step.status = isDeniedResult(step.result)
        ? "denied"
        : failed
          ? "error"
          : "done";
      step.approval = undefined;
      addUnique(stepTurns.get(step.id) ?? current(), produced);
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
  // 之前的輪都已經結束；最後一輪要正常做完，回覆才算數。
  turns.forEach((item, index) => {
    markRecovered(
      item,
      index < turns.length - 1 || detail.summary.status === "done",
    );
  });
  return turns;
}

/**
 * 從工具結果拆出沙盒產生的檔案。伺服器把它們接在結果後面，每個一行，
 * 以 PRODUCED_MARKER 開頭，接著是相對於專案根目錄的路徑。
 */
export function splitProduced(content: string): {
  text: string;
  produced: string[];
} {
  if (!content.includes(PRODUCED_MARKER)) {
    return { text: content, produced: [] };
  }
  const kept: string[] = [];
  const produced: string[] = [];
  for (const line of content.split("\n")) {
    if (line.startsWith(PRODUCED_MARKER)) {
      const path = line.slice(PRODUCED_MARKER.length).trim();
      if (path && !produced.includes(path)) produced.push(path);
    } else kept.push(line);
  }
  return { text: kept.join("\n").trimEnd(), produced };
}

/** 工具結果是不是「使用者拒絕了這次呼叫」（可能帶著錯誤標記）。 */
export function isDeniedResult(text: string | undefined): boolean {
  if (text === undefined) return false;
  const trimmed = text.trim();
  const body = trimmed.startsWith(ERROR_PREFIX)
    ? trimmed.slice(ERROR_PREFIX.length).trim()
    : trimmed;
  return body === DENIED_RESULT;
}

/**
 * 標出已經自己修正的錯誤步驟：後面有同一個工具成功的步驟，或這一輪
 * 正常做完（finished）而且錯誤之後還有回覆。最後還沒修好的錯誤不算。
 * subagent 的步驟：後面同一個工具成功，或交給它的那一步成功了。
 */
export function markRecovered(turn: Turn, finished: boolean): void {
  const order: { step: Step; block: number }[] = [];
  turn.blocks.forEach((block, index) => {
    if (block.kind !== "steps") return;
    for (const step of block.steps) order.push({ step, block: index });
  });
  const lastReply = turn.blocks.findLastIndex(
    (block) => block.kind === "text" && !block.streaming,
  );
  order.forEach(({ step, block }, index) => {
    if (step.status === "error") {
      step.recovered =
        succeedsLater(
          step,
          order.slice(index + 1).map((item) => item.step),
        ) ||
        (finished && lastReply > block);
    }
    if (step.children) markChildren(step.children, step.status === "done");
  });
}

function markChildren(children: Step[], parentDone: boolean): void {
  children.forEach((child, index) => {
    if (child.status === "error") {
      child.recovered =
        parentDone || succeedsLater(child, children.slice(index + 1));
    }
    if (child.children) markChildren(child.children, child.status === "done");
  });
}

function succeedsLater(step: Step, later: Step[]): boolean {
  return later.some(
    (other) => other.name === step.name && other.status === "done",
  );
}

function addUnique(turn: Turn, paths: string[]): void {
  for (const path of paths) {
    if (!turn.produced.includes(path)) turn.produced.push(path);
  }
}

/** 沙盒產生的檔案，同一個路徑只留一次，新的在前面。 */
export function collectProduced(turns: Turn[]): string[] {
  const seen = new Set<string>();
  for (const turn of [...turns].reverse()) {
    for (const path of [...turn.produced].reverse()) seen.add(path);
  }
  return [...seen];
}

/**
 * 確認卡片與檔案面板要顯示的內容。伺服器沒有附上 preview 的 edit_file
 * （TypeScript 版）只有被取代的那一段可以比較。
 */
export function approvalPreview(approval: Approval): FilePreview | undefined {
  return approval.preview ?? editFragment(approval.tool, approval.arguments);
}

/** edit_file 參數裡被取代的那一段，當成修改前後的內容。 */
function editFragment(
  name: string,
  args: Record<string, unknown>,
): FilePreview | undefined {
  if (name !== "edit_file") return undefined;
  const { path, old_text: before, new_text: after } = args;
  if (
    typeof path !== "string" ||
    typeof before !== "string" ||
    typeof after !== "string"
  ) {
    return undefined;
  }
  return { path, before, after };
}

/** 附件那一行路徑後面給模型的說明（uploads.py 的 describe）。 */
const SANDBOX_NOTE = /（run_python 裡的路徑：[^\n]*）$/;

/**
 * 把使用者訊息拆成文字與附件。伺服器把附件接在訊息最後，每個一行，
 * 以 ATTACHMENT_MARKER 開頭，路徑之後可能還有給模型的說明。
 *
 * 檔名可以有括號（例如 report_(1).pdf），所以只去掉已知的說明，不在
 * 第一個括號或空白就切斷。
 */
export function splitAttachments(content: string): {
  text: string;
  attachments: string[];
} {
  const lines = content.split("\n");
  const attachments: string[] = [];
  while (lines.at(-1)?.startsWith(ATTACHMENT_MARKER)) {
    const line = lines.pop() ?? "";
    const path = line
      .slice(ATTACHMENT_MARKER.length)
      .replace(SANDBOX_NOTE, "")
      .trim();
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
  /** 還沒修正的錯誤。 */
  errors: number;
  /** 之後已經自己修正的錯誤。 */
  fixed: number;
} {
  const active = findActive(steps);
  const failed = steps.filter((step) => step.status === "error");
  const fixed = failed.filter((step) => step.recovered).length;
  return { active, errors: failed.length - fixed, fixed };
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
  /** 收集 subagent 用沙盒產生的檔案。 */
  produced: string[],
): Step {
  let status: StepStatus = child.status;
  const approval = approvals.get(child.callId);
  const waiting = approval?.tool === child.name;
  if (child.status === "running") {
    if (!running) status = "interrupted";
    else if (waiting) status = "waiting";
  }
  let result = child.result;
  if (result !== undefined) {
    const split = splitProduced(result);
    result = split.text;
    for (const path of split.produced) {
      if (!produced.includes(path)) produced.push(path);
    }
    if (child.status === "error" && isDeniedResult(result)) status = "denied";
  }
  return {
    id: child.id,
    name: child.name,
    args: child.arguments,
    rawArgs: JSON.stringify(child.arguments),
    result,
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
  if (typeof path !== "string") return;
  let status: FileStatus = "writing";
  if (step.status === "waiting") status = "pending";
  else if (step.status === "done") status = "written";
  else if (step.status === "denied") status = "denied";
  else if (step.status === "error" || step.status === "interrupted") {
    status = "failed";
  }
  const base = { callId: step.id, path, status, approval: step.approval };
  if (typeof content === "string") {
    return {
      ...base,
      before: preview ? preview.before : undefined,
      after: content,
    };
  }
  // edit_file：確認時看過的內容（整個檔案）優先，沒有時只有被取代的那段。
  const piece = editFragment(step.name, step.args);
  if (!piece) return;
  if (preview) {
    return {
      ...base,
      before: preview.before,
      after: preview.after,
      edited: true,
    };
  }
  return {
    ...base,
    before: piece.before,
    after: piece.after,
    edited: true,
    fragment: true,
  };
}
