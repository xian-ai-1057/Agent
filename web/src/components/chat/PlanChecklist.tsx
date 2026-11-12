/** agent 的計畫（update_plan）做成勾選清單，可以收合。 */

import { useState } from "react";
import { ChevronIcon } from "../Icons.tsx";
import { type PlanItem, planProgress } from "./plan.ts";

export function PlanList({ items }: { items: PlanItem[] }) {
  return (
    <ol className="ch-plan-list">
      {items.map((item, index) => (
        <li
          // biome-ignore lint/suspicious/noArrayIndexKey: 步驟可能重複，順序就是身分
          key={index}
          className={`ch-plan-item ${item.status}`}
        >
          <span className="ch-plan-mark" aria-hidden="true">
            {item.status === "completed" && (
              <svg
                width="12"
                height="12"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={3}
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path
                  className="ch-check-draw"
                  pathLength={1}
                  d="M5 12.5l4.5 4.5L19 7.5"
                />
              </svg>
            )}
          </span>
          <span className="ch-plan-step">{item.step}</span>
          <span className="sr-only">
            {item.status === "completed"
              ? "（已完成）"
              : item.status === "in_progress"
                ? "（進行中）"
                : "（尚未開始）"}
          </span>
        </li>
      ))}
    </ol>
  );
}

/** 釘在輸入框上方、對話進行中才顯示的計畫。 */
export function PlanChecklist({
  items,
  defaultOpen = true,
  className,
}: {
  items: PlanItem[];
  defaultOpen?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const { done, total } = planProgress(items);
  const current = items.find((item) => item.status === "in_progress");
  return (
    <section
      className={`ch-plan${className ? ` ${className}` : ""}`}
      aria-label="計畫"
    >
      <button
        type="button"
        className="ch-plan-head"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span className="ch-plan-title">
          計畫{" "}
          <span key={done} className="anim-swap ch-plan-count">
            {done} / {total}
          </span>
        </span>
        {!open && current && (
          <span className="ch-plan-current">{current.step}</span>
        )}
        <ChevronIcon className="chevron" size={14} />
      </button>
      <div className={`collapse ${open ? "open" : ""}`}>
        <div>
          <PlanList items={items} />
        </div>
      </div>
    </section>
  );
}
