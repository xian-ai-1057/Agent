/**
 * 內建的網頁工具（toolset 名稱：web）。
 *
 * 設計參考 Claude Code 的 WebFetch：讀取一個網址，把 HTML 轉成好讀的
 * 純文字（保留標題層級、清單與連結）交給 agent。送出請求會把網址（可能
 * 含有對話裡的資料）傳到外部，所以預設需要使用者同意；信任的話可以在
 * harness.yaml 的 permissions.allow 加上 web_fetch。
 */

import { z } from "zod";
import { type Tool, ToolError, tool } from "../tool.ts";

export const DEFAULT_MAX_CHARS = 20_000;
export const MAX_DOWNLOAD_BYTES = 2_000_000;
const TIMEOUT_MS = 30_000;
const USER_AGENT = "agent-harness";

const ENTITIES: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  nbsp: " ",
};

/** 解碼常見的 HTML 字元實體（&amp;、&#39;、&#x4e2d; 等）。 */
function decodeEntities(text: string): string {
  return text.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (whole, name) => {
    const key = String(name);
    if (key[0] === "#") {
      const hex = key[1] === "x" || key[1] === "X";
      const code = Number.parseInt(key.slice(hex ? 2 : 1), hex ? 16 : 10);
      return Number.isFinite(code) && code <= 0x10ffff
        ? String.fromCodePoint(code)
        : whole;
    }
    return ENTITIES[key.toLowerCase()] ?? whole;
  });
}

function stripTags(html: string): string {
  return html.replace(/<[^>]*>/g, "");
}

/**
 * 把 HTML 轉成純文字：拿掉 script / style 等不顯示的內容，標題轉成
 * `#`，清單項目轉成 `- `，連結轉成 `[文字](網址)`。
 *
 * @param html 網頁原始碼。
 * @param base 網頁網址，用來把相對連結轉成完整網址。
 */
export function htmlToText(html: string, base?: string): string {
  let text = html
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(
      /<(script|style|noscript|template|svg|head|title)\b[\s\S]*?<\/\1\s*>/gi,
      "",
    )
    .replace(
      /<a\b[^>]*?\bhref\s*=\s*["']([^"']*)["'][^>]*>([\s\S]*?)<\/a\s*>/gi,
      (_, href: string, inner: string) => {
        const label = stripTags(inner).trim();
        const target = absoluteUrl(decodeEntities(href), base);
        return label && target ? `[${label}](${target})` : inner;
      },
    )
    .replace(
      /<h([1-6])\b[^>]*>/gi,
      (_, level: string) => `\n\n${"#".repeat(Number(level))} `,
    )
    .replace(/<li\b[^>]*>/gi, "\n- ")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(
      /<\/?(p|div|section|article|header|footer|main|nav|aside|h[1-6]|ul|ol|li|tr|table|blockquote|pre|hr|dl|dt|dd|figure|figcaption|form)\b[^>]*>/gi,
      "\n",
    );
  text = decodeEntities(stripTags(text));
  const lines = text
    .split("\n")
    .map((line) => line.replace(/[ \t\f\v ]+/g, " ").trim());
  return lines
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function absoluteUrl(href: string, base?: string): string | undefined {
  try {
    const url = new URL(href, base);
    return url.protocol === "http:" || url.protocol === "https:"
      ? url.href
      : undefined;
  } catch {
    return undefined;
  }
}

/** 取出 HTML 的 <title>。 */
export function htmlTitle(html: string): string {
  const match = /<title\b[^>]*>([\s\S]*?)<\/title\s*>/i.exec(html);
  return match ? decodeEntities(stripTags(match[1] ?? "")).trim() : "";
}

/** 判斷內容類型是不是可以當成文字讀的。 */
export function isTextType(type: string): boolean {
  return (
    type === "" ||
    type.startsWith("text/") ||
    /[+/](json|xml|javascript|x-yaml|yaml)$/.test(type)
  );
}

async function readLimited(response: Response): Promise<Uint8Array> {
  const reader = response.body?.getReader();
  if (!reader) return new Uint8Array();
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (size < MAX_DOWNLOAD_BYTES) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    size += value.length;
  }
  await reader.cancel().catch(() => undefined);
  return Buffer.concat(chunks).subarray(0, MAX_DOWNLOAD_BYTES);
}

function decode(bytes: Uint8Array, contentType: string): string {
  const charset = /charset=["']?([\w-]+)/i.exec(contentType)?.[1] ?? "utf-8";
  try {
    return new TextDecoder(charset).decode(bytes);
  } catch {
    return new TextDecoder("utf-8").decode(bytes);
  }
}

export const webFetchTool = tool({
  name: "web_fetch",
  description:
    "讀取一個網址的內容。HTML 會轉成純文字（保留標題、清單與連結），JSON " +
    "與純文字原樣回傳。用來查文件、讀使用者給的連結；不支援需要登入的頁面。",
  parameters: z.object({
    url: z.string().describe("完整網址，必須以 http:// 或 https:// 開頭"),
    max_chars: z
      .number()
      .int()
      .min(100)
      .max(100_000)
      .default(DEFAULT_MAX_CHARS)
      .describe("最多回傳幾個字元，內容更長時會截斷"),
  }),
  needsApproval: true,
  execute: async ({ url, max_chars }, context) => {
    let target: URL;
    try {
      target = new URL(url);
    } catch {
      throw new ToolError(`網址格式不正確：${url}`);
    }
    if (target.protocol !== "http:" && target.protocol !== "https:") {
      throw new ToolError(`只支援 http 與 https 網址：${url}`);
    }
    const timeout = AbortSignal.timeout(TIMEOUT_MS);
    const signal = context.signal
      ? AbortSignal.any([context.signal, timeout])
      : timeout;
    let response: Response;
    try {
      response = await fetch(target, {
        signal,
        redirect: "follow",
        headers: {
          "user-agent": USER_AGENT,
          accept: "text/html,text/plain,application/json;q=0.9,*/*;q=0.5",
        },
      });
    } catch (error) {
      const reason = timeout.aborted
        ? `超過 ${TIMEOUT_MS / 1000} 秒沒有回應`
        : (error as Error).message;
      throw new ToolError(`無法讀取 ${url}：${reason}`);
    }
    if (!response.ok) {
      await response.body?.cancel().catch(() => undefined);
      throw new ToolError(
        `伺服器回應 ${response.status} ${response.statusText}`.trim(),
      );
    }
    const contentType = response.headers.get("content-type") ?? "";
    const type = (contentType.split(";")[0] ?? "").trim().toLowerCase();
    if (!isTextType(type)) {
      await response.body?.cancel().catch(() => undefined);
      throw new ToolError(`不支援的內容類型：${type}（只能讀取文字內容）`);
    }
    const raw = decode(await readLimited(response), contentType);
    const html = type.includes("html");
    const body = html ? htmlToText(raw, response.url || url) : raw.trim();
    const header = [`網址：${response.url || url}`];
    const title = html ? htmlTitle(raw) : "";
    if (title) header.push(`標題：${title}`);
    let content = body || "（沒有文字內容）";
    if (content.length > max_chars) {
      const omitted = content.length - max_chars;
      content = `${content.slice(0, max_chars)}\n[內容過長，後面還有 ${omitted} 個字元沒有顯示]`;
    }
    return `${header.join("\n")}\n\n${content}`;
  },
});

export const webTools: Tool[] = [webFetchTool];
