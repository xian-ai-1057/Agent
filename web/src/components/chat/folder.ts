/**
 * 這段對話的工作資料夾（FolderInfo.id），給讀取檔案的元件用：附件、
 * 產生的檔案、回覆裡的圖片、檔案面板的預覽與下載。伺服器不支援工作
 * 資料夾時是 undefined，網址不帶 folder 參數。
 */

import { createContext, useContext } from "react";

export const FolderContext = createContext<string | undefined>(undefined);

export function useFolder(): string | undefined {
  return useContext(FolderContext);
}
