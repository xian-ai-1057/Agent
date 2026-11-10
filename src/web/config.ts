/**
 * 網頁版的設定讀寫：直接改 harness.yaml，保留檔案裡原本的註解與排版。
 *
 * 只處理網頁上能改的欄位（model、maxTurns、permissions）。端點網址與
 * 金鑰來自環境變數，網頁只顯示狀態、不寫入。
 */

import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, join } from "node:path";
import { isMap, parseDocument } from "yaml";
import { CONFIG_FILE, parseFrontmatter } from "../project.ts";
import type { PermissionSettings } from "./protocol.ts";

/** harness.yaml 裡跟模型有關、可以從網頁修改的欄位。 */
export interface ModelSettings {
  /** null 表示刪掉這個欄位，改用環境變數 HARNESS_MODEL。 */
  model: string | null;
  /** null 表示刪掉這個欄位，使用預設值。 */
  maxTurns: number | null;
}

/** agent 檔案自己寫了哪些設定（會蓋過 harness.yaml）。 */
export interface AgentOverrides {
  model?: string;
  maxTurns?: number;
}

/** 讀取 harness.yaml 裡跟模型有關的欄位。 */
export function readModelSettings(root: string): ModelSettings {
  const config = (parseDocument(read(root)).toJS() ?? {}) as Record<
    string,
    unknown
  >;
  return {
    model: typeof config.model === "string" ? config.model : null,
    maxTurns: typeof config.maxTurns === "number" ? config.maxTurns : null,
  };
}

/** 把模型設定寫回 harness.yaml。 */
export function saveModelSettings(root: string, settings: ModelSettings): void {
  update(root, (doc) => {
    setOrDelete(doc, ["model"], settings.model || null);
    setOrDelete(doc, ["maxTurns"], settings.maxTurns);
  });
}

/** 把權限設定寫回 harness.yaml；空的規則清單會整個拿掉。 */
export function savePermissions(
  root: string,
  settings: PermissionSettings,
): void {
  update(root, (doc) => {
    if (!isMap(doc.get("permissions"))) doc.set("permissions", {});
    doc.setIn(["permissions", "mode"], settings.mode);
    for (const key of ["allow", "ask", "deny"] as const) {
      const list = settings[key];
      if (list.length === 0) {
        doc.deleteIn(["permissions", key]);
        continue;
      }
      const node = doc.createNode(list, { flow: true });
      doc.setIn(["permissions", key], node);
    }
  });
}

/**
 * 讀取每個 agent 檔案自己寫的 model 與 maxTurns。
 *
 * 載入後的 Agent 已經把 harness.yaml 的值合併進去，分不出來源，所以
 * 要回頭看 agent 檔案，才知道改 harness.yaml 會影響哪些 agent。
 */
export function readAgentOverrides(
  root: string,
): Record<string, AgentOverrides> {
  const directory = join(root, "agents");
  if (!existsSync(directory)) return {};
  const overrides: Record<string, AgentOverrides> = {};
  for (const file of readdirSync(directory)) {
    if (!file.endsWith(".md")) continue;
    const { meta } = parseFrontmatter(
      readFileSync(join(directory, file), "utf8"),
    );
    const name = (meta.name as string) ?? basename(file, ".md");
    const found: AgentOverrides = {};
    if (typeof meta.model === "string") found.model = meta.model;
    if (typeof meta.maxTurns === "number") found.maxTurns = meta.maxTurns;
    overrides[name] = found;
  }
  return overrides;
}

function read(root: string): string {
  return readFileSync(join(root, CONFIG_FILE), "utf8");
}

type Document = ReturnType<typeof parseDocument>;

function update(root: string, change: (doc: Document) => void): void {
  const doc = parseDocument(read(root));
  if (doc.errors.length > 0) {
    throw new Error(`${CONFIG_FILE} 格式有誤：${doc.errors[0]?.message}`);
  }
  if (!isMap(doc.contents)) {
    throw new Error(`${CONFIG_FILE} 的最上層必須是設定表（key: value）`);
  }
  change(doc);
  writeFileSync(join(root, CONFIG_FILE), String(doc), "utf8");
}

function setOrDelete(doc: Document, path: string[], value: unknown): void {
  if (value === null || value === undefined) doc.deleteIn(path);
  else doc.setIn(path, value);
}
