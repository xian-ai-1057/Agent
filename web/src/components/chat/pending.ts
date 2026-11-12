/**
 * 剛送出、伺服器還沒回報的使用者訊息。
 *
 * 新對話頁建立對話後馬上切到對話頁，這時對話內容還在讀取；先把訊息
 * 交給對話頁，畫面才不會空白。
 */

export interface PendingMessage {
  text: string;
  attachments: string[];
  /** 送出時 messages 有幾則；之後出現的使用者訊息就是這一則。 */
  base: number;
  /** 新對話頁選的工作資料夾（FolderInfo.id），對話內容讀回來之前用。 */
  folder?: string;
}

const handoff = new Map<string, PendingMessage>();

export function handOff(id: string, message: PendingMessage): void {
  handoff.set(id, message);
}

/** 交接的訊息。React 的 StrictMode 會重跑初始化，所以這裡只讀不刪。 */
export function peekHandoff(id: string): PendingMessage | undefined {
  return handoff.get(id);
}

/** 對話頁接手後清掉。 */
export function forgetHandoff(id: string): void {
  handoff.delete(id);
}
