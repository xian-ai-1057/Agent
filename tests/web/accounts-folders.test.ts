/** 多人帳號與工作資料夾的純函式：401 判斷、表單檢查、管理平台、資料夾。 */

import { describe, expect, it } from "vitest";
import type {
  FolderInfo,
  ProjectInfo,
  ServerFeatures,
} from "../../src/web/protocol.ts";
import { ApiError } from "../../web/src/api.ts";
import {
  accountsMode,
  adminSections,
  canAdmin,
  currentUser,
  initialOf,
  isMember,
  isUnauthorized,
  loginErrorMessage,
  memberFormErrors,
  passwordFormErrors,
} from "../../web/src/auth.ts";
import {
  activeFolder,
  defaultFolderId,
  folderLabel,
  sortFolders,
  withoutFolder,
} from "../../web/src/folders.ts";

const FOLDERS: FolderInfo[] = [
  { id: "shared:docs", name: "共用文件", kind: "shared", writable: false },
  { id: "private", name: "我的檔案", kind: "private", writable: true },
  { id: "shared:team", name: "團隊", kind: "shared", writable: true },
];

function project(
  features: Partial<ServerFeatures> = {},
  extra: Partial<ProjectInfo> = {},
): ProjectInfo {
  return {
    features: { search: false, sessionAdmin: false, files: false, ...features },
    ...extra,
  } as ProjectInfo;
}

const user = (role: "admin" | "member") => ({
  id: "u1",
  username: "amy",
  displayName: "Amy",
  role,
});

const accounts = (role: "admin" | "member") =>
  project(
    { accounts: true, folders: true },
    { auth: { mode: "accounts", user: user(role) }, folders: FOLDERS },
  );

describe("isUnauthorized", () => {
  it("只有 401 算要登入", () => {
    expect(isUnauthorized(new ApiError(401, "需要登入"))).toBe(true);
    expect(isUnauthorized(new ApiError(403, "沒有權限"))).toBe(false);
    expect(isUnauthorized(new Error("網路錯誤"))).toBe(false);
    expect(isUnauthorized(undefined)).toBe(false);
  });
});

describe("loginErrorMessage", () => {
  it("帳號或密碼不對時不透露是哪一個錯", () => {
    expect(loginErrorMessage(new ApiError(401, "no such user"))).toBe(
      "帳號或密碼不正確",
    );
    expect(loginErrorMessage(new ApiError(400, "x"))).toBe("帳號或密碼不正確");
  });

  it("其他錯誤顯示伺服器的說明", () => {
    expect(loginErrorMessage(new ApiError(429, "嘗試太多次"))).toBe(
      "嘗試太多次",
    );
    // 403 是來源檢查失敗（例如反向代理設定不對），不是密碼錯。
    expect(loginErrorMessage(new ApiError(403, "來源不符"))).toBe("來源不符");
    expect(loginErrorMessage(new ApiError(500, "伺服器出錯"))).toBe(
      "伺服器出錯",
    );
    expect(loginErrorMessage({})).toBe("登入失敗，請稍後再試");
  });
});

describe("帳號模式與角色", () => {
  it("TypeScript 版沒有帳號：當成管理者，可以用管理平台", () => {
    const ts = project();
    expect(accountsMode(ts)).toBe(false);
    expect(isMember(ts)).toBe(false);
    expect(currentUser(ts)).toBeUndefined();
    expect(canAdmin(ts)).toBe(true);
    expect(adminSections(ts)).toEqual(["model", "permissions"]);
  });

  it("還沒讀到專案時不能用管理平台", () => {
    expect(canAdmin(undefined)).toBe(false);
  });

  it("只在本機使用時不顯示使用者與成員管理", () => {
    const local = project(
      { accounts: true },
      { auth: { mode: "local", user: user("admin") } },
    );
    expect(currentUser(local)).toBeUndefined();
    expect(canAdmin(local)).toBe(true);
    expect(adminSections(local)).not.toContain("members");
  });

  it("管理者的管理平台多了成員管理", () => {
    const admin = accounts("admin");
    expect(currentUser(admin)?.username).toBe("amy");
    expect(canAdmin(admin)).toBe(true);
    expect(adminSections(admin)).toEqual(["model", "permissions", "members"]);
  });

  it("成員不能用管理平台", () => {
    const member = accounts("member");
    expect(isMember(member)).toBe(true);
    expect(canAdmin(member)).toBe(false);
  });

  it("頭像用名稱的第一個字", () => {
    expect(initialOf({ displayName: "amy", username: "a" })).toBe("A");
    expect(initialOf({ displayName: "王小明", username: "ming" })).toBe("王");
    expect(initialOf({ displayName: " ", username: "bob" })).toBe("B");
  });
});

