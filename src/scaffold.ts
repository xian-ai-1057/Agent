/** 建立新專案的範本檔案（`harness new`）。 */

import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";

export type ScaffoldLanguage = "python" | "ts";

const CONFIG_HEAD = `# 專案設定
name: {name}

# 模型名稱，寫法取決於你連接的端點（見 README 的「模型設定」）。
# 不寫的話使用環境變數 HARNESS_MODEL。
# model: anthropic/claude-opus-5-5

defaultAgent: main
`;

const CONFIG_MCP = `
# 把 Python 函式公開成工具的 MCP 伺服器。
# harness 啟動時會自動執行它，伺服器名稱（tools）就是 toolset 名稱。
mcpServers:
  tools:
    command: python3
    args: [tools/server.py]
`;

const CONFIG_TAIL = `
permissions:
  # ask: 需要同意的工具要先問使用者
  # bypass: 全部直接執行
  # dontAsk: 不詢問，需要同意的工具一律拒絕
  mode: ask
  allow: []
  deny: []
  # 執行前要先詢問的工具名稱樣式，例如 ["send_*", "delete_*"]
  ask: []
`;

const AGENTS_MD = `# {name}

這裡寫整個專案共用的背景知識與規則，會接在每個 agent 的指示後面。
`;

const MAIN_AGENT = `---
name: main
description: 專案的主要助手
# tools 可以寫 toolset 名稱（MCP 伺服器名稱、tools/ 底下的 .ts 檔名）
# 或單一工具名稱；整個欄位不寫的話，可以使用專案裡所有的工具。
# 內建的 toolset 要明確列出才會啟用：files（讀取、搜尋、修改檔案）、
# shell（執行指令）、plan（列出計畫）、web（讀取網頁）。
tools:
  - {toolset}
---
你是 {name} 的助手。用使用者的語言回答，需要資料時就呼叫工具，不要
自己編造工具才能取得的內容。
`;

const PYTHON_SERVER = `"""把函式公開成工具。

在這裡定義新的函式，或 import 現有的模組，再用 @mcp.tool() 包一層。
需要先安裝 MCP 套件：pip install "mcp>=2"
"""

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

mcp = MCPServer("{name}")


@mcp.tool()
def word_count(text: str) -> dict[str, int]:
    """計算一段文字的字元數與行數。

    Args:
        text: 要計算的文字。
    """
    if not text:
        # ToolError 的訊息會原樣送給模型，讓它知道怎麼修正。
        raise ToolError("text 不可以是空字串")
    return {"characters": len(text), "lines": len(text.splitlines())}


if __name__ == "__main__":
    mcp.run()
`;

const TS_TOOL = `/**
 * 範例工具。每個匯出的物件都是一個工具，檔名就是 toolset 名稱。
 *
 * 專案裡有安裝 agent-harness 與 zod 時，也可以改用 tool() 搭配 zod
 * schema，參數會自動驗證並推導型別。
 */

export const wordCount = {
  name: "word_count",
  description: "計算一段文字的字元數與行數。",
  parameters: {
    type: "object",
    properties: {
      text: { type: "string", description: "要計算的文字" },
    },
    required: ["text"],
  },
  execute: ({ text }: { text: string }) => ({
    characters: text.length,
    lines: text.split("\\n").length,
  }),
};
`;

const GITIGNORE = `.harness/
__pycache__/
node_modules/
`;

/**
 * 在指定資料夾建立專案範本，不會覆寫已存在的檔案。
 *
 * @param path 專案資料夾，不存在時會建立。
 * @param language 工具用哪種語言寫：python（MCP 伺服器）或 ts。
 * @returns 這次實際建立的檔案路徑。
 */
export function createProject(
  path: string,
  language: ScaffoldLanguage = "python",
): string[] {
  const root = resolve(path);
  const python = language === "python";
  const files: Record<string, string> = {
    "harness.yaml": CONFIG_HEAD + (python ? CONFIG_MCP : "") + CONFIG_TAIL,
    "AGENTS.md": AGENTS_MD,
    "agents/main.md": MAIN_AGENT,
    ".gitignore": GITIGNORE,
  };
  if (python) files["tools/server.py"] = PYTHON_SERVER;
  else files["tools/example.ts"] = TS_TOOL;

  const created: string[] = [];
  for (const [relative, template] of Object.entries(files)) {
    const target = join(root, relative);
    if (existsSync(target)) continue;
    mkdirSync(dirname(target), { recursive: true });
    const content = template
      .replaceAll("{name}", basename(root))
      .replaceAll("{toolset}", python ? "tools" : "example");
    writeFileSync(target, content, "utf8");
    created.push(target);
  }
  return created;
}
