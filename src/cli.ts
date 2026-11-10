/**
 * 命令列介面。
 *
 *   harness new <資料夾>      建立新專案
 *   harness chat              在目前專案開始聊天
 *   harness run "<訊息>"      執行一次後結束
 */

import { createInterface, type Interface } from "node:readline";
import type { Readable } from "node:stream";
import { parseArgs } from "node:util";
import type { Agent } from "./agent.ts";
import { formatArguments, runStream } from "./loop.ts";
import type { ModelFn } from "./model.ts";
import type { Approver, Permissions } from "./permissions.ts";
import { loadProject, type Project } from "./project.ts";
import { createProject, type ScaffoldLanguage } from "./scaffold.ts";
import { createSession, latestSession, loadSession } from "./session.ts";
import type { RunResult } from "./types.ts";

/** CLI 與外界的接點，測試時可以替換。 */
export interface CliEnvironment {
  /** 輸出到 stdout。 */
  write?: (text: string) => void;
  /** 輸出到 stderr。 */
  writeError?: (text: string) => void;
  /** 使用者輸入的來源，預設為 stdin。 */
  input?: Readable;
  /** 是否可以向使用者提問，預設看 stdin 是不是終端機。 */
  interactive?: boolean;
  /** 覆寫模型函式。 */
  model?: ModelFn;
}

const USAGE = `用法：
  harness new <資料夾> [--lang python|ts]   建立新專案
  harness chat [選項]                       開始聊天
  harness run [選項] "<訊息>"               執行一次後結束

chat 與 run 的選項：
  -p, --project <資料夾>   專案位置（預設為目前資料夾）
  -a, --agent <名稱>       使用哪個 agent
  -m, --model <名稱>       覆寫模型
  -c, --continue           接續最新的對話
  -r, --resume <ID>        接續指定的對話
      --bypass             所有工具都不詢問直接執行
`;

const EXIT_COMMANDS = ["/exit", "/quit"];
const STOP_NOTICES: Record<string, string> = {
  max_turns: "已達到單次對話的工具呼叫輪數上限，可以再傳訊息讓它繼續。",
  max_tokens: "回覆達到長度上限而被截斷。",
};

interface Options {
  project?: string;
  agent?: string;
  model?: string;
  continue?: boolean;
  resume?: string;
  bypass?: boolean;
  lang?: string;
  help?: boolean;
}

interface Io {
  write: (text: string) => void;
  writeError: (text: string) => void;
  input: Readable;
  interactive: boolean;
  model?: ModelFn;
}

/**
 * CLI 進入點。
 *
 * @returns 結束代碼，0 表示成功。
 */
export async function main(
  argv: string[] = process.argv.slice(2),
  environment: CliEnvironment = {},
): Promise<number> {
  const io: Io = {
    write: environment.write ?? ((text) => void process.stdout.write(text)),
    writeError:
      environment.writeError ?? ((text) => void process.stderr.write(text)),
    input: environment.input ?? process.stdin,
    interactive: environment.interactive ?? Boolean(process.stdin.isTTY),
    model: environment.model,
  };
  let options: Options;
  let positionals: string[];
  try {
    ({ values: options, positionals } = parseArgs({
      args: argv,
      allowPositionals: true,
      options: {
        project: { type: "string", short: "p" },
        agent: { type: "string", short: "a" },
        model: { type: "string", short: "m" },
        continue: { type: "boolean", short: "c" },
        resume: { type: "string", short: "r" },
        bypass: { type: "boolean" },
        lang: { type: "string" },
        help: { type: "boolean", short: "h" },
      },
    }));
  } catch (error) {
    io.writeError(`錯誤：${(error as Error).message}\n\n${USAGE}`);
    return 2;
  }
  const [command, argument] = positionals;
  if (options.help) {
    io.write(USAGE);
    return 0;
  }
  try {
    if (command === "new" && argument) {
      return commandNew(argument, options, io);
    }
    if (command === "chat" || (command === "run" && argument)) {
      return await commandAgent(command, argument, options, io);
    }
  } catch (error) {
    io.writeError(`錯誤：${(error as Error).message}\n`);
    return 1;
  }
  io.writeError(USAGE);
  return 2;
}

function commandNew(path: string, options: Options, io: Io): number {
  const language = options.lang ?? "python";
  if (language !== "python" && language !== "ts") {
    throw new Error(`--lang 必須是 python 或 ts：${language}`);
  }
  const created = createProject(path, language as ScaffoldLanguage);
  if (created.length === 0) {
    io.write("檔案都已存在，沒有建立任何東西。\n");
    return 0;
  }
  for (const file of created) io.write(`建立 ${file}\n`);
  if (language === "python") {
    io.write('\n工具伺服器需要 Python 的 MCP 套件：pip install "mcp>=2"\n');
  }
  io.write(`\n完成。接著執行：cd ${path} && harness chat\n`);
  return 0;
}

