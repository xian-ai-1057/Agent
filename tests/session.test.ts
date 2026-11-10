import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  createSession,
  latestSession,
  listSessionIds,
  loadSession,
  Session,
} from "../src/session.ts";
import { tempDir } from "./helpers.ts";

describe("Session", () => {
  it("每則訊息寫成一行 JSON", () => {
    const session = createSession(tempDir());

    session.append({ role: "user", content: "你好" });
    session.append({ role: "assistant", content: "哈囉" });

    const lines = readFileSync(session.path ?? "", "utf8")
      .trim()
      .split("\n");
    expect(lines).toHaveLength(2);
    expect(JSON.parse(lines[0] ?? "")).toEqual({
      role: "user",
      content: "你好",
    });
  });

  it("loadSession 還原訊息", () => {
    const directory = tempDir();
    const session = createSession(directory);
    session.append({ role: "user", content: "你好" });

    const restored = loadSession(directory, session.id);

    expect(restored.id).toBe(session.id);
    expect(restored.messages).toEqual([{ role: "user", content: "你好" }]);
  });

  it("還原後繼續寫入同一個檔案", () => {
    const directory = tempDir();
    const session = createSession(directory);
    session.append({ role: "user", content: "一" });

    loadSession(directory, session.id).append({ role: "user", content: "二" });

    expect(loadSession(directory, session.id).messages).toHaveLength(2);
    expect(readdirSync(directory)).toHaveLength(1);
  });

  it("找不到 session 時丟出錯誤", () => {
    expect(() => loadSession(tempDir(), "nope")).toThrow(/找不到 session/);
  });

  it("latestSession 回傳最新的一個", () => {
    const directory = tempDir();
    for (const id of ["20260101-000000-aaaaaa", "20260201-000000-bbbbbb"]) {
      writeFileSync(join(directory, `${id}.jsonl`), "");
    }

    expect(latestSession(directory)?.id).toBe("20260201-000000-bbbbbb");
  });

  it("資料夾不存在時沒有任何 session", () => {
    const missing = join(tempDir(), "missing");

    expect(listSessionIds(missing)).toEqual([]);
    expect(latestSession(missing)).toBeUndefined();
  });

  it("沒有路徑的 session 只存在記憶體", () => {
    const session = new Session();

    session.append({ role: "user", content: "你好" });

    expect(session.path).toBeUndefined();
    expect(session.messages).toHaveLength(1);
  });
});
