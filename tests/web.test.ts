import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { request } from "node:http";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import type { ModelFn } from "../src/model.ts";
import { loadProject } from "../src/project.ts";
import type { ModelResponse } from "../src/types.ts";
import type {
  Approval,
  ProjectInfo,
  ServerEvent,
  SessionDetail,
  SessionSummary,
} from "../src/web/protocol.ts";
import { startWebServer, type WebServer } from "../src/web/server.ts";
import { fakeModel, tempDir, textResponse, toolResponse } from "./helpers.ts";

const CONFIG = `# 測試專案
name: web_demo
defaultAgent: main

permissions:
  # 預設先問
  mode: ask
`;

const AGENT = `---
name: main
description: 測試用的助手
tools:
  - files
---
你是助手。
`;

const running: { server: WebServer; close: () => Promise<void> }[] = [];

afterEach(async () => {
  for (const item of running.splice(0)) await item.close();
});

/** 建立專案並啟動伺服器。 */
async function start(
  options: { script?: ModelResponse[]; model?: ModelFn; static?: string } = {},
) {
  const root = tempDir();
  writeFileSync(join(root, "harness.yaml"), CONFIG);
  mkdirSync(join(root, "agents"));
  writeFileSync(join(root, "agents", "main.md"), AGENT);
  const project = await loadProject(root);
  const fake = fakeModel(options.script ?? []);
  const server = await startWebServer({
    project,
    port: 0,
    model: options.model ?? fake.model,
    staticDir: options.static,
  });
  const events = await listen(server.url);
  const close = async () => {
    events.close();
    await server.close();
    await project.close();
  };
  running.push({ server, close });
  return { root, server, events, requests: fake.requests };
}

/** 訂閱 SSE，收集所有事件。 */
async function listen(base: string) {
  const controller = new AbortController();
  const received: ServerEvent[] = [];
  const waiters: (() => void)[] = [];
  const response = await fetch(`${base}/api/events`, {
    signal: controller.signal,
  });
  const reader = (response.body as ReadableStream<Uint8Array>).getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  void (async () => {
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) return;
        buffer += decoder.decode(value, { stream: true });
        let index = buffer.indexOf("\n\n");
        while (index !== -1) {
          const chunk = buffer.slice(0, index);
          buffer = buffer.slice(index + 2);
          if (chunk.startsWith("data: ")) {
            received.push(JSON.parse(chunk.slice(6)) as ServerEvent);
            for (const wake of waiters.splice(0)) wake();
          }
          index = buffer.indexOf("\n\n");
        }
      }
    } catch {
      // 測試結束時中斷連線。
    }
  })();
  /** 等到出現符合條件的事件。 */
  const waitFor = async <T extends ServerEvent["type"]>(
    type: T,
    predicate: (event: Extract<ServerEvent, { type: T }>) => boolean = () =>
      true,
  ): Promise<Extract<ServerEvent, { type: T }>> => {
    const deadline = Date.now() + 3000;
    for (;;) {
      const found = received.find(
        (event): event is Extract<ServerEvent, { type: T }> =>
          event.type === type &&
          predicate(event as Extract<ServerEvent, { type: T }>),
      );
      if (found) return found;
      if (Date.now() > deadline) throw new Error(`沒有等到 ${type} 事件`);
      await new Promise<void>((wake) => {
        waiters.push(wake);
        setTimeout(wake, 50);
      });
    }
  };
  await waitFor("hello");
  return { received, waitFor, close: () => controller.abort() };
}

