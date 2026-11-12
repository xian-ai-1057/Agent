/**
 * 工作資料夾（Python 版，features.folders）：每段對話選一個資料夾，agent
 * 只能讀寫裡面的檔案，上傳、預覽與下載也都在這個資料夾裡。
 *
 * 只放純函式，測試可以直接引用。伺服器沒有這個功能時一律回傳
 * undefined，網址就不帶 folder 參數，和以前一樣。
 */

import type {
  FolderInfo,
  ProjectInfo,
  SessionOptions,
} from "../../src/web/protocol.ts";

/** 每個人自己的「我的檔案」的 id（伺服器沒有列出資料夾時用）。 */
export const PRIVATE_FOLDER = "private";

export function foldersEnabled(project: ProjectInfo | undefined): boolean {
  return project?.features?.folders === true;
}

/** 搬過來的舊對話所在的資料夾（舊資料（專案資料夾））的 id。 */
export const LEGACY_FOLDER = "legacy";

/**
 * 選單裡的順序：我的檔案在最前面，舊資料在最後，其他照伺服器給的
 * 順序。
 */
export function sortFolders(folders: FolderInfo[] | undefined): FolderInfo[] {
  const list = folders ?? [];
  const legacy = (folder: FolderInfo) => folder.id === LEGACY_FOLDER;
  return [
    ...list.filter((folder) => folder.kind === "private" && !legacy(folder)),
    ...list.filter((folder) => folder.kind !== "private" && !legacy(folder)),
    ...list.filter(legacy),
  ];
}

/**
 * 對話裡改選項時要送出的內容：拿掉 folder。資料夾在開始對話前選定，
 * 對話開始後伺服器也不接受更改。
 */
export function withoutFolder(
  change: Partial<SessionOptions>,
): Partial<Omit<SessionOptions, "folder">> {
  const { folder: _folder, ...rest } = change;
  return rest;
}

/** 沒有選資料夾時用的：自己的「我的檔案」。 */
export function defaultFolderId(folders: FolderInfo[] | undefined): string {
  return sortFolders(folders)[0]?.id ?? PRIVATE_FOLDER;
}

/**
 * 這段對話（或新對話頁）實際使用的資料夾 id；伺服器不支援時是
 * undefined。chosen 是 SessionOptions.folder 或新對話頁選的。
 */
export function activeFolder(
  project: ProjectInfo | undefined,
  chosen: string | null | undefined,
): string | undefined {
  if (!foldersEnabled(project)) return undefined;
  return chosen || defaultFolderId(project?.folders);
}

/** 資料夾的資訊；清單裡沒有時（例如被管理者拿掉了）是 undefined。 */
export function findFolder(
  folders: FolderInfo[] | undefined,
  id: string | undefined,
): FolderInfo | undefined {
  return folders?.find((folder) => folder.id === id);
}

/** 顯示的名稱；清單裡沒有時，我的檔案顯示「我的檔案」，其他顯示 id。 */
export function folderLabel(
  folders: FolderInfo[] | undefined,
  id: string | undefined,
): string {
  const found = findFolder(folders, id);
  if (found) return found.name;
  if (!id || id === PRIVATE_FOLDER) return "我的檔案";
  return id;
}
