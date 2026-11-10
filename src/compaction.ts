/**
 * 讀取對話壓縮紀錄，推算模型實際看到的歷史。
 *
 * 對話壓縮由 Python 版執行：較舊的歷史被濃縮成檢查點時，它不會改寫
 * 原本的訊息，而是在 JSONL 裡附加 role 為 "harness" 的紀錄（壓縮與
 * 工具結果修剪）。TypeScript 版用同樣的規則讀取這些紀錄，所以由 Python
 * 版壓縮過的對話也能在這裡接續；TypeScript 版本身不會主動壓縮。
 */

import type { Message } from "./types.ts";

/** 框架自己的紀錄（壓縮、修剪）使用的 role。 */
export const RECORD_ROLE = "harness";

const CHECKPOINT_PREAMBLE =
  "以下是系統自動產生的檢查點，濃縮了較早的對話以節省上下文。把其中的" +
  "內容當成已知的背景，直接延續後面的對話繼續工作，不要重述，也不要" +
  "提到這個檢查點。";

interface HarnessRecord {
  role: typeof RECORD_ROLE;
  type?: string;
  keep_from?: number;
  summary?: string;
  results?: Record<string, string>;
}

/** 是不是框架自己的紀錄，而不是對話訊息。 */
export function isRecord(message: Message): boolean {
  return (message.role as string) === RECORD_ROLE;
}

/**
 * 從對話紀錄推算要送給模型的歷史（不含 system prompt）。
 *
 * 規則與 Python 版的 compaction.model_history 相同：最後一次壓縮的
 * 摘要，加上它保留的訊息與之後的訊息；修剪過的工具結果換成修剪後的
 * 內容；找不到對應呼叫的工具結果拿掉。
 */
export function modelHistory(messages: Message[]): Message[] {
  let last: number | undefined;
  const replacements = new Map<string, string>();
  messages.forEach((message, index) => {
    if (!isRecord(message)) return;
    const record = message as unknown as HarnessRecord;
    if (record.type === "compaction") last = index;
    if (record.type === "prune") {
      for (const [id, text] of Object.entries(record.results ?? {})) {
        replacements.set(id, text);
      }
    }
  });

  const history: Message[] = [];
  let start = 0;
  if (last !== undefined) {
    const record = messages[last] as unknown as HarnessRecord;
    history.push(checkpointMessage(record.summary ?? ""));
    start = record.keep_from ?? last;
  }
  for (const message of messages.slice(start)) {
    if (isRecord(message)) continue;
    const replaced =
      message.role === "tool" && message.tool_call_id
        ? replacements.get(message.tool_call_id)
        : undefined;
    history.push(
      replaced === undefined ? message : { ...message, content: replaced },
    );
  }
  return dropOrphanResults(history);
}

/** 對話紀錄裡最後一則真正的訊息（略過框架的紀錄）。 */
export function lastMessage(messages: Message[]): Message | undefined {
  return messages.findLast((message) => !isRecord(message));
}

function checkpointMessage(summary: string): Message {
  return {
    role: "user",
    content: `${CHECKPOINT_PREAMBLE}\n\n<compacted-summary>\n${summary}\n</compacted-summary>`,
  };
}

/** 拿掉找不到對應呼叫的工具結果，模型端點不接受這種訊息。 */
function dropOrphanResults(history: Message[]): Message[] {
  const called = new Set(
    history.flatMap((message) => (message.tool_calls ?? []).map((c) => c.id)),
  );
  return history.filter(
    (message) =>
      message.role !== "tool" || called.has(message.tool_call_id ?? ""),
  );
}
