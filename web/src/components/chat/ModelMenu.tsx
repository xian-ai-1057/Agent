/** 輸入框右下角的模型選單：可以搜尋，列出供應商與能力標籤。 */

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckIcon, ChevronDownIcon } from "../Icons.tsx";
import { SearchIcon } from "./icons.tsx";
import { type ComposerOptions, effectiveModel, modelLabel } from "./options.ts";
import { focusTrigger, Popover } from "./Popover.tsx";

export function ModelMenu({
  options,
  closeSignal,
}: {
  options: ComposerOptions;
  /** 改變時關掉選單（例如送出了訊息）。 */
  closeSignal?: number;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const anchor = useRef<HTMLDivElement>(null);
  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
  }, []);
  // biome-ignore lint/correctness/useExhaustiveDependencies: 只在 closeSignal 改變時關
  useEffect(close, [closeSignal]);

  const { models } = options;
  const current = effectiveModel(options.value, options.fallbackModel, models);
  const label = modelLabel(current, models) ?? "選擇模型";
  const needle = query.trim().toLowerCase();
  const shown = needle
    ? models.filter((model) =>
        `${model.label} ${model.name} ${model.provider}`
          .toLowerCase()
          .includes(needle),
      )
    : models;
  const choose = (name: string) => {
    if (name !== current) options.onChange({ model: name });
    focusTrigger(anchor);
    close();
  };

  return (
    <div className="ch-anchor" ref={anchor}>
      <button
        type="button"
        className="ch-model-btn"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`模型：${label}`}
        title={current}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <span className="ch-model-name">{label}</span>
        <ChevronDownIcon size={14} />
      </button>
      <Popover
        open={open}
        onClose={close}
        anchor={anchor}
        align="end"
        label="選擇模型"
        className="ch-model-pop"
      >
        <div className="ch-pop-search">
          <SearchIcon size={14} />
          <input
            type="search"
            placeholder="搜尋模型"
            aria-label="搜尋模型"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              // 輸入框在表單裡，Enter 會送出訊息；改成選第一個符合的模型。
              if (event.key !== "Enter" || event.nativeEvent.isComposing) {
                return;
              }
              event.preventDefault();
              const first = shown[0];
              if (first) choose(first.name);
            }}
          />
        </div>
        <div className="ch-pop-list" role="menu" aria-label="模型">
          {shown.map((model) => {
            const checked = model.name === current;
            return (
              <button
                key={model.name}
                type="button"
                role="menuitemradio"
                aria-checked={checked}
                className="ch-pop-item"
                onClick={() => choose(model.name)}
              >
                <span className="ch-pop-main">
                  <span className="ch-pop-title">{model.label}</span>
                  <span className="ch-pop-sub">
                    {model.provider}
                    {model.vision && <span className="ch-tag">看得懂圖片</span>}
                    {model.isDefault && <span className="ch-tag">預設</span>}
                  </span>
                </span>
                <span className="ch-pop-check">
                  {checked && <CheckIcon size={14} />}
                </span>
              </button>
            );
          })}
          {shown.length === 0 && (
            <div className="ch-pop-empty">找不到符合的模型</div>
          )}
        </div>
        {options.nextTurn && (
          <div className="ch-pop-note">下一則訊息開始使用</div>
        )}
      </Popover>
    </div>
  );
}
