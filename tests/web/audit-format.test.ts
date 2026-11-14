/** 管理平台 → 稽核紀錄的純函式：查詢條件與每一筆的說明。 */

import { describe, expect, it } from "vitest";
import type { AuditEntry } from "../../src/web/protocol.ts";
import {
  auditQuery,
  describeDetail,
  EMPTY_FORM,
  formatTime,
  toFilters,
} from "../../web/src/components/admin/auditFormat.ts";

function entry(action: string, detail: Record<string, unknown>): AuditEntry {
  return {
    seq: 1,
    at: 0,
    actor: { id: "u1", username: "boss" },
    action,
    label: action,
    target: "",
    detail,
    address: "",
  };
}

describe("toFilters", () => {
  it("空的表單沒有條件", () => {
    expect(toFilters(EMPTY_FORM)).toEqual({});
  });

  it("日期用當地時間，「到」包含當天整天", () => {
    const filters = toFilters({
      category: "auth",
      actor: " alice ",
      from: "2026-11-01",
      to: "2026-11-14",
    });
    expect(filters.category).toBe("auth");
    expect(filters.actor).toBe("alice");
    expect(filters.since).toBe(new Date(2026, 10, 1).getTime());
    expect(filters.until).toBe(new Date(2026, 10, 15).getTime());
  });

  it("格式不對的日期略過", () => {
    expect(toFilters({ ...EMPTY_FORM, from: "11/01" }).since).toBeUndefined();
  });
});

describe("auditQuery", () => {
  it("沒有值的參數不放", () => {
    expect(
      auditQuery({ actor: "a b" }, { before: undefined, format: "csv" }),
    ).toBe("actor=a+b&format=csv");
  });
});

describe("describeDetail", () => {
  it("成員的變更列出改之前與之後", () => {
    expect(
      describeDetail(
        entry("member.update", {
          changes: { role: ["member", "admin"], disabled: [false, true] },
        }),
      ),
    ).toBe("角色：成員 → 管理者；狀態：啟用 → 停用");
  });

  it("工具核准顯示決定與參數", () => {
    expect(
      describeDetail(
        entry("approval.decide", {
          decision: "session",
          arguments: '{"path": "a.md"}',
        }),
      ),
    ).toBe('這段對話都允許 · {"path": "a.md"}');
  });

  it("登入失敗顯示原因", () => {
    expect(
      describeDetail(entry("auth.login_failed", { reason: "throttled" })),
    ).toBe("失敗太多次，暫時不能登入");
  });

  it("群組的變更列出改了哪些欄位", () => {
    expect(
      describeDetail(
        entry("group.update", { changes: { members: [], agents: null } }),
      ),
    ).toBe("修改了：成員、Agent");
  });

  it("模型連線只說換了金鑰，不顯示金鑰", () => {
    expect(
      describeDetail(
        entry("settings.connection", {
          provider: "endpoint",
          model: "gpt-x",
          apiKeyChanged: true,
        }),
      ),
    ).toBe("endpoint · gpt-x · 換了金鑰");
  });

  it("其他動作沒有說明", () => {
    expect(describeDetail(entry("auth.login", {}))).toBe("");
  });
});

describe("formatTime", () => {
  it("年月日與時分秒", () => {
    expect(formatTime(new Date(2026, 10, 14, 9, 5, 3).getTime())).toBe(
      "2026/11/14 09:05:03",
    );
  });
});
