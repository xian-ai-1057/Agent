/**
 * 輸入框上的彈出選單。點外面或按 Esc 關閉，關閉後焦點回到按鈕。
 *
 * 預設往上開；上方空間不夠、下方比較大時改往下開。高度不超過可用的
 * 空間，內容太多時捲動，選單不會跑出畫面。
 */

import {
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

const GAP = 12;
const PREFERRED = 420;

export function Popover({
  open,
  onClose,
  anchor,
  align = "start",
  label,
  className,
  children,
}: {
  open: boolean;
  onClose: () => void;
  /** 按鈕外層（position: relative 的元素）；點在它裡面不算「外面」。 */
  anchor: RefObject<HTMLElement | null>;
  align?: "start" | "end";
  label: string;
  className?: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState<{ up: boolean; max: number }>({
    up: true,
    max: PREFERRED,
  });

  useLayoutEffect(() => {
    if (!open || !anchor.current) return;
    const rect = anchor.current.getBoundingClientRect();
    const above = rect.top - GAP;
    const below = window.innerHeight - rect.bottom - GAP;
    const up = above >= 240 || above >= below;
    setPlace({
      up,
      max: Math.max(160, Math.min(PREFERRED, up ? above : below)),
    });
  }, [open, anchor]);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!anchor.current?.contains(event.target as Node)) onClose();
    };
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      onClose();
      focusTrigger(anchor);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onClose, anchor]);

  // 打開時把焦點放在搜尋框或第一個選項。
  useEffect(() => {
    if (!open) return;
    const first = ref.current?.querySelector<HTMLElement>(
      "input, [role^='menuitem'], button",
    );
    first?.focus();
  }, [open]);

  if (!open) return null;
  return (
    <div
      ref={ref}
      role="dialog"
      aria-label={label}
      className={`ch-pop ${place.up ? "up" : "down"} ${align}${className ? ` ${className}` : ""}`}
      style={{ maxHeight: place.max }}
      onKeyDown={moveFocus}
    >
      {children}
    </div>
  );
}

/**
 * 把焦點放回打開選單的按鈕。選了項目、選單關起來時呼叫，焦點才不會
 * 掉到頁面最前面。
 */
export function focusTrigger(anchor: RefObject<HTMLElement | null>) {
  anchor.current?.querySelector<HTMLElement>("[aria-haspopup]")?.focus();
}

/** 上下鍵在選項之間移動焦點。 */
function moveFocus(event: KeyboardEvent<HTMLDivElement>) {
  if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
  const items = [
    ...event.currentTarget.querySelectorAll<HTMLElement>(
      "input, [role^='menuitem']",
    ),
  ];
  if (items.length === 0) return;
  event.preventDefault();
  const index = items.indexOf(document.activeElement as HTMLElement);
  const step = event.key === "ArrowDown" ? 1 : -1;
  const next = items[(index + step + items.length) % items.length];
  next?.focus();
}
