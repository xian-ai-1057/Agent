import { describe, expect, it } from "vitest";
import { resultPreview } from "../web/src/transcript.ts";

describe("resultPreview", () => {
  it("把排版過的 JSON 物件壓成一行", () => {
    const text = JSON.stringify({ month: "2026-10", total: 192000 }, null, 2);
    expect(resultPreview(text)).toBe('{"month":"2026-10","total":192000}');
  });

  it("把排版過的 JSON 陣列壓成一行", () => {
    const text = JSON.stringify(["2026-09", "2026-10"], null, 2);
    expect(resultPreview(text)).toBe('["2026-09","2026-10"]');
  });

  it("一般文字取第一個非空白的行", () => {
    expect(resultPreview("\n\n  # 2026-10 銷售報告\n\n內容")).toBe(
      "# 2026-10 銷售報告",
    );
  });

  it("看起來像 JSON 但解析不了時當成一般文字", () => {
    expect(resultPreview("{ 不是 JSON\n第二行")).toBe("{ 不是 JSON");
  });

  it("太長時截斷", () => {
    const text = JSON.stringify({ items: "字".repeat(200) }, null, 2);
    const preview = resultPreview(text, 20);
    expect(preview).toHaveLength(21);
    expect(preview.endsWith("…")).toBe(true);
  });

  it("沒有結果時回傳空字串", () => {
    expect(resultPreview(undefined)).toBe("");
    expect(resultPreview("   ")).toBe("");
  });
});
