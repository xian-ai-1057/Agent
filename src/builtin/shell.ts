/**
 * 內建的指令工具（toolset 名稱：shell）。
 *
 * 設計參考 Claude Code 的 Bash 與 Codex 的 shell：在專案資料夾裡執行
 * 一個不需要互動的指令，回傳結束代碼與輸出，讓 agent 能跑測試、建置、
 * git 等命令列工具。指令可以做任何事，所以預設需要使用者同意。
 */

import { spawn } from "node:child_process";
import { z } from "zod";
import { type Tool, ToolError, tool } from "../tool.ts";
import type { RunContext } from "../types.ts";
import { resolvePath } from "./paths.ts";

export const DEFAULT_TIMEOUT_SECONDS = 120;
export const MAX_TIMEOUT_SECONDS = 600;
/** 每個輸出串流（stdout、stderr）最多回傳幾個字元，超過時保留頭尾。 */
export const MAX_STREAM_CHARS = 15_000;
/** 每個輸出串流最多收集幾個位元組，避免失控的輸出吃光記憶體。 */
const MAX_CAPTURE_BYTES = 1_000_000;

/** 文字過長時保留開頭與結尾，中間註明省略了多少。 */
export function clipMiddle(text: string, limit = MAX_STREAM_CHARS): string {
  if (text.length <= limit) return text;
  const half = Math.floor(limit / 2);
  const omitted = text.length - half * 2;
  return `${text.slice(0, half)}\n…[中間省略 ${omitted} 個字元]…\n${text.slice(-half)}`;
}

interface Captured {
  chunks: Buffer[];
  bytes: number;
}

function capture(target: Captured, chunk: Buffer): void {
  const room = MAX_CAPTURE_BYTES - target.bytes;
  if (room <= 0) return;
  const kept = chunk.length > room ? chunk.subarray(0, room) : chunk;
  target.chunks.push(kept);
  target.bytes += kept.length;
}

interface CommandResult {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  timedOut: boolean;
}

/** 執行指令並收集輸出；逾時或使用者中斷時終止整個行程群組。 */
function execute(
  command: string,
  cwd: string,
  timeoutSeconds: number,
  signal?: AbortSignal,
): Promise<CommandResult> {
  return new Promise((resolvePromise, reject) => {
    const posix = process.platform !== "win32";
    const child = spawn(command, {
      cwd,
      shell: true,
      stdio: ["ignore", "pipe", "pipe"],
      // 在 POSIX 上自成一個行程群組，終止時連子行程一起結束。
      detached: posix,
      windowsHide: true,
    });
    const stdout: Captured = { chunks: [], bytes: 0 };
    const stderr: Captured = { chunks: [], bytes: 0 };
    child.stdout.on("data", (chunk: Buffer) => capture(stdout, chunk));
    child.stderr.on("data", (chunk: Buffer) => capture(stderr, chunk));

    let timedOut = false;
    const kill = () => {
      if (child.pid === undefined) return;
      if (child.exitCode !== null || child.signalCode !== null) return;
      try {
        if (posix) process.kill(-child.pid, "SIGKILL");
        else child.kill("SIGKILL");
      } catch {
        // 行程已經結束。
      }
    };
    const timer = setTimeout(() => {
      timedOut = true;
      kill();
    }, timeoutSeconds * 1000);
    signal?.addEventListener("abort", kill, { once: true });
    const cleanup = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", kill);
    };

    child.on("error", (error) => {
      cleanup();
      reject(new ToolError(`無法執行指令：${error.message}`));
    });
    child.on("close", (code) => {
      cleanup();
      resolvePromise({
        exitCode: code,
        stdout: Buffer.concat(stdout.chunks).toString("utf8"),
        stderr: Buffer.concat(stderr.chunks).toString("utf8"),
        timedOut,
      });
    });
  });
}

/** 把執行結果整理成給模型看的文字。 */
export function formatCommandResult(
  result: CommandResult,
  timeoutSeconds: number,
): string {
  const lines: string[] = [];
  if (result.timedOut) {
    lines.push(`指令執行超過 ${timeoutSeconds} 秒，已終止。`);
  }
  lines.push(`結束代碼：${result.exitCode ?? "無（被終止）"}`);
  if (result.stdout.trim()) {
    lines.push("[stdout]", clipMiddle(result.stdout.trimEnd()));
  }
  if (result.stderr.trim()) {
    lines.push("[stderr]", clipMiddle(result.stderr.trimEnd()));
  }
  if (!result.stdout.trim() && !result.stderr.trim())
    lines.push("（沒有輸出）");
  return lines.join("\n");
}

export const runCommandTool = tool({
  name: "run_command",
  description:
    "在專案資料夾裡執行一個 shell 指令，回傳結束代碼、stdout 與 stderr。" +
    "適合跑測試、建置、git 與其他命令列工具。指令不能互動（沒有標準輸入），" +
    "需要確認的指令請加上 --yes 之類的參數。讀寫檔案請優先用 files 工具。",
  parameters: z.object({
    command: z
      .string()
      .min(1)
      .describe("要執行的指令，例如 npm test 或 git status"),
    cwd: z.string().default(".").describe("在哪個資料夾執行，相對於專案根目錄"),
    timeout: z
      .number()
      .int()
      .min(1)
      .max(MAX_TIMEOUT_SECONDS)
      .default(DEFAULT_TIMEOUT_SECONDS)
      .describe(`逾時秒數，超過就終止指令（最多 ${MAX_TIMEOUT_SECONDS}）`),
  }),
  needsApproval: true,
  execute: async ({ command, cwd, timeout }, context: RunContext) => {
    const directory = resolvePath(context, cwd);
    const result = await execute(command, directory, timeout, context.signal);
    return formatCommandResult(result, timeout);
  },
});

export const shellTools: Tool[] = [runCommandTool];
