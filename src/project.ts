/**
 * 載入專案資料夾。
 *
 *   my-project/
 *   ├── harness.yaml   名稱、模型、預設 agent、權限、MCP 伺服器
 *   ├── AGENTS.md      專案說明，會接在每個 agent 的指示後面（可省略）
 *   ├── agents/*.md    一個檔案一個 agent：開頭是設定，內文是指示
 *   └── tools/*.ts     用 TypeScript 寫的工具（可省略）
 *
 * 工具有三個來源，每個來源都是一個 toolset：
 *   - harness.yaml 的 mcpServers：名稱是伺服器名稱
 *   - tools/ 底下的檔案：名稱是檔名
 *   - 內建工具：files
 */

import { existsSync, readdirSync, readFileSync } from "node:fs";
import { basename, extname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { parse as parseYaml } from "yaml";
import { type Agent, defineAgent } from "./agent.ts";
import { fileTools } from "./builtin/files.ts";
import {
  connectMcpServer,
  type McpConnection,
  type McpServerConfig,
} from "./mcp.ts";
import { definePermissions, type Permissions } from "./permissions.ts";
import { isToolLike, type Tool, tool } from "./tool.ts";

export const CONFIG_FILE = "harness.yaml";
const TOOL_EXTENSIONS = [".ts", ".mts", ".js", ".mjs"];
const BUILTIN_TOOLSETS: Record<string, Tool[]> = { files: fileTools };

/** 一個載入完成的專案。 */
export interface Project {
  name: string;
  /** 專案根目錄的絕對路徑。 */
  root: string;
  /** 專案裡所有的 agent，以名稱為鍵。 */
  agents: Record<string, Agent>;
  /** 專案自己的工具（不含內建工具），以名稱為鍵。 */
  tools: Record<string, Tool>;
  /** 每個 toolset（含內建的 files）包含哪些工具，以 toolset 名稱為鍵。 */
  toolsets: Record<string, Tool[]>;
  permissions: Permissions;
  /** 沒指定時使用的 agent 名稱。 */
  defaultAgent: string;
  /** 存放 session 檔案的資料夾。 */
  sessionsDir: string;
  /** 關閉專案啟動的 MCP 伺服器，用完專案後一定要呼叫。 */
  close(): Promise<void>;
}

type Config = Record<string, unknown>;
type Toolsets = Record<string, Tool[]>;

/**
 * 讀取專案資料夾，並啟動它設定的 MCP 伺服器。
 *
 * @param path 專案根目錄（裡面要有 harness.yaml）。
 * @throws 找不到 harness.yaml，或設定內容有誤時。
 */
export async function loadProject(path = "."): Promise<Project> {
  const root = resolve(path);
  const configPath = join(root, CONFIG_FILE);
  if (!existsSync(configPath)) {
    throw new Error(
      `${root} 裡沒有 ${CONFIG_FILE}，可用 \`harness new\` 建立專案`,
    );
  }
  const config = (parseYaml(readFileSync(configPath, "utf8")) ?? {}) as Config;

  const connections: McpConnection[] = [];
  const close = async () => {
    await Promise.all(connections.map((item) => item.close()));
  };
  try {
    const servers = (config.mcpServers ?? {}) as Record<
      string,
      McpServerConfig
    >;
    for (const [name, serverConfig] of Object.entries(servers)) {
      connections.push(await connectMcpServer(name, serverConfig, root));
    }
    const projectToolsets: Toolsets = {
      ...Object.fromEntries(connections.map((item) => [item.name, item.tools])),
      ...(await loadToolModules(root)),
    };
    const tools = indexTools(projectToolsets);
    const toolsets = { ...BUILTIN_TOOLSETS, ...projectToolsets };
    const agents = loadAgents(root, config, toolsets, tools);
    const defaultAgent =
      (config.defaultAgent as string) ?? Object.keys(agents)[0];
    if (!agents[defaultAgent]) {
      throw new Error(`defaultAgent 指向不存在的 agent：${defaultAgent}`);
    }
    return {
      name: (config.name as string) ?? basename(root),
      root,
      agents,
      tools,
      toolsets,
      permissions: definePermissions(
        (config.permissions ?? {}) as Partial<Permissions>,
      ),
      defaultAgent,
      sessionsDir: join(root, ".harness", "sessions"),
      close,
    };
  } catch (error) {
    await close();
    throw error;
  }
}

/** 把 Markdown 檔拆成開頭的 YAML 設定與內文。 */
export function parseFrontmatter(text: string): {
  meta: Config;
  body: string;
} {
  const match = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/.exec(text);
  if (!match) return { meta: {}, body: text.trim() };
  const meta = (parseYaml(match[1] ?? "") ?? {}) as Config;
  return { meta, body: (match[2] ?? "").trim() };
}

/**
 * 把 agent 設定裡的名稱換成實際的工具。
 * 名稱可以是 toolset（MCP 伺服器名稱、tools/ 底下的檔名、內建的 files）
 * 或單一工具。
 *
 * @throws 名稱既不是 toolset 也不是工具時。
 */
export function resolveTools(
  names: string[],
  toolsets: Toolsets,
  tools: Record<string, Tool>,
): Tool[] {
  const resolved = new Map<string, Tool>();
  for (const name of names) {
    const found = toolsets[name] ?? (tools[name] ? [tools[name]] : undefined);
    if (!found) {
      const known = [...Object.keys(toolsets), ...Object.keys(tools)];
      throw new Error(
        `找不到工具或 toolset：${name}，可用的有：${known.join("、")}`,
      );
    }
    for (const item of found) resolved.set(item.name, item);
  }
  return [...resolved.values()];
}

/** 載入 tools/ 底下每個檔案匯出的工具。 */
async function loadToolModules(root: string): Promise<Toolsets> {
  const directory = join(root, "tools");
  if (!existsSync(directory)) return {};
  const toolsets: Toolsets = {};
  for (const file of readdirSync(directory).sort()) {
    const extension = extname(file);
    if (!TOOL_EXTENSIONS.includes(extension) || file.startsWith("_")) continue;
    const module = await import(pathToFileURL(join(directory, file)).href);
    const found = Object.values(module as Record<string, unknown>)
      .flat()
      .filter(isToolLike)
      // 用 tool() 建立的工具原樣使用；直接寫成物件的則補上預設值。
      .map((item) => ("parse" in item ? (item as Tool) : tool(item)));
    toolsets[basename(file, extension)] = found;
  }
  return toolsets;
}

/** 把所有 toolset 的工具攤平成以名稱為鍵的表，並檢查名稱沒有重複。 */
function indexTools(toolsets: Toolsets): Record<string, Tool> {
  const tools: Record<string, Tool> = {};
  const origin: Record<string, string> = {};
  for (const [setName, items] of Object.entries(toolsets)) {
    for (const item of items) {
      if (BUILTIN_TOOLSETS.files?.some((b) => b.name === item.name)) {
        throw new Error(`${setName} 的工具 ${item.name} 與內建工具同名`);
      }
      if (tools[item.name]) {
        throw new Error(
          `工具名稱重複：${item.name}（${origin[item.name]} 與 ${setName}）`,
        );
      }
      tools[item.name] = item;
      origin[item.name] = setName;
    }
  }
  return tools;
}

function loadAgents(
  root: string,
  config: Config,
  toolsets: Toolsets,
  tools: Record<string, Tool>,
): Record<string, Agent> {
  const directory = join(root, "agents");
  const files = existsSync(directory)
    ? readdirSync(directory)
        .filter((file) => file.endsWith(".md"))
        .sort()
    : [];
  if (files.length === 0) {
    throw new Error(`${directory} 裡沒有任何 agent（*.md）`);
  }
  const sharedPath = join(root, "AGENTS.md");
  const shared = existsSync(sharedPath)
    ? readFileSync(sharedPath, "utf8").trim()
    : "";
  const agents: Record<string, Agent> = {};
  for (const file of files) {
    const text = readFileSync(join(directory, file), "utf8");
    const { meta, body } = parseFrontmatter(text);
    const agent = defineAgent({
      name: (meta.name as string) ?? basename(file, ".md"),
      instructions: [body, shared].filter(Boolean).join("\n\n"),
      // 沒寫 tools 欄位時，預設可以使用專案裡所有的工具。
      tools:
        "tools" in meta
          ? resolveTools((meta.tools as string[]) ?? [], toolsets, tools)
          : Object.values(tools),
      model: (meta.model ?? config.model) as string | undefined,
      modelParams: (meta.modelParams ?? config.modelParams) as
        | Record<string, unknown>
        | undefined,
      maxTurns: (meta.maxTurns ?? config.maxTurns) as number | undefined,
      description: meta.description as string | undefined,
    });
    agents[agent.name] = agent;
  }
  return agents;
}
