/** 最簡單的路由：只用 history API，不需要額外套件。 */

import { useSyncExternalStore } from "react";
import { SETTINGS_SECTIONS, type SettingsSection } from "./sections.ts";

export type Route =
  | { page: "new" }
  | { page: "session"; id: string }
  | { page: "work" }
  | { page: "settings"; section: SettingsSection };

export type { SettingsSection };

const SECTIONS = SETTINGS_SECTIONS;
const listeners = new Set<() => void>();

window.addEventListener("popstate", () => {
  for (const listener of listeners) listener();
});

/** replace 為 true 時取代目前的紀錄（上一頁不會回到原本的網址）。 */
export function navigate(path: string, { replace = false } = {}): void {
  if (path === location.pathname) return;
  if (replace) history.replaceState(null, "", path);
  else history.pushState(null, "", path);
  for (const listener of listeners) listener();
}

/** 網址改變時通知（包含上一頁、下一頁），回傳取消訂閱的函式。 */
export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useRoute(): Route {
  const path = useSyncExternalStore(subscribe, () => location.pathname);
  return parse(path);
}

function parse(path: string): Route {
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "s" && parts[1]) return { page: "session", id: parts[1] };
  if (parts[0] === "work") return { page: "work" };
  if (parts[0] === "settings") {
    const section = SECTIONS.find((item) => item === parts[1]);
    return { page: "settings", section: section ?? "permissions" };
  }
  return { page: "new" };
}
