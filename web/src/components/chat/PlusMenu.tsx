/**
 * 輸入框左下角的 ＋ 選單：上傳檔案、選 skills、勾選這段對話要搜尋的知識庫
 * 與可以用的工具。
 *
 * Skills 與工具在同一個彈出框裡換頁顯示，手機上也好點。可以複選，所以
 * 勾選後選單不關；送出訊息時（closeSignal 改變）才關。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { BookIcon, CheckIcon, ChevronIcon } from "../Icons.tsx";
import {
  BackIcon,
  PlusIcon,
  SearchIcon,
  SparkIcon,
  ToolIcon,
  UploadIcon,
} from "./icons.tsx";
import {
  type ComposerOptions,
  chosenKnowledge,
  toggleKnowledge,
} from "./options.ts";
import { focusTrigger, Popover } from "./Popover.tsx";

type View = "main" | "skills" | "knowledge" | "tools";

export function PlusMenu({
  options,
  closeSignal,
  onUpload,
}: {
  options: ComposerOptions;
  /** 改變時關掉選單（例如送出了訊息）。 */
  closeSignal?: number;
  /** 打開選檔視窗；沒有提供時不顯示「上傳檔案」。 */
  onUpload?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<View>("main");
  const anchor = useRef<HTMLDivElement>(null);
  // 從子頁按「回上一層」時，記下是哪一頁，回到主選單後焦點放在那一項。
  const returnTo = useRef<View>(undefined);
  const close = useCallback(() => {
    setOpen(false);
    setView("main");
  }, []);
  // biome-ignore lint/correctness/useExhaustiveDependencies: 只在 closeSignal 改變時關
  useEffect(close, [closeSignal]);
  const back = (from: View) => {
    returnTo.current = from;
    setView("main");
  };
  useEffect(() => {
    if (view !== "main" || !returnTo.current) return;
    anchor.current
      ?.querySelector<HTMLElement>(`[data-view="${returnTo.current}"]`)
      ?.focus();
    returnTo.current = undefined;
  }, [view]);

  const { skills, tools, value, knowledgeBases, defaultKnowledge } = options;
  const enabledTools = value.tools ?? tools.map((tool) => tool.name);
  const searched = chosenKnowledge(value, defaultKnowledge);

  const toggleSkill = (name: string) => {
    const next = value.skills.includes(name)
      ? value.skills.filter((item) => item !== name)
      : [...value.skills, name];
    options.onChange({ skills: next });
  };

  const toggleTool = (name: string) => {
    const next = enabledTools.includes(name)
      ? enabledTools.filter((item) => item !== name)
      : [...enabledTools, name];
    // 全部勾選時存成 null，表示跟著 agent 的設定。
    const all = tools.every((tool) => next.includes(tool.name));
    options.onChange({ tools: all ? null : next });
  };

  return (
    <div className="ch-anchor" ref={anchor}>
      <button
        type="button"
        className="icon-btn ch-plus-btn"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label="更多選項：上傳檔案、skills、知識庫、工具"
        onClick={() => (open ? close() : setOpen(true))}
      >
        <PlusIcon />
      </button>
      <Popover
        open={open}
        onClose={close}
        anchor={anchor}
        label="更多選項"
        className="ch-plus-pop"
      >
        {view === "main" && (
          <div className="ch-pop-list" role="menu" aria-label="更多選項">
            {onUpload && (
              <button
                type="button"
                role="menuitem"
                className="ch-pop-item"
                onClick={() => {
                  focusTrigger(anchor);
                  close();
                  onUpload();
                }}
              >
                <UploadIcon />
                <span className="ch-pop-main">
                  <span className="ch-pop-title">上傳檔案</span>
                </span>
              </button>
            )}
            {skills.length > 0 && (
              <button
                type="button"
                role="menuitem"
                className="ch-pop-item"
                data-view="skills"
                onClick={() => setView("skills")}
              >
                <SparkIcon />
                <span className="ch-pop-main">
                  <span className="ch-pop-title">Skills</span>
                </span>
                {value.skills.length > 0 && (
                  <span className="ch-count">{value.skills.length}</span>
                )}
                <ChevronIcon size={14} />
              </button>
            )}
            {knowledgeBases.length > 0 && (
              <button
                type="button"
                role="menuitem"
                className="ch-pop-item"
                data-view="knowledge"
                onClick={() => setView("knowledge")}
              >
                <BookIcon />
                <span className="ch-pop-main">
                  <span className="ch-pop-title">知識庫</span>
                </span>
                <span className="ch-count">
                  {searched.length}/{knowledgeBases.length}
                </span>
                <ChevronIcon size={14} />
              </button>
            )}
            {tools.length > 0 && (
              <button
                type="button"
                role="menuitem"
                className="ch-pop-item"
                data-view="tools"
                onClick={() => setView("tools")}
              >
                <ToolIcon />
                <span className="ch-pop-main">
                  <span className="ch-pop-title">工具</span>
                </span>
                <span className="ch-count">
                  {enabledTools.length}/{tools.length}
                </span>
                <ChevronIcon size={14} />
              </button>
            )}
          </div>
        )}
        {view === "skills" && (
          <Checklist
            title="Skills"
            hint="勾選的 skill 會請 agent 先讀過再回答。"
            items={skills.map((skill) => ({
              name: skill.name,
              description: skill.description,
              checked: value.skills.includes(skill.name),
            }))}
            onToggle={toggleSkill}
            onBack={() => back("skills")}
          />
        )}
        {view === "knowledge" && (
          <Checklist
            title="知識庫"
            hint="勾選的知識庫，agent 回答前會一起搜尋，並標出資料來自哪個知識庫。"
            items={knowledgeBases.map((base) => ({
              name: base.id,
              label: base.name,
              description: base.description,
              checked: searched.includes(base.id),
            }))}
            onToggle={(id) =>
              options.onChange({
                knowledge: toggleKnowledge(
                  value,
                  defaultKnowledge,
                  knowledgeBases,
                  id,
                ),
              })
            }
            onBack={() => back("knowledge")}
          />
        )}
        {view === "tools" && (
          <Checklist
            title="工具"
            hint="取消勾選的工具，這段對話就不會使用。"
            items={tools.map((tool) => ({
              name: tool.name,
              description: tool.description,
              checked: enabledTools.includes(tool.name),
            }))}
            onToggle={toggleTool}
            onBack={() => back("tools")}
          />
        )}
        {view !== "main" && options.nextTurn && (
          <div className="ch-pop-note">下一則訊息開始使用</div>
        )}
      </Popover>
    </div>
  );
}

