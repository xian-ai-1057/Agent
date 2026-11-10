import { describe, expect, it } from "vitest";
import { z } from "zod";
import { defineAgent } from "../src/agent.ts";
import { allow, deny, Hooks } from "../src/hooks.ts";
import { run } from "../src/loop.ts";
import { tool } from "../src/tool.ts";
import {
  fakeModel,
  lastContent,
  textResponse,
  toolResponse,
} from "./helpers.ts";

function setup() {
  const executed: number[][] = [];
  const add = tool({
    name: "add",
    description: "相加",
    parameters: z.object({ a: z.number(), b: z.number() }),
    execute: ({ a, b }) => {
      executed.push([a, b]);
      return a + b;
    },
  });
  const fake = fakeModel([
    toolResponse(["add", { a: 1, b: 2 }]),
    textResponse("完成"),
  ]);
  return { executed, agent: defineAgent({ name: "a", tools: [add] }), ...fake };
}

describe("Hooks", () => {
  it("preToolUse 回傳 deny 時不執行工具，原因送回模型", async () => {
    const { executed, agent, model, requests } = setup();
    const hooks = new Hooks().on("preToolUse", () => deny("不准算"));

    await run(agent, "go", { model, hooks });

    expect(executed).toEqual([]);
    expect(lastContent(requests, 1)).toContain("不准算");
  });

  it("preToolUse 可以改寫參數", async () => {
    const { agent, model, requests } = setup();
    const hooks = new Hooks().on("preToolUse", (call) => {
      call.arguments.b = 100;
    });

    await run(agent, "go", { model, hooks });

    expect(lastContent(requests, 1)).toBe("101");
  });

  it("postToolUse 可以改寫結果（async 處理函式）", async () => {
    const { agent, model, requests } = setup();
    const hooks = new Hooks().on("postToolUse", async (_call, result) => {
      result.output = "[已遮蔽]";
    });

    await run(agent, "go", { model, hooks });

    expect(lastContent(requests, 1)).toBe("[已遮蔽]");
  });

  it("被拒絕的工具呼叫不會觸發 postToolUse", async () => {
    const { agent, model } = setup();
    const seen: string[] = [];
    const hooks = new Hooks()
      .on("preToolUse", () => deny("不行"))
      .on("postToolUse", (call) => void seen.push(call.name));

    await run(agent, "go", { model, hooks });

    expect(seen).toEqual([]);
  });

  it("runStart 與 runEnd 各觸發一次", async () => {
    const { model } = fakeModel([textResponse("你好")]);
    const seen: string[] = [];
    const hooks = new Hooks()
      .on("runStart", (text) => void seen.push(`start:${text}`))
      .on("runEnd", (result) => void seen.push(`end:${result.output}`));

    await run(defineAgent({ name: "a" }), "嗨", { model, hooks });

    expect(seen).toEqual(["start:嗨", "end:你好"]);
  });

  it("allow 與 deny 產生對應的 Decision", () => {
    expect(allow()).toEqual({ action: "allow" });
    expect(deny("原因")).toEqual({ action: "deny", reason: "原因" });
  });
});
