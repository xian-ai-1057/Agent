/**
 * 外觀：跟著系統、淺色或深色。
 *
 * 淺色、深色時在 <html> 加上 data-theme，CSS 依它選顏色；跟著系統時拿掉，
 * 由 prefers-color-scheme 決定。選擇記在 localStorage，讀寫失敗（例如
 * 無痕視窗）時就只在這次有效。
 */

export type ThemePreference = "system" | "light" | "dark";

const KEY = "harness-theme";

export function getThemePreference(): ThemePreference {
  try {
    const value = localStorage.getItem(KEY);
    if (value === "light" || value === "dark") return value;
  } catch {
    // 讀不到時跟著系統。
  }
  return "system";
}

function apply(preference: ThemePreference): void {
  const root = document.documentElement;
  if (preference === "system") delete root.dataset.theme;
  else root.dataset.theme = preference;
}

let switching: ReturnType<typeof setTimeout> | undefined;

export function setThemePreference(preference: ThemePreference): void {
  // 切換時讓顏色在 200ms 內換過去；使用者要求減少動態效果時直接換。
  const root = document.documentElement;
  if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
    root.classList.add("theme-switching");
    clearTimeout(switching);
    switching = setTimeout(() => root.classList.remove("theme-switching"), 260);
  }
  apply(preference);
  try {
    if (preference === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, preference);
  } catch {
    // 存不了也沒關係，這次仍然有效。
  }
}

/** 網頁一開始時套用上次的選擇，避免先閃一下另一種顏色。 */
export function applyStoredTheme(): void {
  apply(getThemePreference());
}
