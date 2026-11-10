// 測試用的 MCP 伺服器，由測試以 `node` 啟動。
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

if (process.argv.includes("--crash")) {
  console.error("ModuleNotFoundError: No module named 'my_project'");
  process.exit(1);
}

const server = new McpServer({ name: "fixture", version: "1.0.0" });

server.registerTool(
  "greet",
  {
    description: "打招呼。",
    inputSchema: { name: z.string().describe("對方的名字") },
  },
  async ({ name }) => ({ content: [{ type: "text", text: `哈囉 ${name}` }] }),
);

server.registerTool(
  "read_env",
  { description: "讀取環境變數。", inputSchema: { key: z.string() } },
  async ({ key }) => ({
    content: [{ type: "text", text: process.env[key] ?? "(未設定)" }],
  }),
);

server.registerTool(
  "fail",
  { description: "一定會失敗。", inputSchema: {} },
  async () => ({
    content: [{ type: "text", text: "找不到這個月份的資料" }],
    isError: true,
  }),
);

server.registerTool(
  "wipe",
  {
    description: "刪除全部資料。",
    inputSchema: {},
    annotations: { destructiveHint: true },
  },
  async () => ({ content: [{ type: "text", text: "已刪除" }] }),
);

server.registerTool(
  "structured",
  { description: "只回傳結構化內容。", inputSchema: {} },
  async () => ({ content: [], structuredContent: { total: 3 } }),
);

await server.connect(new StdioServerTransport());
