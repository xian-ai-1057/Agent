/**
 * 右側面板的寬度：拖曳對話區與面板之間的分隔線可以調整。
 *
 * 寬度記在 localStorage，下次打開面板時沿用；讀寫失敗（例如無痕視窗）
 * 時就只在這次有效。沒有調整過時是 null，交給 CSS 的預設寬度。
 */

const KEY = "harness-panel-width";
/** 面板最窄的寬度，和 CSS 的 min-width 一致。 */
export const MIN_PANEL = 340;
/** 拖曳時對話區至少留下的寬度。 */
export const MIN_CHAT = 360;
/** 用鍵盤（方向鍵）調整時每次移動的距離。 */
export const KEY_STEP = 24;

/**
 * 把寬度限制在面板與對話區都還能用的範圍內。
 *
 * Args:
 *   width: 想要的寬度。
 *   available: 對話區加面板可用的總寬度。
 */
export function clampWidth(width: number, available: number): number {
  const max = Math.max(MIN_PANEL, available - MIN_CHAT);
  return Math.round(Math.min(Math.max(width, MIN_PANEL), max));
}

export function loadPanelWidth(): number | null {
  try {
    const value = Number(localStorage.getItem(KEY));
    return Number.isFinite(value) && value >= MIN_PANEL ? value : null;
  } catch {
    return null;
  }
}

export function savePanelWidth(width: number | null): void {
  try {
    if (width === null) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, String(width));
  } catch {
    // 存不了也沒關係，這次仍然有效。
  }
}
