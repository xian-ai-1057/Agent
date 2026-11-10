import { describe, expect, it } from "vitest";
import { z } from "zod";
import {
  formatOutput,
  isToolLike,
  ToolError,
  tool,
  toToolSchema,
} from "../src/tool.ts";
import { contextIn } from "./helpers.ts";

const add = tool({
  name: "add",
  description: "把兩個整數相加。",
  parameters: z.object({
    a: z.number().int().describe("第一個數"),
    b: z.number().int().default(2).describe("第二個數"),
  }),
  execute: ({ a, b }) => a + b,
});

describe("tool：zod 參數", () => {
  it("把 zod schema 轉成 JSON schema", () => {
    expect(add.parameters).toMatchObject({
      type: "object",
      properties: {
        a: { type: "integer", description: "第一個數" },
        b: { type: "integer", default: 2 },
      },
      required: ["a"],
    });
    expect(add.parameters).not.toHaveProperty("$schema");
  });

  it("驗證參數並補上預設值", () => {
    expect(add.parse({ a: 1 })).toEqual({ a: 1, b: 2 });
  });

  it("參數不合格時丟出說明原因的 ToolError", () => {
    expect(() => add.parse({ a: "不是數字" })).toThrow(ToolError);
    expect(() => add.parse({})).toThrow(/參數不符合格式/);
  });

  it.each([
    ["字串", z.string(), "string"],
    ["布林", z.boolean(), "boolean"],
    ["陣列", z.array(z.string()), "array"],
    ["列舉", z.enum(["a", "b"]), "string"],
  ])("支援%s型別", (_label, schema, expected) => {
    const target = tool({
      name: "t",
      description: "d",
      parameters: z.object({ value: schema }),
      execute: () => "",
    });

    expect(target.parameters).toMatchObject({
      properties: { value: { type: expected } },
    });
  });
});

describe("tool：JSON schema 參數", () => {
  const plain = tool({
    name: "echo",
    description: "回傳輸入。",
    parameters: {
      type: "object",
      properties: { text: { type: "string" } },
      required: ["text"],
    },
    execute: ({ text }: { text: string }) => text,
  });

  it("原樣使用 JSON schema 且不驗證參數", () => {
    expect(plain.parameters.required).toEqual(["text"]);
    expect(plain.parse({ anything: 1 })).toEqual({ anything: 1 });
  });

  it("沒有參數時給空的 object schema", () => {
    const ping = tool({ name: "ping", description: "測試", execute: () => "" });

    expect(ping.parameters).toEqual({ type: "object", properties: {} });
  });
});

describe("tool：其他", () => {
  it("needsApproval 預設為 false", () => {
    expect(add.needsApproval).toBe(false);
  });

  it("execute 會收到 context", async () => {
    const whoami = tool({
      name: "whoami",
      description: "回報 agent 名稱",
      execute: (_args, context) => context.agentName,
    });

    expect(await whoami.execute({}, contextIn("/tmp"))).toBe("test");
  });

  it("缺少 name 或 execute 時丟出錯誤", () => {
    expect(() =>
      tool({ name: "", description: "", execute: () => "" }),
    ).toThrow(TypeError);
  });

  it("toToolSchema 產生 OpenAI function 格式", () => {
    expect(toToolSchema(add)).toMatchObject({
      type: "function",
      function: { name: "add", description: "把兩個整數相加。" },
    });
  });

  it("isToolLike 只接受有 name 與 execute 的物件", () => {
    expect(isToolLike(add)).toBe(true);
    expect(isToolLike({ name: "x" })).toBe(false);
    expect(isToolLike(null)).toBe(false);
  });
});

describe("formatOutput", () => {
  it.each([
    ["文字", "文字"],
    [{ 名稱: "甲" }, '{"名稱":"甲"}'],
    [[1, 2], "[1,2]"],
    [0, "0"],
    [false, "false"],
  ])("把 %j 轉成文字", (value, expected) => {
    expect(formatOutput(value)).toBe(expected);
  });

  it.each([undefined, null, ""])("%j 不會變成空字串", (value) => {
    expect(formatOutput(value)).not.toBe("");
  });
});