/** chat 與 run 共用的流程。 */
async function commandAgent(
  command: "chat" | "run",
  prompt: string | undefined,
  options: Options,
  io: Io,
): Promise<number> {
  const project = await loadProject(options.project ?? ".");
  let controller: AbortController | undefined;
  let chat: Interface | undefined;
  // 執行中按 Ctrl-C 只中斷目前這一輪；沒有在執行時則離開。
  const onInterrupt = () => {
    if (controller) controller.abort();
    else chat?.close();
  };
  process.on("SIGINT", onInterrupt);
  try {
    const agent = pickAgent(project, options);
    const session = openSession(project, options);
    const permissions: Permissions = {
      ...project.permissions,
      mode: options.bypass ? "bypass" : project.permissions.mode,
    };

    const turn = async (text: string): Promise<boolean> => {
      controller = new AbortController();
      const { signal } = controller;
      try {
        const events = runStream(agent, text, {
          session,
          permissions,
          workdir: project.root,
          model: io.model,
          signal,
        });
        await printEvents(events, io);
        return true;
      } catch (error) {
        // 模型或網路出錯時顯示原因，讓使用者可以繼續對話。
        const reason = signal.aborted
          ? "⚠ 已中斷這一輪。"
          : `✗ ${describeError(error)}`;
        io.write(`\n  ${reason}\n`);
        return false;
      } finally {
        controller = undefined;
      }
    };

    if (command === "run") {
      if (io.interactive) {
        permissions.approver = makeApprover((question) =>
          askOnce(io.input, question),
        );
      }
      return (await turn(prompt ?? "")) ? 0 : 1;
    }

    chat = createInterface({
      input: io.input,
      output: io.interactive ? process.stdout : undefined,
      terminal: io.interactive,
      prompt: "你> ",
    });
    const rl = chat;
    rl.on("SIGINT", onInterrupt);
    if (io.interactive) {
      permissions.approver = makeApprover(
        (question) => new Promise((done) => rl.question(question, done)),
      );
    }
    io.write(`${project.name} / ${agent.name}（session ${session.id}）\n`);
    io.write("輸入訊息開始對話，/exit 離開，Ctrl-C 中斷目前這一輪。\n");
    const showPrompt = () => {
      if (!io.interactive) return;
      io.write("\n");
      rl.prompt();
    };
    showPrompt();
    for await (const line of rl) {
      const text = line.trim();
      if (EXIT_COMMANDS.includes(text)) break;
      if (text) await turn(text);
      showPrompt();
    }
    rl.close();
    return 0;
  } finally {
    process.off("SIGINT", onInterrupt);
    await project.close();
  }
}

function pickAgent(project: Project, options: Options): Agent {
  const name = options.agent ?? project.defaultAgent;
  const agent = project.agents[name];
  if (!agent) {
    const available = Object.keys(project.agents).join("、");
    throw new Error(`沒有 ${name} 這個 agent，可用的有：${available}`);
  }
  return options.model ? { ...agent, model: options.model } : agent;
}

/** 建立新的 session，或依 --resume / --continue 讀取既有的。 */
function openSession(project: Project, options: Options) {
  if (options.resume) return loadSession(project.sessionsDir, options.resume);
  if (!options.continue) return createSession(project.sessionsDir);
  const found = latestSession(project.sessionsDir);
  if (!found) throw new Error("這個專案還沒有任何 session 可以接續");
  return found;
}

/** 把一輪對話的過程印出來。 */
async function printEvents(
  events: ReturnType<typeof runStream>,
  io: Io,
): Promise<void> {
  let atLineStart = true;
  for await (const event of events) {
    if (event.type === "text_delta") {
      io.write(event.text);
      atLineStart = event.text.endsWith("\n");
      continue;
    }
    if (!atLineStart) {
      io.write("\n");
      atLineStart = true;
    }
    if (event.type === "tool_call") {
      const { call } = event;
      io.write(`  ⚙ ${call.name}(${formatArguments(call)})\n`);
    } else if (event.type === "tool_result" && event.result.isError) {
      io.write(`  ✗ ${event.result.output}\n`);
    } else if (event.type === "run_end") {
      printStopNotice(event.result, io);
    }
  }
}

/** 不是正常結束時，告訴使用者原因。 */
function printStopNotice(result: RunResult, io: Io): void {
  if (result.stopReason === "end_turn") return;
  const notice =
    STOP_NOTICES[result.stopReason] ?? `模型停止：${result.stopReason}`;
  io.write(`  ⚠ ${notice}\n`);
}

function makeApprover(ask: (question: string) => Promise<string>): Approver {
  return async (call, target) => {
    const question = `  ? 允許執行 ${target.name}(${formatArguments(call)}) 嗎？[y/N] `;
    const answer = await ask(question);
    return ["y", "yes"].includes(answer.trim().toLowerCase());
  };
}

/** 開一個暫時的輸入介面問一個問題。 */
function askOnce(input: Readable, question: string): Promise<string> {
  const rl = createInterface({ input, output: process.stdout });
  return new Promise((done) => {
    rl.question(question, (answer) => {
      rl.close();
      done(answer);
    });
  });
}

function describeError(error: unknown): string {
  if (!(error instanceof Error)) return String(error);
  return error.name === "Error"
    ? error.message
    : `${error.name}: ${error.message}`;
}
