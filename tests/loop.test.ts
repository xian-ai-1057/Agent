import { setTimeout as sleep } from "node:timers/promises";
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { defineAgent } from "../src/agent.ts";
import { run, runStream } from "../src/loop.ts";
import { Session } from "../src/session.ts";
import { tool } from "../src/tool.ts";
import {
  fakeModel,
  lastContent,
  textResponse,
  toolResponse,
} from "./helpers.ts";

const add = tool({
  name: "add",
  description: "相加",
  parameters: z.object({ a: z.number(), b: z.number() }),
  execute: ({ a, b }) => a + b,
});

describe("run：基本流程", () => {
  it("模型沒有呼叫工具時直接回傳文字", async () => {
    const { model } = fakeModel([textResponse("你好")]);
    const agent = defineAgent({ name: "a", instructions: "你是助手" });

    const result = await run(agent, "嗨", { model });

    expect(result).toMatchObject({
      output: "你好",
      stopReason: "end_turn",
      turns: 1,
    });
  });

  it("送出 system prompt 與使用者訊息", async () => {
    const { model, requests } = fakeModel([textResponse("你好")]);
    const agent = defineAgent({ name: "a", instructions: "你是助手" });

    await run(agent, "嗨", { model });

    expect(requests[0]?.messages).toEqual([
      { role: "system", content: "你是助手" },
      { role: "user", content: "嗨" },
    ]);
  });

  it("執行工具並把結果送回模型", async () => {
    const { model, requests } = fakeModel([
      toolResponse(["add", { a: 1, b: 2 }]),
      textResponse("答案是 3"),
    ]);

    const result = await run(defineAgent({ name: "a", tools: [add] }), "1+2?", {
      model,
    });

    expect(requests[1]?.messages.at(-1)).toMatchObject({
      role: "tool",
      content: "3",
      tool_call_id: "call_0_add",
    });
    expect(result.output).toBe("答案是 3");
    expect(result.turns).toBe(2);
  });

  it("累計各輪的 token 用量", async () => {
    const { model } = fakeModel([
      toolResponse(["add", { a: 1, b: 2 }]),
      textResponse("3"),
    ]);

    const result = await run(defineAgent({ name: "a", tools: [add] }), "?", {
      model,
    });

    expect(result.usage).toEqual({ inputTokens: 20, outputTokens: 10 });
  });

  it("同一輪的多個工具呼叫同時執行，結果依呼叫順序排列", async () => {
    let running = 0;
    let peak = 0;
    const slow = tool({
      name: "slow",
      description: "慢速工具",
      parameters: z.object({ label: z.string(), ms: z.number() }),
      execute: async ({ label, ms }) => {
        running += 1;
        peak = Math.max(peak, running);
        await sleep(ms);
        running -= 1;
        return label;
      },
    });
    const { model, requests } = fakeModel([
      toolResponse(
        ["slow", { label: "甲", ms: 20 }],
        ["slow", { label: "乙", ms: 1 }],
      ),
      textResponse("完成"),
    ]);

    await run(defineAgent({ name: "a", tools: [slow] }), "go", { model });

    const outputs = requests[1]?.messages.slice(-2).map((m) => m.content);
    expect(peak).toBe(2);
    expect(outputs).toEqual(["甲", "乙"]);
  });

  it("使用 agent 設定的模型與參數", async () => {
    const { model, requests } = fakeModel([textResponse("好")]);
    const agent = defineAgent({
      name: "a",
      model: "openai/gpt-x",
      modelParams: { max_tokens: 99 },
    });

    await run(agent, "go", { model });

    expect(requests[0]).toMatchObject({
      model: "openai/gpt-x",
      params: { max_tokens: 99 },
    });
  });

  it("把 deps 傳給工具", async () => {
    const whoami = tool({
      name: "whoami",
      description: "目前的使用者",
      execute: (_args, context) => (context.deps as { user: string }).user,
    });
    const { model, requests } = fakeModel([
      toolResponse(["whoami", {}]),
      textResponse("好"),
    ]);

    await run(defineAgent({ name: "a", tools: [whoami] }), "我是誰", {
      model,
      deps: { user: "小明" },
    });

    expect(lastContent(requests, 1)).toBe("小明");
  });
});

describe("run：錯誤處理", () => {
  async function contentAfter(
    response: ReturnType<typeof toolResponse>,
    tools = [add],
  ) {
    const { model, requests } = fakeModel([response, textResponse("好")]);
    await run(defineAgent({ name: "a", tools }), "go", { model });
    return lastContent(requests, 1);
  }

  it("工具丟出的錯誤會回報給模型，對話不中斷", async () => {
    const explode = tool({
      name: "explode",
      description: "一定會失敗",
      execute: () => {
        throw new RangeError("資料庫連不上");
      },
    });

    const content = await contentAfter(toolResponse(["explode", {}]), [
      explode,
    ]);

    expect(content).toBe("[錯誤] RangeError: 資料庫連不上");
  });

  it("找不到工具時告訴模型有哪些工具可用", async () => {
    const content = await contentAfter(toolResponse(["nope", {}]));

    expect(content).toContain("沒有 nope 這個工具");
    expect(content).toContain("add");
  });

  it("參數型別錯誤時回報驗證結果", async () => {
    const content = await contentAfter(toolResponse(["add", { a: "x", b: 2 }]));

    expect(content).toContain("[錯誤] 參數不符合格式");
  });

  it("參數不是合法 JSON 時回報解析錯誤", async () => {
    const response = toolResponse(["add", {}]);
    (response.toolCalls[0] as { parseError?: string }).parseError = "壞掉了";

    const content = await contentAfter(response);

    expect(content).toContain("參數不是合法的 JSON：壞掉了");
  });
});

