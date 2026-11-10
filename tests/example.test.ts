/** 範例專案的整合測試：Python 的既有功能透過 MCP 交給 agent 使用。 */

import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { run } from "../src/loop.ts";
import { loadProject, type Project } from "../src/project.ts";
import { createProject } from "../src/scaffold.ts";
import {
  contextIn,
  fakeModel,
  lastContent,
  tempDir,
  textResponse,
  toolResponse,
} from "./helpers.ts";

const EXAMPLE = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "examples",
  "report_bot",
);
// 這組測試需要 Python 與 MCP 套件，環境沒有時略過。
const probe = spawnSync("python3", ["-c", "import mcp.server.mcpserver"]);

describe.skipIf(probe.status !== 0)("examples/report_bot", () => {
  let project: Project;
  beforeAll(async () => {
    project = await loadProject(EXAMPLE);
  });
  afterAll(() => project?.close());

  const mainAgent = () => {
    const agent = project.agents.main;
    if (!agent) throw new Error("範例專案沒有 main agent");
    return agent;
  };

  it("agent 拿到 Python 伺服器的工具與內建檔案工具", () => {
    expect(mainAgent().tools.map((item) => item.name)).toEqual([
      "list_months",
      "get_sales_summary",
      "build_sales_report",
      "read_file",
      "list_dir",
      "write_file",
    ]);
  });

  it("透過工具產生報告", async () => {
    const { model, requests } = fakeModel([
      toolResponse(["build_sales_report", { month: "2026-10" }]),
      textResponse("報告如上"),
    ]);

    await run(mainAgent(), "給我十月報告", { model });

    const report = lastContent(requests, 1);
    expect(report).toContain("# 2026-10 銷售報告");
    expect(report).toContain("| **合計** | **192,000** |");
  });

  it("Python 端的 ToolError 訊息會送到模型", async () => {
    const { model, requests } = fakeModel([
      toolResponse(["get_sales_summary", { month: "1999-01" }]),
      textResponse("沒有資料"),
    ]);

    await run(mainAgent(), "1999 年呢", { model });

    expect(lastContent(requests, 1)).toContain(
      "1999-01 沒有銷售資料，請先用 list_months 確認",
    );
  });

  it("AGENTS.md 接在 agent 的指示後面", () => {
    expect(mainAgent().instructions).toMatch(/^你是銷售報告助手/);
    expect(mainAgent().instructions).toContain("金額單位是新台幣");
  });
});

describe.skipIf(probe.status !== 0)("harness new 的 Python 範本", () => {
  it("建立後可以直接載入並呼叫範例工具", async () => {
    const root = join(tempDir(), "py_project");
    createProject(root, "python");
    const project = await loadProject(root);

    try {
      const wordCount = project.agents.main?.tools[0];
      const output = await wordCount?.execute(
        { text: "一\n二" },
        contextIn(root),
      );

      expect(wordCount?.name).toBe("word_count");
      expect(JSON.parse(String(output))).toEqual({ characters: 3, lines: 2 });
    } finally {
      await project.close();
    }
  });
});
