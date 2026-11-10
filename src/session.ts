/**
 * Session：一段對話的紀錄，可以存成 JSONL 檔並在之後接續。
 *
 * 紀錄只會往後附加、不會改寫，每一行是一則訊息。system prompt 不存進
 * 紀錄，每次執行時由 agent 重新組合。
 */

import { randomUUID } from "node:crypto";
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import type { Message } from "./types.ts";

/** 一段對話。沒有 path 時只存在記憶體中。 */
export class Session {
  readonly id: string;
  readonly path?: string;
  readonly messages: Message[];

  constructor(
    options: { id?: string; path?: string; messages?: Message[] } = {},
  ) {
    this.id = options.id ?? newSessionId();
    this.path = options.path;
    this.messages = options.messages ?? [];
  }

  /** 加入一則訊息，有檔案路徑時同步寫入。 */
  append(message: Message): void {
    this.messages.push(message);
    if (!this.path) return;
    mkdirSync(dirname(this.path), { recursive: true });
    appendFileSync(this.path, `${JSON.stringify(message)}\n`, "utf8");
  }
}

/** 產生可依時間排序的識別碼，形如 20260101-120000-ab12cd。 */
export function newSessionId(): string {
  const now = new Date();
  const pad = (value: number) => String(value).padStart(2, "0");
  const date = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`;
  const time = `${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  return `${date}-${time}-${randomUUID().slice(0, 6)}`;
}

/** 建立一個會寫入檔案的新 session。 */
export function createSession(directory: string): Session {
  const id = newSessionId();
  return new Session({ id, path: join(directory, `${id}.jsonl`) });
}

/**
 * 讀取既有的 session。
 *
 * @throws 找不到這個 session 時。
 */
export function loadSession(directory: string, id: string): Session {
  const path = join(directory, `${id}.jsonl`);
  if (!existsSync(path)) throw new Error(`找不到 session：${id}`);
  const messages = readFileSync(path, "utf8")
    .split("\n")
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line) as Message);
  return new Session({ id, path, messages });
}

/** 列出資料夾裡所有的 session 識別碼，由舊到新。 */
export function listSessionIds(directory: string): string[] {
  if (!existsSync(directory)) return [];
  return readdirSync(directory)
    .filter((file) => file.endsWith(".jsonl"))
    .map((file) => file.slice(0, -".jsonl".length))
    .sort();
}

/** 讀取最新的 session；沒有任何 session 時回傳 undefined。 */
export function latestSession(directory: string): Session | undefined {
  const id = listSessionIds(directory).at(-1);
  return id ? loadSession(directory, id) : undefined;
}
