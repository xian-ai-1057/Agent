/**
 * 頁面內的對話框，取代 window.confirm。
 *
 * 用瀏覽器內建的 <dialog>（showModal），焦點會留在對話框裡，按 Esc
 * 或點背景關閉。只在要顯示時才 render，關閉時由呼叫的一方拿掉它。
 */

import {
  type ReactNode,
  type RefObject,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { TrashIcon } from "../Icons.tsx";

export function Modal({
  onClose,
  className = "",
  labelledBy,
  initialFocus,
  children,
}: {
  onClose: () => void;
  className?: string;
  /** 對話框標題元素的 id。 */
  labelledBy?: string;
  /** 打開後要先聚焦的元素；沒有時聚焦第一個可以操作的元素。 */
  initialFocus?: RefObject<HTMLElement | null>;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const previous = document.activeElement;
    if (!dialog.open) dialog.showModal();
    initialFocus?.current?.focus();
    // 點到對話框本身（不是裡面的內容）就是點到背景。
    const onClick = (event: MouseEvent) => {
      if (event.target === dialog) close.current();
    };
    const onCancel = (event: Event) => {
      event.preventDefault();
      close.current();
    };
    dialog.addEventListener("click", onClick);
    dialog.addEventListener("cancel", onCancel);
    return () => {
      dialog.removeEventListener("click", onClick);
      dialog.removeEventListener("cancel", onCancel);
      if (dialog.open) dialog.close();
      if (previous instanceof HTMLElement && previous.isConnected) {
        previous.focus();
      }
    };
  }, [initialFocus]);

  return (
    <dialog
      ref={ref}
      className={`modal ${className}`}
      aria-labelledby={labelledBy}
    >
      {children}
    </dialog>
  );
}

/**
 * 確認對話框。onConfirm 回傳的 Promise 完成後自動關閉；失敗時把錯誤
 * 顯示在對話框裡，讓使用者決定要重試還是取消。
 */
export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  danger = false,
  disabled = false,
  onConfirm,
  onClose,
}: {
  title: string;
  children?: ReactNode;
  confirmLabel: string;
  /** 刪除這類無法復原的動作：按鈕用危險色並加上垃圾桶圖示。 */
  danger?: boolean;
  /** 現在不能確認（原因寫在 children 裡）：確認按鈕停用。 */
  disabled?: boolean;
  onConfirm: () => Promise<void> | void;
  onClose: () => void;
}) {
  const titleId = useId();
  const cancelRef = useRef<HTMLButtonElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const confirm = async () => {
    setBusy(true);
    setError(undefined);
    try {
      await onConfirm();
      onClose();
    } catch (failure) {
      setError((failure as Error).message);
      setBusy(false);
    }
  };

  return (
    <Modal
      onClose={onClose}
      className="confirm-dialog"
      labelledBy={titleId}
      initialFocus={cancelRef}
    >
      <h2 id={titleId} className="dialog-title">
        {title}
      </h2>
      {children && <div className="dialog-body">{children}</div>}
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      <div className="dialog-actions">
        <button
          ref={cancelRef}
          type="button"
          className="btn btn-outline"
          onClick={onClose}
        >
          取消
        </button>
        <button
          type="button"
          className={`btn ${danger ? "btn-danger" : "btn-accent"}`}
          disabled={busy || disabled}
          onClick={confirm}
        >
          {danger && <TrashIcon size={15} />}
          {confirmLabel}
        </button>
      </div>
    </Modal>
  );
}
