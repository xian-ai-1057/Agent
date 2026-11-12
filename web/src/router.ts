/** 最簡單的路由：只用 history API，不需要額外套件。 */

import { useSyncExternalStore } from "react";

export type Route =
  | { page: "new" }
  | { page: "session"; id: string }
  | { page: "work" }
  | { page: "settings"; section: SettingsSection };

export type SettingsSection = "model" | "permissions" | "tools" | "agents";

const SECTIONS: SettingsSection[] = ["model", "permissions", "tools", "agents"];
const listeners = new Set<() => void>();

window.addEventListener("popstate", () => {
  for (const listener of listeners) listener();
});

export function navigate(path: string): void {
  if (path === location.pathname) return;
  history.pushState(null, "", path);
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