function Checklist({
  title,
  hint,
  items,
  onToggle,
  onBack,
}: {
  title: string;
  hint: string;
  items: {
    name: string;
    /** 顯示的名稱；沒有時顯示 name。 */
    label?: string;
    description: string;
    checked: boolean;
  }[];
  onToggle: (name: string) => void;
  onBack: () => void;
}) {
  const [query, setQuery] = useState("");
  const search = useRef<HTMLInputElement>(null);
  // 換頁後焦點放在搜尋框，鍵盤可以直接輸入。
  useEffect(() => search.current?.focus(), []);
  const needle = query.trim().toLowerCase();
  const shown = needle
    ? items.filter((item) =>
        `${item.name} ${item.label ?? ""} ${item.description}`
          .toLowerCase()
          .includes(needle),
      )
    : items;
  return (
    <div className="ch-pop-view">
      <div className="ch-pop-head">
        <button
          type="button"
          className="icon-btn ch-pop-back"
          aria-label="回上一層"
          onClick={onBack}
        >
          <BackIcon />
        </button>
        <span className="ch-pop-heading">{title}</span>
      </div>
      <div className="ch-pop-search">
        <SearchIcon size={14} />
        <input
          ref={search}
          type="search"
          placeholder={`搜尋${title}`}
          aria-label={`搜尋${title}`}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            // 輸入框在表單裡，Enter 會送出訊息；改成勾選第一個符合的項目。
            if (event.key !== "Enter" || event.nativeEvent.isComposing) return;
            event.preventDefault();
            const first = shown[0];
            if (first) onToggle(first.name);
          }}
        />
      </div>
      <div className="ch-pop-list" role="menu" aria-label={title}>
        {shown.map((item) => (
          <button
            key={item.name}
            type="button"
            role="menuitemcheckbox"
            aria-checked={item.checked}
            className="ch-pop-item"
            title={item.description}
            onClick={() => onToggle(item.name)}
          >
            <span className={`ch-box${item.checked ? " on" : ""}`}>
              {item.checked && <CheckIcon size={12} />}
            </span>
            <span className="ch-pop-main">
              <span className={`ch-pop-title${item.label ? "" : " mono"}`}>
                {item.label ?? item.name}
              </span>
              {item.description && (
                <span className="ch-pop-sub clamp">{item.description}</span>
              )}
            </span>
          </button>
        ))}
        {shown.length === 0 && (
          <div className="ch-pop-empty">找不到符合的項目</div>
        )}
      </div>
      <div className="ch-pop-hint">{hint}</div>
    </div>
  );
}
