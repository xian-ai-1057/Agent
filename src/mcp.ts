/**
 * 連接 MCP 工具伺服器，把它提供的工具轉成框架的 Tool。
 *
 * 這是讓 agent 使用其他語言（例如 Python）既有功能的方式：那個專案用
 * MCP SDK 把函式公開成工具，這裡負責啟動它、列出工具、轉送呼叫。
 */

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import type { Transport } from "@modelcontextprotocol/sdk/shared/transport.js";
import { type JsonSchema, type Tool, ToolError } from "./tool.ts";

/** 一個 MCP 伺服器的設定：command（本機程式）與 url（遠端）擇一。 */
export interface McpServerConfig {
  /** 要啟動的程式，例如 python3。 */
  command?: string;
  args?: string[];
  /** 額外的環境變數，會加在目前的環境變數之上。 */
  env?: Record<string, string>;
  /** 遠端伺服器的網址（Streamable HTTP）。 */
  url?: string;
  /** 連遠端伺服器時附帶的 HTTP 標頭。 */
  headers?: Record<string, string>;
  /** 單次工具呼叫最多等幾秒，預設 300。 */
  timeoutSeconds?: number;
}

/** 一條已建立的 MCP 連線。 */
export interface McpConnection {
  name: string;
  tools: Tool[];
  close(): Promise<void>;
}

const DEFAULT_TIMEOUT_SECONDS = 300;
const STDERR_TAIL_CHARS = 2000;
// 設定裡的 ${NAME} 或 ${NAME:-預設值} 換成環境變數，金鑰就不必寫進
// harness.yaml。
const ENV_REFERENCE = /\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}/g;

/**
 * 把字串裡的 ${NAME}、${NAME:-預設值} 換成環境變數的值。
 *
 * @param text 設定裡的字串。
 * @param where 錯誤訊息裡用來指出是哪一項設定。
 * @throws 用到的環境變數沒有設定（或是空的），也沒有寫預設值時。
 */
export function expandEnv(text: string, where = "設定"): string {
  return text.replace(
    ENV_REFERENCE,
    (_match, variable: string, fallback: string | undefined) => {
      const value = process.env[variable];
      if (value) return value;
      if (fallback !== undefined) return fallback;
      throw new Error(
        `${where} 用到環境變數 ${variable}，但它沒有設定；請在 .env 或啟動前的環境變數設定它`,
      );
    },
  );
}

/**
 * 把 harness.yaml 裡一個伺服器設定的 args、env、url、headers 中的
 * ${NAME} 換成環境變數的值。
 */
export function expandServerConfig(
  name: string,
  config: McpServerConfig,
): McpServerConfig {
  const where = `mcpServers.${name}`;
  const expand = (value: unknown) => expandEnv(String(value), where);
  const expandValues = (record?: Record<string, string>) =>
    record &&
    Object.fromEntries(
      Object.entries(record).map(([key, value]) => [key, expand(value)]),
    );
  return {
    ...config,
    args: config.args?.map(expand),
    env: expandValues(config.env),
    url: config.url ? expand(config.url) : config.url,
    headers: expandValues(config.headers),
  };
}

/**
 * 連上一個 MCP 伺服器並取得它的工具。
 *
 * @param name 伺服器名稱，也是這組工具的 toolset 名稱。
 * @param config 伺服器設定。
 * @param cwd 啟動本機伺服器時的工作目錄。
 * @throws 設定有誤，或伺服器無法啟動時。
 */
export async function connectMcpServer(
  name: string,
  config: McpServerConfig,
  cwd: string,
): Promise<McpConnection> {
  const stderr = { tail: "" };
  const transport = createTransport(name, config, cwd, stderr);
  const client = new Client({ name: "agent-harness", version: "0.2.0" });
  try {
    await client.connect(transport);
    const listed = await client.listTools();
    const timeout = (config.timeoutSeconds ?? DEFAULT_TIMEOUT_SECONDS) * 1000;
    return {
      name,
      tools: listed.tools.map((item) => wrapTool(client, item, timeout)),
      close: () => client.close(),
    };
  } catch (error) {
    await client.close().catch(() => {});
    const detail = stderr.tail.trim() || (error as Error).message;
    throw new Error(`無法啟動 MCP 伺服器 ${name}：\n${detail}`);
  }
}

function createTransport(
  name: string,
  config: McpServerConfig,
  cwd: string,
  stderr: { tail: string },
): Transport {
  if (config.url) {
    return new StreamableHTTPClientTransport(new URL(config.url), {
      requestInit: { headers: config.headers },
    });
  }
  if (!config.command) {
    throw new Error(`MCP 伺服器 ${name} 必須設定 command 或 url`);
  }
  const transport = new StdioClientTransport({
    command: config.command,
    args: config.args ?? [],
    // 伺服器是專案自己的程式，需要完整的環境變數（虛擬環境、資料庫
    // 連線設定…），所以不使用 SDK 預設的精簡環境。
    env: { ...definedEnv(), ...config.env },
    cwd,
    stderr: "pipe",
  });
  // 伺服器的 log 不直接印到聊天畫面，只保留最後一段供啟動失敗時顯示。
  transport.stderr?.on("data", (chunk: Buffer) => {
    if (process.env.HARNESS_DEBUG) process.stderr.write(chunk);
    stderr.tail = (stderr.tail + chunk.toString("utf8")).slice(
      -STDERR_TAIL_CHARS,
    );
  });
  return transport;
}

function definedEnv(): Record<string, string> {
  const entries = Object.entries(process.env).filter(
    (entry): entry is [string, string] => entry[1] !== undefined,
  );
  return Object.fromEntries(entries);
}

interface McpToolInfo {
  name: string;
  description?: string;
  inputSchema: JsonSchema;
  annotations?: { destructiveHint?: boolean };
}

function wrapTool(client: Client, info: McpToolInfo, timeout: number): Tool {
  return {
    name: info.name,
    description: info.description?.trim() || info.name,
    parameters: info.inputSchema,
    // 伺服器明確標記為破壞性操作的工具，執行前要先問使用者。
    needsApproval: info.annotations?.destructiveHint === true,
    // 參數由伺服器自己驗證。
    parse: (args) => args,
    execute: async (args, context) => {
      const result = await client.callTool(
        { name: info.name, arguments: args },
        undefined,
        { timeout, signal: context.signal },
      );
      const text = contentToText(result.content, result.structuredContent);
      if (result.isError) throw new ToolError(text);
      return text;
    },
  };
}

/** 把 MCP 的回傳內容轉成文字。 */
function contentToText(content: unknown, structured: unknown): string {
  const blocks = Array.isArray(content) ? content : [];
  const parts = blocks.map((block: { type?: string; text?: string }) =>
    block.type === "text" ? (block.text ?? "") : `[${block.type} 內容]`,
  );
  const text = parts.join("\n").trim();
  if (text) return text;
  return structured === undefined ? "" : JSON.stringify(structured);
}
