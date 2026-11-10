import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { connectMcpServer, type McpConnection } from "../src/mcp.ts";
import { ToolError } from "../src/tool.ts";
import { contextIn, FIXTURE_DIR, MCP_FIXTURE } from "./helpers.ts";

const here = FIXTURE_DIR;
const FIXTURE = MCP_FIXTURE;
const context = contextIn(here);

describe("connectMcpServer", () => {
  let connection: McpConnection;
  const toolNamed = (name: string) => {
    const found = connection.tools.find((item) => item.name === name);
    if (!found) throw new Error(`fixture 沒有 ${name}`);
    return found;
  };

  beforeAll(async () => {
    connection = await connectMcpServer(
      "fixture",
      {
        command: "node",
        args: [FIXTURE],
        env: { FIXTURE_SECRET: "從設定來的" },
      },
      here,
    );
  });
  afterAll(() => connection.close());

  it("列出伺服器的工具與參數格式", () => {
    const greet = toolNamed("greet");

    expect(connection.tools.map((item) => item.name)).toContain("fail");
    expect(greet.description).toBe("打招呼。");
    expect(greet.parameters).toMatchObject({
      type: "object",
      properties: { name: { type: "string", description: "對方的名字" } },
      required: ["name"],
    });
  });

  it("呼叫工具並取得文字結果", async () => {
    const output = await toolNamed("greet").execute({ name: "小明" }, context);

    expect(output).toBe("哈囉 小明");
  });

  it("伺服器回報錯誤時丟出帶有原訊息的 ToolError", async () => {
    const pending = toolNamed("fail").execute({}, context);

    await expect(pending).rejects.toThrow(ToolError);
    await expect(toolNamed("fail").execute({}, context)).rejects.toThrow(
      "找不到這個月份的資料",
    );
  });

  it("只有結構化內容時轉成 JSON 文字", async () => {
    expect(await toolNamed("structured").execute({}, context)).toBe(
      '{"total":3}',
    );
  });

  it("標記為破壞性的工具需要使用者同意", () => {
    expect(toolNamed("wipe").needsApproval).toBe(true);
    expect(toolNamed("greet").needsApproval).toBe(false);
  });

  it("伺服器拿得到設定的環境變數與原本的環境變數", async () => {
    const readEnv = toolNamed("read_env");

    expect(await readEnv.execute({ key: "FIXTURE_SECRET" }, context)).toBe(
      "從設定來的",
    );
    expect(await readEnv.execute({ key: "HARNESS_MODEL" }, context)).toBe(
      "test/model",
    );
  });
});

describe("connectMcpServer：失敗情況", () => {
  it("伺服器啟動失敗時，錯誤訊息包含它的 stderr", async () => {
    const pending = connectMcpServer(
      "broken",
      { command: "node", args: [FIXTURE, "--crash"] },
      here,
    );

    await expect(pending).rejects.toThrow(
      /無法啟動 MCP 伺服器 broken[\s\S]*No module named 'my_project'/,
    );
  });

  it("沒有 command 也沒有 url 時丟出錯誤", async () => {
    await expect(connectMcpServer("empty", {}, here)).rejects.toThrow(
      /必須設定 command 或 url/,
    );
  });
});
