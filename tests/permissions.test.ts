import { describe, expect, it } from "vitest";
import { z } from "zod";
import { defineAgent } from "../src/agent.ts";
import { allow, Hooks } from "../src/hooks.ts";
import { run } from "../src/loop.ts";
import {
  checkPermission,
  definePermissions,
  type Permissions,
} from "../src/permissions.ts";
import { tool } from "../src/tool.ts";
import {
  fakeModel,
  lastContent,
  textResponse,
  toolResponse,
} from "./helpers.ts";

function riskyTool() {
  const sent: string[] = [];
  const sendMail = tool({
    name: "send_mail",
    description: "寄信",
    parameters: z.object({ to: z.string() }),
    needsApproval: true,
    execute: ({ to }) => {
      sent.push(to);
      return "已寄出";
    },
  });
  return { sendMail, sent };
}

const safeTool = tool({
  name: "lookup",
  description: "查詢",
  execute: () => "",
});

describe("checkPermission", () => {
  it.each([
    ["ask", "ask"],
    ["bypass", "allow"],
    ["dontAsk", "deny"],
  ] as const)("%s 模式下，需要同意的工具得到 %s", (mode, expected) => {
    const { sendMail } = riskyTool();

    expect(checkPermission(definePermissions({ mode }), sendMail)).toBe(
      expected,
    );
  });

  it.each(["ask", "bypass", "dontAsk"] as const)(
    "%s 模式下，一般工具直接允許",
    (mode) => {
      expect(checkPermission(definePermissions({ mode }), safeTool)).toBe(
        "allow",
      );
    },
  );

  it("allow 規則讓需要同意的工具不必詢問", () => {
    const { sendMail } = riskyTool();
    const rules = definePermissions({ allow: ["send_*"] });

    expect(checkPermission(rules, sendMail)).toBe("allow");
  });

  it("deny 規則優先於 allow 規則與 bypass 模式", () => {
    const { sendMail } = riskyTool();
    const rules = definePermissions({
      mode: "bypass",
      allow: ["send_mail"],
      deny: ["send_*"],
    });

    expect(checkPermission(rules, sendMail)).toBe("deny");
  });

  it("ask 規則讓一般工具也要先詢問", () => {
    const rules = definePermissions({ ask: ["look??"] });

    expect(checkPermission(rules, safeTool)).toBe("ask");
  });

  it("樣式必須完整符合名稱", () => {
    const rules = definePermissions({ deny: ["look"] });

    expect(checkPermission(rules, safeTool)).toBe("allow");
  });

  it("不認得的模式會丟出錯誤", () => {
    expect(() => definePermissions({ mode: "yolo" as never })).toThrow(
      /權限模式/,
    );
  });
});

describe("權限在主迴圈裡的行為", () => {
  async function runWith(permissions: Partial<Permissions>, hooks?: Hooks) {
    const { sendMail, sent } = riskyTool();
    const { model, requests } = fakeModel([
      toolResponse(["send_mail", { to: "a@b.c" }]),
      textResponse("完成"),
    ]);
    await run(defineAgent({ name: "a", tools: [sendMail] }), "go", {
      model,
      hooks,
      permissions: definePermissions(permissions),
    });
    return { content: lastContent(requests, 1), sent };
  }

  it("使用者同意時執行工具", async () => {
    const { content, sent } = await runWith({ approver: async () => true });

    expect(content).toBe("已寄出");
    expect(sent).toEqual(["a@b.c"]);
  });

  it("使用者不同意時不執行，並告訴模型不要重試", async () => {
    const { content, sent } = await runWith({ approver: async () => false });

    expect(content).toContain("使用者沒有同意");
    expect(sent).toEqual([]);
  });

  it("沒有設定 approver 時視為不同意", async () => {
    const { content, sent } = await runWith({});

    expect(content).toContain("使用者沒有同意");
    expect(sent).toEqual([]);
  });

  it("hook 回傳 allow 時略過詢問", async () => {
    const hooks = new Hooks().on("preToolUse", () => allow());

    const { content } = await runWith({}, hooks);

    expect(content).toBe("已寄出");
  });

  it("hook 的 allow 不能推翻 deny 規則", async () => {
    const hooks = new Hooks().on("preToolUse", () => allow());

    const { content, sent } = await runWith({ deny: ["send_mail"] }, hooks);

    expect(content).toContain("權限設定不允許");
    expect(sent).toEqual([]);
  });
});
