/**
 * 按鈕打開的小選單（⋮、使用者選單），放在 body 底下，不會被側欄或
 * 列表的捲動區域裁掉。下方空間不夠時往上開。
 *
 * 上下鍵移動、Esc 關閉並把焦點還給按鈕、Tab 離開時關閉；點外面、
 * 捲動按鈕所在的區域或改變視窗大小時也會關閉。
 */

import {
  type KeyboardEvent,
  type ReactNode,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

export function FloatingMenu({
  anchor,
  label,
  align = "end",
  onClose,
  children,
}: {
  anchor: HTMLElement;
  label: string;
  /** end：選單右緣對齊按鈕右緣；start：左緣對齊左緣。 */
  align?: "start" | "end";
  onClose: () => void;
  /** 選項：role="menuitem" 的按鈕，className 用 menu-item。 */
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{
    top: number;
    left: number;
    above: boolean;
  }>();

  useLayoutEffect(() => {
    const rect = anchor.getBoundingClientRect();
    const width = ref.current?.offsetWidth ?? 160;
    const height = ref.current?.offsetHeight ?? 88;
    const below = rect.bottom + 4 + height <= window.innerHeight;
    const left =
      align === "end"
        ? rect.right - width
        : Math.min(rect.left, window.innerWidth - width - 8);
    setPosition({
      top: below ? rect.bottom + 4 : rect.top - 4 - height,
      left: Math.max(8, left),
      above: !below,
    });
  }, [anchor, align]);

  // 定好位置、看得到之後才能把焦點移到第一個選項。
  useEffect(() => {
    if (position) {
      ref.current?.querySelector<HTMLElement>("[role=menuitem]")?.focus();
    }
  }, [position]);

  useEffect(() => {
    const onPointer = (event: PointerEvent) => {
      const target = event.target as Node;
      if (ref.current?.contains(target) || anchor.contains(target)) return;
      onClose();
    };
    // 只有按鈕所在的區域或整頁捲動（按鈕跟著移動）時才關閉，其他區域的捲動不管。
    const onScroll = (event: Event) => {
      const target = event.target;
      if (target instanceof Node && target.contains(anchor)) onClose();
    };
    document.addEventListener("pointerdown", onPointer);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onClose);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onClose);
    };
  }, [anchor, onClose]);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const items = [
      ...(ref.current?.querySelectorAll<HTMLElement>("[role=menuitem]") ?? []),
    ];
    const index = items.indexOf(document.activeElement as HTMLElement);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      items[(index + step + items.length) % items.length]?.focus();
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      onClose();
      anchor.focus();
    } else if (event.key === "Tab") {
      onClose();
    }
  };

  const origin = `${position?.above ? "above" : ""} ${align === "start" ? "from-start" : ""}`;
  return createPortal(
    <div
      ref={ref}
      // 在按鈕上方時從下緣展開，動畫才會從按鈕那一角長出來。
      className={`menu ${origin}`}
      role="menu"
      aria-label={label}
      style={
        position
          ? { top: position.top, left: position.left }
          : { visibility: "hidden" }
      }
      onKeyDown={onKeyDown}
    >
      {children}
    </div>,
    document.body,
  );
}
