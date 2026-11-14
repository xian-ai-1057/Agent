/**
 * 多人帳號（Python 版，features.accounts）用到的判斷與檢查。
 *
 * 只放純函式，不碰 window 或路由，測試可以直接引用。TypeScript 版的
 * 伺服器沒有帳號，所有判斷都當成一位本機管理者。
 */

import type { CurrentUser, ProjectInfo } from "../../src/web/protocol.ts";
import type { AdminSection } from "./sections.ts";

/** 新密碼最少幾個字，和伺服器一致。 */
export const MIN_PASSWORD = 10;

export const ROLE_LABELS: Record<CurrentUser["role"], string> = {
  admin: "管理者",
  member: "成員",
};

/** 這個錯誤是不是「需要登入」（HTTP 401）。 */
export function isUnauthorized(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { status?: unknown }).status === 401
  );
}

/**
 * 登入失敗時顯示的文字：帳號密碼不對（400、401）一律同一句，不透露是
 * 哪一個錯。其他狀況（403 來源檢查失敗、429 失敗太多次…）顯示伺服器
 * 的說明，管理者才知道問題在哪。
 */
export function loginErrorMessage(error: unknown): string {
  const status = (error as { status?: unknown } | null)?.status;
  if (status === 400 || status === 401) {
    return "帳號或密碼不正確";
  }
  const message = (error as { message?: unknown } | null)?.message;
  return typeof message === "string" && message
    ? message
    : "登入失敗，請稍後再試";
}

/** 要登入的多人帳號模式（不是只在本機使用）。 */
export function accountsMode(project: ProjectInfo | undefined): boolean {
  return (
    project?.features?.accounts === true && project.auth?.mode === "accounts"
  );
}

/** 登入的人（多人帳號模式才有）。 */
export function currentUser(
  project: ProjectInfo | undefined,
): CurrentUser | undefined {
  return accountsMode(project) ? project?.auth?.user : undefined;
}

/**
 * 是不是一般成員。沒有帳號功能或只在本機使用時，使用者就是管理者，
 * 所有設定照舊。
 */
export function isMember(project: ProjectInfo | undefined): boolean {
  return (
    project?.features?.accounts === true && project.auth?.user.role === "member"
  );
}

/**
 * 能不能使用管理平台。沒有帳號功能（TypeScript 版）或只在本機使用時，
 * 使用者就是這台電腦的管理者。
 */
export function canAdmin(project: ProjectInfo | undefined): boolean {
  return project !== undefined && !isMember(project);
}

/** 管理平台有哪些分類，第一個是打開管理平台時的預設分類。 */
export function adminSections(
  project: ProjectInfo | undefined,
): AdminSection[] {
  const sections: AdminSection[] = ["model", "permissions"];
  if (accountsMode(project)) sections.push("members");
  return sections;
}

/** 頭像上的字：顯示名稱（沒有時帳號）的第一個字，英文字母轉大寫。 */
export function initialOf(user: Pick<CurrentUser, "displayName" | "username">) {
  const name = user.displayName.trim() || user.username.trim();
  const first = Array.from(name)[0] ?? "?";
  return first.toUpperCase();
}

export interface PasswordForm {
  current: string;
  next: string;
  confirm: string;
}

export type PasswordErrors = Partial<Record<keyof PasswordForm, string>>;

/** 修改密碼表單的檢查；沒有問題時回傳空物件。 */
export function passwordFormErrors(form: PasswordForm): PasswordErrors {
  const errors: PasswordErrors = {};
  if (!form.current) errors.current = "請輸入目前的密碼";
  if (Array.from(form.next).length < MIN_PASSWORD) {
    errors.next = `新密碼至少要 ${MIN_PASSWORD} 個字`;
  } else if (form.next === form.current) {
    errors.next = "新密碼不能和目前的密碼一樣";
  }
  if (!errors.next && form.confirm !== form.next) {
    errors.confirm = "兩次輸入的新密碼不一樣";
  }
  return errors;
}

/** 新增成員表單的檢查。 */
export function memberFormErrors(form: {
  username: string;
  displayName: string;
}): Partial<Record<"username" | "displayName", string>> {
  const errors: Partial<Record<"username" | "displayName", string>> = {};
  const username = form.username.trim();
  if (!username) errors.username = "請輸入帳號";
  else if (/\s/.test(username)) errors.username = "帳號不能有空白";
  if (!form.displayName.trim()) errors.displayName = "請輸入顯示名稱";
  return errors;
}
