/**
 * 畫面底部的短暫提示，取代 window.alert。
 *
 * 任何地方都可以呼叫 showToast()；App 裡放一個 <Toaster /> 負責顯示。
 */

import { useSyncExternalStore } from "react";
import { CloseIcon } from "../Icons.tsx";

export type ToastKind = "info" | "error";

interface Toast {
  id: number;
  message: string;
  kind: ToastKind;
}

let toasts: Toast[] = [];
let nextId = 1;
const listeners = new Set<() => void>();

function emit(next: Toast[]): void {
  toasts = next;
  for (const listener of listeners) listener();
}

function dismiss(id: number): void {
  emit(toasts.filter((toast) => toast.id !== id));
}

/** 顯示一則提示，幾秒後自動消失；錯誤停留久一點。 */
export function showToast(message: string, kind: ToastKind = "info"): void {
  const id = nextId++;
  // 最多同時顯示三則，舊的先拿掉。
  emit([...toasts.slice(-2), { id, message, kind }]);
  setTimeout(() => dismiss(id), kind === "error" ? 6000 : 3500);
}

export function Toaster() {
  const list = useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => toasts,
  );
  return (
    <div className="toaster" aria-live="polite">
      {list.map((toast) => (
        <div
          key={toast.id}
          className={`toast-item ${toast.kind}`}
          role={toast.kind === "error" ? "alert" : "status"}
        >
          <span className="grow">{toast.message}</span>
          <button
            type="button"
            className="toast-close"
            aria-label="關閉提示"
            onClick={() => dismiss(toast.id)}
          >
            <CloseIcon size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}
