/** 設定頁的分類（網址 /settings/<分類>）。不碰 window，測試可以直接引用。 */

export type SettingsSection =
  | "model"
  | "permissions"
  | "tools"
  | "agents"
  | "members";

export const SETTINGS_SECTIONS: SettingsSection[] = [
  "model",
  "permissions",
  "tools",
  "agents",
  "members",
];
