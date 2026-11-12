/**
 * 對話區與右側面板之間的分隔線：拖曳調整面板寬度，雙擊回到預設寬度。
 *
 * 也可以用鍵盤：按 Tab 移到分隔線後，左右方向鍵調整、Home／End 到最
 * 寬／最窄、Enter 回到預設寬度。
 */

import {
  type KeyboardEvent,
  type PointerEvent,
  useEffect,
  useRef,
} from "react";
import { clampWidth, KEY_STEP, MIN_PANEL } from "./panelWidth.ts";

export function PanelResizer({
  width,
  onWidth,
  onReset,
}: {
  /** 目前的寬度；null 表示 CSS 的預設寬度。 */
  width: number | null;
  onWidth: (width: number) => void;
  onReset: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const drag = useRef<{ startX: number; startWidth: number } | null>(null);

  // 拖曳中游標維持左右箭頭、不選取文字；放開或卸載時恢復。
  useEffect(() => () => document.body.classList.remove("ch-resizing"), []);

  /** 對話區加面板可用的總寬度，以及面板現在的寬度。 */
  function measure() {
    const handle = ref.current;
    const chat = handle?.previousElementSibling;
    const panel = handle?.nextElementSibling;
    if (!chat || !panel) return null;
    const chatBox = chat.getBoundingClientRect();
    const panelBox = panel.getBoundingClientRect();
    return {
      available: chatBox.width + panelBox.width,
      current: panelBox.width,
    };
  }

  function onPointerDown(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    const box = measure();
    if (!box) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { startX: event.clientX, startWidth: box.current };
    document.body.classList.add("ch-resizing");
  }

  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    const start = drag.current;
    const box = start && measure();
    if (!start || !box) return;
    // 面板在右邊：往左拖是變寬。
    const next = start.startWidth + (start.startX - event.clientX);
    onWidth(clampWidth(next, box.available));
  }

  function onPointerUp(event: PointerEvent<HTMLDivElement>) {
    if (!drag.current) return;
    drag.current = null;
    event.currentTarget.releasePointerCapture(event.pointerId);
    document.body.classList.remove("ch-resizing");
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const box = measure();
    if (!box) return;
    const moves: Record<string, number> = {
      ArrowLeft: box.current + KEY_STEP,
      ArrowRight: box.current - KEY_STEP,
      Home: Number.POSITIVE_INFINITY,
      End: MIN_PANEL,
    };
    if (event.key === "Enter") {
      event.preventDefault();
      onReset();
      return;
    }
    const next = moves[event.key];
    if (next === undefined) return;
    event.preventDefault();
    onWidth(clampWidth(next, box.available));
  }

  return (
    // biome-ignore lint/a11y/useSemanticElements: <hr> 不能接收焦點與拖曳，可調整的分隔線依 WAI-ARIA 用 role="separator"
    <div
      ref={ref}
      className="ch-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label="調整預覽面板寬度"
      aria-valuenow={width ?? undefined}
      aria-valuemin={MIN_PANEL}
      tabIndex={0}
      title="拖曳調整寬度，雙擊回到預設寬度"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onDoubleClick={onReset}
      onKeyDown={onKeyDown}
    />
  );
}
