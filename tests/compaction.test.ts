import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { defineAgent } from "../src/agent.ts";
import { isRecord, lastMessage, modelHistory } from "../src/compaction.ts";
import { run } from "../src/loop.ts";
import { Session } from "../src/session.ts";
import type { Message } from "../src/types.ts";
import { FIXTURE_DIR, fakeModel, textResponse } from "./helpers.ts";

/** Python 版產生的壓縮紀錄（python/tests/test_compaction.py 維護）。 */
function compactedSession(): Message[] {
  return readFileSync(join(FIXTURE_DIR, "compacted-session.jsonl"), "utf8")
    .split("\n")
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line) as Message);
}

describe("modelHistory：讀取 Python 版的壓縮紀錄", () => {
  it("推算結果與 Python 版相同", () => {
    const expected = JSON.parse(
      readFileSync(join(FIXTURE_DIR, "compacted-history.json"), "utf8"),
    );

    expect(modelHistory(compactedSession())).toEqual(expected);
  });

  it("沒有壓縮紀錄時原樣回傳", () => {
    const messages: Message[] = [
      { role: "user", content: "嗨" },
      { role: "assistant", content: "你好" },
    ];

    expect(modelHistory(messages)).toEqual(messages);
  });

  it("拿掉找不到對應呼叫的工具結果", () => {
    const messages: Message[] = [
      { role: "tool", tool_call_id: "gone", content: "x" },
      { role: "user", content: "嗨" },
    ];

    expect(modelHistory(messages)).toEqual([{ role: "user", content: "嗨" }]);
  });

  it("lastMessage 略過框架的紀錄", () => {
    const messages = compactedSession();
    messages.push({
      role: "harness",
      type: "prune",
      results: {},
    } as unknown as Message);

    expect(isRecord(messages.at(-1) as Message)).toBe(true);
    expect(lastMessage(messages)).toEqual({
      role: "assistant",
      content: "讀完了",
    });
  });
});

describe("run：接續壓縮過的對話", () => {
  it("送給模型的是壓縮後的歷史", async () => {
    const { model, requests } = fakeModel([textResponse("好")]);
    const agent = defineAgent({ name: "a", instructions: "你是助手" });
    const session = new Session({ messages: compactedSession() });

    await run(agent, "繼續", { model, session });

    const sent = requests[0]?.messages ?? [];
    expect(sent[0]).toEqual({ role: "system", content: "你是助手" });
    expect(sent[1]?.content).toContain("<compacted-summary>");
    expect(sent.some((m) => m.content === "舊回答")).toBe(false);
    expect(sent.some((m) => (m.role as string) === "harness")).toBe(false);
    expect(sent.at(-1)).toEqual({ role: "user", content: "繼續" });
  });
});
