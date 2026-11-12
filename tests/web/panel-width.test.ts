/** 右側面板拖曳調整寬度的範圍限制。 */

import { describe, expect, it } from "vitest";
import {
  clampWidth,
  MIN_CHAT,
  MIN_PANEL,
} from "../../web/src/components/chat/panelWidth.ts";

describe("clampWidth", () => {
  it("keeps the width the user dragged to when both sides fit", () => {
    expect(clampWidth(600, 1400)).toBe(600);
  });

  it("never makes the panel narrower than its minimum", () => {
    expect(clampWidth(100, 1400)).toBe(MIN_PANEL);
  });

  it("leaves the conversation enough room", () => {
    expect(clampWidth(5000, 1400)).toBe(1400 - MIN_CHAT);
    expect(clampWidth(Number.POSITIVE_INFINITY, 1000)).toBe(1000 - MIN_CHAT);
  });

  it("falls back to the minimum when the window is too narrow for both", () => {
    expect(clampWidth(500, 500)).toBe(MIN_PANEL);
  });
});
