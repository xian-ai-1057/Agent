/**
 * 搜尋對話（Python 版）：⌘K / Ctrl+K 或側欄的「搜尋對話」打開。
 *
 * 沒有輸入時列出最近的對話；輸入後停一下才向伺服器搜尋，標題與內容
 * 片段裡的關鍵字會標出來。↑↓ 選擇、Enter 開啟、Esc 關閉。
 */

import {
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import type { SearchResult } from "../../../../src/web/protocol.ts";
import { api } from "../../api.ts";
import { formatAgo } from "../../format.ts";
import { navigate } from "../../router.ts";
import { useStore } from "../../store.ts";
import { SearchIcon } from "../Icons.tsx";
import { Modal } from "./Dialog.tsx";

const RECENT_COUNT = 8;
const DEBOUNCE_MS = 200;

export function SearchDialog({ onClose }: { onClose: () => void }) {
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const sessions = useStore((s) => s.sessions);
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<SearchResult[]>();
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string>();
  const [active, setActive] = useState(0);
  const trimmed = query.trim();

  useEffect(() => {
    if (!trimmed) {
      setFound(undefined);
      setSearching(false);
      setError(undefined);
      return;
    }
    // 打字時先等一下；新的查詢開始後，舊查詢的結果就不要了。
    let cancelled = false;
    setSearching(true);
    const timer = setTimeout(() => {
      api
        .searchSessions(trimmed)
        .then((results) => {
          if (cancelled) return;
          setFound(results);
          setError(undefined);
          setActive(0);
        })
        .catch((failure: Error) => {
          if (!cancelled) setError(failure.message);
        })
        .finally(() => {
          if (!cancelled) setSearching(false);
        });
    }, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [trimmed]);

  const recent: SearchResult[] = Object.values(sessions)
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .slice(0, RECENT_COUNT)
    .map(({ id, title, updatedAt }) => ({ id, title, updatedAt }));
  const results = trimmed ? (found ?? []) : recent;
  const current = Math.min(active, results.length - 1);
  const optionId = (index: number) => `${listId}-${index}`;

  useEffect(() => {
    if (current < 0) return;
    document
      .getElementById(`${listId}-${current}`)
      ?.scrollIntoView({ block: "nearest" });
  }, [current, listId]);

  const open = (result: SearchResult | undefined) => {
    if (!result) return;
    onClose();
    navigate(`/s/${result.id}`);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive(Math.min(current + 1, results.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive(Math.max(current - 1, 0));
    } else if (event.key === "Enter" && !event.nativeEvent.isComposing) {
      event.preventDefault();
      open(results[current]);
    }
  };

  const onLink = (event: MouseEvent<HTMLAnchorElement>, item: SearchResult) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0)
      return;
    event.preventDefault();
    open(item);
  };

  let status = "";
  if (trimmed && error) status = error;
  else if (trimmed && found && found.length === 0 && !searching)
    status = "找不到符合的對話";
  else if (trimmed && !found) status = "搜尋中…";
  else if (!trimmed && recent.length === 0) status = "還沒有對話";

  const now = Date.now();

  return (
    <Modal
      onClose={onClose}
      className="search-dialog"
      labelledBy={`${listId}-label`}
      initialFocus={inputRef}
    >
      <div className="search-field">
        <SearchIcon size={18} />
        <label
          id={`${listId}-label`}
          htmlFor={`${listId}-input`}
          className="sr-only"
        >
          搜尋對話
        </label>
        <input
          ref={inputRef}
          id={`${listId}-input`}
          type="search"
          role="combobox"
          aria-expanded={results.length > 0}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={current >= 0 ? optionId(current) : undefined}
          placeholder="搜尋對話標題或內容"
          autoComplete="off"
          spellCheck={false}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
        />
        <kbd className="key-hint">Esc</kbd>
      </div>
      <div className="search-section">
        {trimmed ? "搜尋結果" : "最近的對話"}
        {searching && found && (
          <span className="dot pulse" aria-hidden="true" />
        )}
      </div>
      <div
        id={listId}
        className="search-results"
        role="listbox"
        aria-label="對話"
      >
        {results.map((item, index) => (
          <a
            key={item.id}
            id={optionId(index)}
            href={`/s/${item.id}`}
            role="option"
            tabIndex={-1}
            aria-selected={index === current}
            className={index === current ? "active" : ""}
            // 依序出現用的序號，見 styles.css 的 .search-results a。
            style={{ "--i": index } as CSSProperties}
            onMouseMove={() => setActive(index)}
            onClick={(event) => onLink(event, item)}
          >
            <span className="search-title">
              {highlight(item.title, trimmed)}
            </span>
            {item.snippet && (
              <span className="search-snippet">
                {highlight(item.snippet, trimmed)}
              </span>
            )}
            <span className="search-time">
              {formatAgo(item.updatedAt, now)}
            </span>
          </a>
        ))}
      </div>
      {status && (
        <p
          className={`search-status ${error ? "inline-error" : ""}`}
          role="status"
        >
          {status}
        </p>
      )}
    </Modal>
  );
}

/** 把文字裡的關鍵字（不分大小寫）用 <mark> 標出來。 */
function highlight(text: string, query: string): ReactNode {
  if (!query) return text;
  const lower = text.toLowerCase();
  const target = query.toLowerCase();
  const parts: ReactNode[] = [];
  let start = 0;
  let at = lower.indexOf(target);
  while (at !== -1) {
    if (at > start) parts.push(text.slice(start, at));
    parts.push(<mark key={at}>{text.slice(at, at + target.length)}</mark>);
    start = at + target.length;
    at = lower.indexOf(target, start);
  }
  if (parts.length === 0) return text;
  parts.push(text.slice(start));
  return parts;
}
