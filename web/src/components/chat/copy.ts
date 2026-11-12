/** 複製文字到剪貼簿，並在按鈕上短暫顯示「已複製」。 */

import { useEffect, useRef, useState } from "react";

/**
 * 先用 navigator.clipboard；不能用時（非 https、舊瀏覽器）改用暫時的
 * textarea 加 execCommand。
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // 改用下面的做法。
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  area.remove();
  return ok;
}

/** 回傳目前的狀態與複製的函式；成功或失敗都會在 1.5 秒後恢復。 */
export function useCopy(): [
  "idle" | "copied" | "failed",
  (text: string) => void,
] {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  const copy = (text: string) => {
    void copyText(text).then((ok) => {
      setState(ok ? "copied" : "failed");
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setState("idle"), 1500);
    });
  };
  return [state, copy];
}

export const COPY_LABELS = {
  idle: "複製",
  copied: "已複製",
  failed: "無法複製",
} as const;