async function api<T>(
  server: WebServer,
  method: string,
  path: string,
  body?: unknown,
  headers: Record<string, string> = {},
): Promise<{ status: number; data: T }> {
  const response = await fetch(`${server.url}/api${path}`, {
    method,
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { status: response.status, data: (await response.json()) as T };
}

const writeCall = toolResponse([
  "write_file",
  { path: "reports/a.md", content: "# 報告\n" },
]);

describe("web：對話", () => {
  it("送出訊息後建立對話，串流回覆並存進紀錄", async () => {
    const { server, events } = await start({ script: [textResponse("你好")] });

    const created = await api<SessionSummary>(server, "POST", "/sessions", {
      text: "嗨\n第二行",
    });
    const end = await events.waitFor("run_end");

    expect(created.status).toBe(201);
    expect(created.data.title).toBe("嗨");
    expect(end.stopReason).toBe("end_turn");
    expect(events.received.map((e) => e.type)).toContain("text_delta");
    const detail = await api<SessionDetail>(
      server,
      "GET",
      `/sessions/${created.data.id}`,
    );
    expect(detail.data.summary.status).toBe("done");
    expect(detail.data.messages.map((m) => m.role)).toEqual([
      "user",
      "assistant",
    ]);
    const list = await api<SessionSummary[]>(server, "GET", "/sessions");
    expect(list.data.map((s) => s.id)).toEqual([created.data.id]);
  });

  it("在同一段對話接著問", async () => {
    const { server, events, requests } = await start({
      script: [textResponse("一"), textResponse("二")],
    });
    const { data } = await api<SessionSummary>(server, "POST", "/sessions", {
      text: "第一題",
    });
    await events.waitFor("run_end");

    await api(server, "POST", `/sessions/${data.id}/messages`, {
      text: "第二題",
    });
    await events.waitFor("run_end", () => requests.length === 2);

    expect(requests[1]?.messages.map((m) => m.content)).toEqual([
      "你是助手。",
      "第一題",
      "一",
      "第二題",
    ]);
  });

  it("空白訊息回 400", async () => {
    const { server } = await start();

    const { status } = await api(server, "POST", "/sessions", { text: "  " });

    expect(status).toBe(400);
  });
});

describe("web：工具確認", () => {
  it("需要同意的工具先送出確認，附上檔案內容；允許後才寫入", async () => {
    const { root, server, events } = await start({
      script: [writeCall, textResponse("好了")],
    });
    const { data } = await api<SessionSummary>(server, "POST", "/sessions", {
      text: "寫報告",
    });

    const { approval } = await events.waitFor("approval");
    const waiting = await api<SessionDetail>(
      server,
      "GET",
      `/sessions/${data.id}`,
    );
    expect(waiting.data.summary.status).toBe("waiting");
    expect(approval.preview).toEqual({
      path: "reports/a.md",
      before: null,
      after: "# 報告\n",
    });

    await decide(server, approval, "once");
    await events.waitFor("run_end");

    expect(readFileSync(join(root, "reports", "a.md"), "utf8")).toBe(
      "# 報告\n",
    );
  });

  it("「以後都允許」會把工具加進 harness.yaml 的 allow，並保留註解", async () => {
    const { root, server, events } = await start({
      script: [writeCall, textResponse("好了")],
    });
    await api(server, "POST", "/sessions", { text: "寫報告" });

    const { approval } = await events.waitFor("approval");
    await decide(server, approval, "always");
    await events.waitFor("run_end");

    const config = readFileSync(join(root, "harness.yaml"), "utf8");
    expect(config).toContain("allow: [ write_file ]");
    expect(config).toContain("# 預設先問");
    const info = await api<ProjectInfo>(server, "GET", "/project");
    expect(info.data.permissions.allow).toEqual(["write_file"]);
  });

  it("拒絕後不寫檔，並把拒絕告訴模型", async () => {
    const { root, server, events, requests } = await start({
      script: [writeCall, textResponse("那就不寫")],
    });
    await api(server, "POST", "/sessions", { text: "寫報告" });

    const { approval } = await events.waitFor("approval");
    await decide(server, approval, "deny");
    const result = await events.waitFor("tool_result");
    await events.waitFor("run_end");

    expect(result.result.isError).toBe(true);
    expect(requests[1]?.messages.at(-1)?.content).toContain("沒有同意");
    expect(() => readFileSync(join(root, "reports", "a.md"))).toThrow();
  });

  it("等待確認時按停止，對話狀態變成 stopped", async () => {
    const { server, events } = await start({ script: [writeCall] });
    const { data } = await api<SessionSummary>(server, "POST", "/sessions", {
      text: "寫報告",
    });
    await events.waitFor("approval");

    await api(server, "POST", `/sessions/${data.id}/stop`, {});
    await events.waitFor("run_stopped");

    const detail = await api<SessionDetail>(
      server,
      "GET",
      `/sessions/${data.id}`,
    );
    expect(detail.data.summary.status).toBe("stopped");
    expect(detail.data.approvals).toEqual([]);
  });

  it("處理過的確認再送一次回 404", async () => {
    const { server, events } = await start({
      script: [writeCall, textResponse("好")],
    });
    await api(server, "POST", "/sessions", { text: "寫報告" });
    const { approval } = await events.waitFor("approval");
    await decide(server, approval, "once");

    const again = await decide(server, approval, "once");

    expect(again).toBe(404);
  });
});

describe("web：錯誤與重試", () => {
  it("模型出錯時回報 run_error；重試不會重複使用者訊息", async () => {
    let calls = 0;
    const model: ModelFn = async function* (request) {
      calls += 1;
      if (calls === 1) throw new Error("無法連線到模型端點 http://x");
      expect(request.messages.filter((m) => m.role === "user")).toHaveLength(1);
      yield textResponse("回來了");
    };
    const { server, events } = await start({ model });
    const { data } = await api<SessionSummary>(server, "POST", "/sessions", {
      text: "嗨",
    });

    const failed = await events.waitFor("run_error");
    expect(failed.message).toContain("無法連線");
    const errored = await api<SessionDetail>(
      server,
      "GET",
      `/sessions/${data.id}`,
    );
    expect(errored.data.summary).toMatchObject({ status: "error" });

    await api(server, "POST", `/sessions/${data.id}/retry`, {});
    await events.waitFor("run_end");

    const detail = await api<SessionDetail>(
      server,
      "GET",
      `/sessions/${data.id}`,
    );
    expect(detail.data.messages.map((m) => m.role)).toEqual([
      "user",
      "assistant",
    ]);
  });

  it("工具回報的錯誤會算進 toolErrors", async () => {
    const { server, events } = await start({
      script: [
        toolResponse(["read_file", { path: "nope.txt" }]),
        textResponse("找不到檔案"),
      ],
    });
    const { data } = await api<SessionSummary>(server, "POST", "/sessions", {
      text: "讀檔",
    });
    await events.waitFor("run_end");

    const detail = await api<SessionDetail>(
      server,
      "GET",
      `/sessions/${data.id}`,
    );
    expect(detail.data.summary.toolErrors).toBe(1);
  });
});

describe("web：設定", () => {
  it("回傳專案資訊與 toolset", async () => {
    const { server } = await start();

    const { data } = await api<ProjectInfo>(server, "GET", "/project");

    expect(data.name).toBe("web_demo");
    expect(data.agents[0]).toMatchObject({
      name: "main",
      description: "測試用的助手",
      maxTurns: 20,
      maxTurnsSource: "default",
    });
    const files = data.toolsets.find((set) => set.name === "files");
    expect(files?.builtin).toBe(true);
    expect(files?.tools.find((t) => t.name === "write_file")).toMatchObject({
      needsApproval: true,
    });
  });

  it("儲存權限設定，空的規則不寫進檔案", async () => {
    const { root, server } = await start();

    const { status } = await api(server, "PUT", "/settings/permissions", {
      mode: "dontAsk",
      allow: [],
      ask: ["send_*"],
      deny: ["delete_*", "delete_*"],
    });

    expect(status).toBe(200);
    const config = readFileSync(join(root, "harness.yaml"), "utf8");
    expect(config).toContain("mode: dontAsk");
    expect(config).toContain("deny: [ delete_* ]");
    expect(config).not.toContain("allow");
  });

  it("權限模式不存在時回 400", async () => {
    const { server } = await start();

    const { status } = await api(server, "PUT", "/settings/permissions", {
      mode: "yolo",
      allow: [],
      ask: [],
      deny: [],
    });

    expect(status).toBe(400);
  });

  it("儲存模型設定，下一輪開始使用", async () => {
    const { root, server, events, requests } = await start({
      script: [textResponse("好")],
    });

    await api(server, "PUT", "/settings/model", {
      model: "my-model",
      maxTurns: 5,
    });
    await api(server, "POST", "/sessions", { text: "嗨" });
    await events.waitFor("run_end");

    expect(requests[0]?.model).toBe("my-model");
    expect(readFileSync(join(root, "harness.yaml"), "utf8")).toContain(
      "model: my-model",
    );
    const info = await api<ProjectInfo>(server, "GET", "/project");
    expect(info.data.agents[0]).toMatchObject({
      model: "my-model",
      modelSource: "config",
      maxTurns: 5,
    });
  });

  it("測試模型連線", async () => {
    const { server } = await start({ script: [textResponse("pong")] });

    const { data } = await api(server, "POST", "/settings/model/test", {});

    expect(data).toEqual({ ok: true, model: "test/model" });
  });
});

describe("web：安全性", () => {
  it("拒絕其他網站送來的請求", async () => {
    const { server } = await start();

    const { status } = await api(
      server,
      "POST",
      "/sessions",
      { text: "嗨" },
      { Origin: "https://evil.example" },
    );

    expect(status).toBe(403);
  });

  it("會改變狀態的請求必須是 JSON", async () => {
    const { server } = await start();

    const response = await fetch(`${server.url}/api/sessions`, {
      method: "POST",
      headers: { "Content-Type": "text/plain" },
      body: JSON.stringify({ text: "嗨" }),
    });

    expect(response.status).toBe(415);
  });

  it("拒絕 Host 不是本機的請求", async () => {
    const { server } = await start();

    const body = await rawGet(server, "/api/project", "evil.example");

    expect(body).toContain("Host");
  });
});

describe("web：前端檔案", () => {
  it("未知的路徑回 index.html，不能讀到資料夾外的檔案", async () => {
    const dir = tempDir();
    writeFileSync(join(dir, "index.html"), "<p>app</p>");
    writeFileSync(join(dir, "..", "secret.txt"), "secret");
    const { server } = await start({ static: dir });

    const page = await fetch(`${server.url}/s/123`);

    expect(await page.text()).toBe("<p>app</p>");
    expect(await rawGet(server, "/../secret.txt")).toBe("<p>app</p>");
    expect(await rawGet(server, "/%2e%2e/secret.txt")).toBe("<p>app</p>");
  });

  it("還沒打包前端時說明要先 build", async () => {
    const { server } = await start({ static: tempDir() });

    const response = await fetch(`${server.url}/`);

    expect(response.status).toBe(503);
    expect(await response.text()).toContain("npm run build");
  });
});

/** 不經過 fetch 的網址正規化，原樣送出路徑與 Host。 */
function rawGet(server: WebServer, path: string, host?: string) {
  const url = new URL(server.url);
  return new Promise<string>((done, fail) => {
    const options = {
      host: url.hostname,
      port: url.port,
      path,
      headers: host ? { Host: host } : {},
    };
    request(options, async (response) => {
      let text = "";
      for await (const chunk of response) text += chunk;
      done(text);
    })
      .on("error", fail)
      .end();
  });
}

async function decide(
  server: WebServer,
  approval: Approval,
  decision: string,
): Promise<number> {
  const { status } = await api(
    server,
    "POST",
    `/sessions/${approval.sessionId}/approvals/${approval.id}`,
    { decision },
  );
  return status;
}