describe("passwordFormErrors", () => {
  const ok = { current: "old-password", next: "0123456789", confirm: "" };

  it("檢查目前密碼、長度與兩次輸入", () => {
    expect(
      passwordFormErrors({ current: "", next: "short", confirm: "short" }),
    ).toEqual({
      current: "請輸入目前的密碼",
      next: "新密碼至少要 10 個字",
    });
    expect(passwordFormErrors({ ...ok, confirm: "0123456788" })).toEqual({
      confirm: "兩次輸入的新密碼不一樣",
    });
    expect(passwordFormErrors({ ...ok, confirm: ok.next })).toEqual({});
  });

  it("新密碼不能和目前的一樣", () => {
    const same = "0123456789";
    expect(
      passwordFormErrors({ current: same, next: same, confirm: same }).next,
    ).toBe("新密碼不能和目前的密碼一樣");
  });

  it("長度用字數算，不是位元組", () => {
    const next = "密碼密碼密碼密碼密碼";
    expect(passwordFormErrors({ current: "x", next, confirm: next })).toEqual(
      {},
    );
  });
});

describe("memberFormErrors", () => {
  it("帳號與顯示名稱都要填，帳號不能有空白", () => {
    expect(memberFormErrors({ username: " ", displayName: "" })).toEqual({
      username: "請輸入帳號",
      displayName: "請輸入顯示名稱",
    });
    expect(memberFormErrors({ username: "a b", displayName: "A" })).toEqual({
      username: "帳號不能有空白",
    });
    expect(memberFormErrors({ username: "amy", displayName: "Amy" })).toEqual(
      {},
    );
  });
});

describe("工作資料夾", () => {
  it("我的檔案排第一個，其他照原本順序", () => {
    expect(sortFolders(FOLDERS).map((f) => f.id)).toEqual([
      "private",
      "shared:docs",
      "shared:team",
    ]);
    expect(sortFolders(undefined)).toEqual([]);
  });

  it("舊資料（專案資料夾）排最後", () => {
    const legacy: FolderInfo = {
      id: "legacy",
      name: "舊資料（專案資料夾）",
      kind: "shared",
      writable: true,
    };
    expect(sortFolders([legacy, ...FOLDERS]).map((f) => f.id)).toEqual([
      "private",
      "shared:docs",
      "shared:team",
      "legacy",
    ]);
    expect(defaultFolderId([legacy, ...FOLDERS])).toBe("private");
    expect(folderLabel([legacy], "legacy")).toBe("舊資料（專案資料夾）");
    expect(activeFolder(accounts("admin"), "legacy")).toBe("legacy");
  });

  it("對話裡改選項時不送出資料夾", () => {
    expect(withoutFolder({ model: "m", folder: "shared:team" })).toEqual({
      model: "m",
    });
    expect(withoutFolder({ skills: [] })).toEqual({ skills: [] });
  });

  it("沒有選時用我的檔案", () => {
    expect(defaultFolderId(FOLDERS)).toBe("private");
    expect(defaultFolderId(undefined)).toBe("private");
  });

  it("伺服器不支援時不帶資料夾", () => {
    expect(activeFolder(project(), "shared:docs")).toBeUndefined();
    // 本機模式：有帳號功能但 folders 是 false，對話在專案根目錄跑。
    expect(
      activeFolder(project({ accounts: true, folders: false }), "private"),
    ).toBeUndefined();
    expect(activeFolder(accounts("member"), null)).toBe("private");
    expect(activeFolder(accounts("member"), "shared:team")).toBe("shared:team");
  });

  it("顯示名稱，找不到時退回 id", () => {
    expect(folderLabel(FOLDERS, "shared:docs")).toBe("共用文件");
    expect(folderLabel([], "private")).toBe("我的檔案");
    expect(folderLabel([], "shared:gone")).toBe("shared:gone");
  });
});
