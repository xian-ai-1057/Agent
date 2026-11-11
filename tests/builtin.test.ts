import { mkdirSync, writeFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { BUILTIN_TOOLSETS } from "../src/builtin/index.ts";
import { updatePlanTool } from "../src/builtin/plan.ts";
import { clipMiddle, runCommandTool } from "../src/builtin/shell.ts";
import { htmlToText, webFetchTool } from "../src/builtin/web.ts";
import { loadProject } from "../src/project.ts";
import { createProject } from "../src/scaffold.ts";
import type { Tool } from "../src/tool.ts";
import type { RunContext } from "../src/types.ts";
import { contextIn, tempDir } from "./helpers.ts";

/** 先驗證參數（補上預設值）再執行工具，和主迴圈的做法相同。 */
async function call(
  target: Tool,
  args: Record<string, unknown>,
  context: RunContext = contextIn(tempDir()),
) {
  return target.execute(target.parse(args), context);
}

describe("內建 toolsets", () => {
  it("有 files、shell、plan、web 四組", () => {
    expect(Object.keys(BUILTIN_TOOLSETS)).toEqual([
      "files",
      "shell",
      "plan",
      "web",
    ]);
  });

  it("會執行指令或連網的工具需要使用者同意", () => {
    expect(runCommandTool.needsApproval).toBe(true);
    expect(webFetchTool.needsApproval).toBe(true);
    expect(updatePlanTool.needsApproval).toBe(false);
  });

  it("agent 要明確列出才拿得到內建工具", async () => {
    const root = join(tempDir(), "demo");
    createProject(root, "ts");
    writeFileSync(
      join(root, "agents", "dev.md"),
      "---\ntools: [shell, plan]\n---\n你是助手\n",
    );

    const project = await loadProject(root);
    await project.close();

    expect(project.agents.dev?.tools.map((item) => item.name)).toEqual([
      "run_command",
      "update_plan",
    ]);
    expect(project.agents.main?.tools.map((item) => item.name)).toEqual([
      "word_count",
    ]);
  });

  it("專案裡同名的 toolset 取代內建的那一組", async () => {
    const root = join(tempDir(), "demo");
    createProject(root, "ts");
    writeFileSync(
      join(root, "tools", "web.ts"),
      'export const ping = { name: "ping", execute: () => "pong" };\n',
    );
    writeFileSync(
      join(root, "agents", "main.md"),
      "---\ntools: [web]\n---\n你是助手\n",
    );

    const project = await loadProject(root);
    await project.close();

    expect(project.agents.main?.tools.map((item) => item.name)).toEqual([
      "ping",
    ]);
  });
});

describe("run_command", () => {
  it("回傳結束代碼與 stdout、stderr", async () => {
    const output = await call(runCommandTool, {
      command: "echo hello && echo oops 1>&2 && exit 3",
    });

    expect(output).toBe("結束代碼：3\n[stdout]\nhello\n[stderr]\noops");
  });

  it("在指定的子資料夾執行", async () => {
    const context = contextIn(tempDir());
    mkdirSync(join(context.workdir, "sub"));
    writeFileSync(join(context.workdir, "sub", "x.txt"), "");

    const output = await call(
      runCommandTool,
      { command: "ls", cwd: "sub" },
      context,
    );

    expect(output).toBe("結束代碼：0\n[stdout]\nx.txt");
  });

  it("逾時就終止指令", async () => {
    const output = await call(runCommandTool, {
      command: "sleep 5",
      timeout: 1,
    });

    expect(output).toContain("指令執行超過 1 秒，已終止");
  });

  it("不能在專案資料夾以外執行", async () => {
    await expect(
      call(runCommandTool, { command: "ls", cwd: ".." }),
    ).rejects.toThrow("超出專案資料夾");
  });

  it("輸出過長時保留頭尾", () => {
    const clipped = clipMiddle(`${"a".repeat(10)}${"b".repeat(10)}`, 10);

    expect(clipped).toBe("aaaaa\n…[中間省略 10 個字元]…\nbbbbb");
  });
});

describe("update_plan", () => {
  it("回傳整份計畫與完成進度", async () => {
    const output = await call(updatePlanTool, {
      plan: [
        { step: "讀需求", status: "completed" },
        { step: "實作", status: "in_progress" },
        { step: "測試", status: "pending" },
      ],
    });

    expect(output).toBe(
      "計畫已更新（已完成 1/3）：\n[x] 讀需求\n[>] 實作\n[ ] 測試",
    );
  });

  it("同時有兩個進行中的步驟時回報錯誤", async () => {
    const plan = [
      { step: "a", status: "in_progress" },
      { step: "b", status: "in_progress" },
    ];

    await expect(call(updatePlanTool, { plan })).rejects.toThrow(
      "只能有一個步驟是 in_progress",
    );
  });
});

describe("htmlToText", () => {
  it("去掉 script 與標籤，保留標題、清單與連結", () => {
    const html = `<html><head><title>T</title><style>p{}</style></head>
      <body><h1>標題</h1><script>alert(1)</script>
      <p>第一段&amp;更多&nbsp;文字</p>
      <ul><li>甲</li><li>乙</li></ul>
      <a href="/docs">文件</a></body></html>`;

    expect(htmlToText(html, "https://example.com/a/")).toBe(
      "# 標題\n\n第一段&更多 文字\n\n- 甲\n\n- 乙\n\n[文件](https://example.com/docs)",
    );
  });
});

describe("web_fetch", () => {
  let server: Server;
  let base = "";

  beforeAll(async () => {
    server = createServer((request, response) => {
      if (request.url === "/page") {
        response.setHeader("content-type", "text/html; charset=utf-8");
        response.end("<title>測試頁</title><p>你好</p>");
      } else if (request.url === "/data") {
        response.setHeader("content-type", "application/json");
        response.end('{"a":1}');
      } else if (request.url === "/image") {
        response.setHeader("content-type", "image/png");
        response.end("png");
      } else {
        response.statusCode = 404;
        response.end("missing");
      }
    });
    await new Promise<void>((resolve) =>
      server.listen(0, "127.0.0.1", resolve),
    );
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });

  afterAll(() => {
    server.close();
  });

  it("把 HTML 轉成文字並附上標題", async () => {
    const output = await call(webFetchTool, { url: `${base}/page` });

    expect(output).toBe(`網址：${base}/page\n標題：測試頁\n\n你好`);
  });

  it("JSON 原樣回傳", async () => {
    const output = await call(webFetchTool, { url: `${base}/data` });

    expect(output).toBe(`網址：${base}/data\n\n{"a":1}`);
  });

  it("內容過長時截斷", async () => {
    const output = await call(webFetchTool, {
      url: `${base}/data`,
      max_chars: 100,
    });

    expect(output).not.toContain("後面還有");
  });

  it.each([
    ["/missing", "伺服器回應 404"],
    ["/image", "不支援的內容類型：image/png"],
  ])("%s 回報 %s", async (path, message) => {
    await expect(call(webFetchTool, { url: `${base}${path}` })).rejects.toThrow(
      message,
    );
  });

  it("只接受 http 與 https", async () => {
    await expect(
      call(webFetchTool, { url: "file:///etc/passwd" }),
    ).rejects.toThrow("只支援 http 與 https");
  });
});
