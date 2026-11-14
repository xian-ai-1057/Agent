/**
 * 設定頁與管理平台的分類。不碰 window，測試可以直接引用。
 *
 * - 設定（網址 /settings/<分類>）：每個人看到的都一樣，只能看。
 * - 管理平台（網址 /admin/<分類>）：只有管理者能用，改模型連線、權限
 *   與成員。
 */

export type SettingsSection = "tools" | "agents";

export const SETTINGS_SECTIONS: SettingsSection[] = ["tools", "agents"];

export type AdminSection = "model" | "permissions" | "members" | "groups";

export const ADMIN_SECTIONS: AdminSection[] = [
  "model",
  "permissions",
  "members",
  "groups",
];