describe("run：停止條件", () => {
  it("達到 maxTurns 時停止", async () => {
    const call = toolResponse(["add", { a: 1, b: 1 }]);
    const { model } = fakeModel([call, call, call]);
    const agent = defineAgent({ name: "a", tools: [add], maxTurns: 2 });

    const result = await run(agent, "go", { model });

    expect(result).toMatchObject({ stopReason: "max_turns", turns: 2 });
    expect(result.messages.at(-1)?.role).toBe("tool");
  });

  it("finish_reason 為 length 時回報 max_tokens", async () => {
    const { model } = fakeModel([textResponse("被截斷", "length")]);

    const result = await run(defineAgent({ name: "a" }), "go", { model });

    expect(result.stopReason).toBe("max_tokens");
  });

  it("signal 已中斷時不再呼叫模型", async () => {
    const { model, requests } = fakeModel([textResponse("好")]);

    const pending = run(defineAgent({ name: "a" }), "go", {
      model,
      signal: AbortSignal.abort(),
    });

    await expect(pending).rejects.toThrow();
    expect(requests).toHaveLength(0);
  });

  it("沒有設定模型時說明怎麼設定", async () => {
    const { model } = fakeModel([textResponse("好")]);
    const saved = process.env.HARNESS_MODEL;
    delete process.env.HARNESS_MODEL;

    const pending = run(defineAgent({ name: "a" }), "go", { model });

    await expect(pending).rejects.toThrow(/HARNESS_MODEL/);
    if (saved !== undefined) process.env.HARNESS_MODEL = saved;
  });
});

describe("run：session", () => {
  it("傳入同一個 session 可以延續對話", async () => {
    const { model, requests } = fakeModel([
      textResponse("第一次"),
      textResponse("第二次"),
    ]);
    const session = new Session();
    const agent = defineAgent({ name: "a" });

    await run(agent, "一", { model, session });
    await run(agent, "二", { model, session });

    expect(requests[1]?.messages.map((m) => m.role)).toEqual([
      "user",
      "assistant",
      "user",
    ]);
  });

  it("替上次中斷而沒有結果的工具呼叫補上結果", async () => {
    const interrupted = toolResponse(["add", { a: 1, b: 2 }]);
    const session = new Session({
      messages: [{ role: "user", content: "1+2?" }, interrupted.message],
    });
    const { model, requests } = fakeModel([textResponse("好")]);

    await run(defineAgent({ name: "a", tools: [add] }), "還在嗎", {
      model,
      session,
    });

    const sent = requests[0]?.messages ?? [];
    expect(sent[2]).toMatchObject({ role: "tool", tool_call_id: "call_0_add" });
    expect(sent[2]?.content).toContain("中斷");
    expect(sent[3]).toEqual({ role: "user", content: "還在嗎" });
  });

  it("userInput 為 null 時不加新訊息，接著最後一則使用者訊息跑", async () => {
    const session = new Session({
      messages: [{ role: "user", content: "嗨" }],
    });
    const { model, requests } = fakeModel([textResponse("你好")]);

    await run(defineAgent({ name: "a" }), null, { model, session });

    expect(requests[0]?.messages).toEqual([{ role: "user", content: "嗨" }]);
    expect(session.messages.map((m) => m.role)).toEqual(["user", "assistant"]);
  });

  it("userInput 為 null 但最後一則是 assistant 時丟出錯誤", async () => {
    const session = new Session({
      messages: [
        { role: "user", content: "嗨" },
        { role: "assistant", content: "你好" },
      ],
    });
    const { model } = fakeModel([]);

    await expect(
      run(defineAgent({ name: "a" }), null, { model, session }),
    ).rejects.toThrow("沒有可以接續");
  });
});

describe("runStream", () => {
  it("依序產生事件，最後一個是 run_end", async () => {
    const { model } = fakeModel([
      toolResponse(["add", { a: 1, b: 2 }]),
      textResponse("答案是 3"),
    ]);
    const agent = defineAgent({ name: "a", tools: [add] });

    const types = [];
    for await (const event of runStream(agent, "1+2?", { model })) {
      types.push(event.type);
    }

    expect(types).toEqual([
      "message",
      "tool_call",
      "tool_result",
      "text_delta",
      "message",
      "run_end",
    ]);
  });
});

describe("defineAgent", () => {
  it("工具名稱重複時丟出錯誤", () => {
    expect(() => defineAgent({ name: "a", tools: [add, add] })).toThrow(
      /重複的工具名稱：add/,
    );
  });
});
