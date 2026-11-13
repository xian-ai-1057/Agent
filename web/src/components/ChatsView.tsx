/**
 * 對話紀錄與專案頁（features.categories）。
 *
 * 對話紀錄（/chats）列出所有對話，可以搜尋標題與內容、依專案篩選；
 * 勾選好幾段對話後可以一起刪除，或移到某個專案。專案頁（/p/:id）列出
 * 一個專案裡的對話，可以在這個專案開新對話、改名或刪除專案（刪除專案
 * 不刪對話）。
 */

import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import type {
  SearchResult,
  SessionSummary,
} from "../../../src/web/protocol.ts";
import { api } from "../api.ts";
import { formatAgo, isBusy, useNow } from "../format.ts";
import { navigate } from "../router.ts";
import {
  bulkSessions,
  deleteCategory,
  renameCategory,
  useStore,
} from "../store.ts";
import { CategoryNameDialog, MoveDialog } from "./CategoryDialogs.tsx";
import {
  CloseIcon,
  FolderIcon,
  MoreIcon,
  MoveIcon,
  PenIcon,
  SearchIcon,
  TrashIcon,
} from "./Icons.tsx";
import { Link } from "./Sidebar.tsx";
import { ConfirmDialog } from "./shell/Dialog.tsx";
import { FloatingMenu } from "./shell/FloatingMenu.tsx";
import { showToast } from "./shell/toast.tsx";

/** 篩選：全部、未分類，或某個專案的 id。 */
type Filter = "all" | "none" | string;

export function ChatsView() {
  useEffect(() => {
    document.title = "對話紀錄 · Harness";
  }, []);
  return (
    <Page title="對話紀錄">
      <ChatBrowser />
    </Page>
  );
}

export function CategoryView({ id }: { id: string }) {
  const category = useStore((s) => s.categories.find((item) => item.id === id));
  const loaded = useStore((s) => s.connected);
  const [menu, setMenu] = useState<HTMLElement>();
  const [renaming, setRenaming] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    document.title = `${category?.name ?? "專案"} · Harness`;
  }, [category?.name]);

  if (!category) {
    return (
      <Page title="專案">
        <p className="empty-state anim-in">
          {loaded ? "找不到這個專案，可能已經被刪除。" : "讀取中…"}{" "}
          {loaded && <Link to="/chats">看所有對話</Link>}
        </p>
      </Page>
    );
  }

  return (
    <Page
      title={category.name}
      icon={<FolderIcon size={18} />}
      actions={
        <>
          <button
            type="button"
            className="btn btn-accent"
            onClick={() => navigate(`/?category=${encodeURIComponent(id)}`)}
          >
            <PenIcon size={15} />
            在這個專案開新對話
          </button>
          <button
            type="button"
            className="icon-btn"
            aria-label="專案選項"
            aria-haspopup="menu"
            aria-expanded={Boolean(menu)}
            onClick={(event) => setMenu(menu ? undefined : event.currentTarget)}
          >
            <MoreIcon />
          </button>
        </>
      }
    >
      <ChatBrowser category={id} />
      {menu && (
        <FloatingMenu
          anchor={menu}
          label="專案選項"
          onClose={() => setMenu(undefined)}
        >
          <button
            type="button"
            role="menuitem"
            className="menu-item"
            onClick={() => {
              setMenu(undefined);
              setRenaming(true);
            }}
          >
            <PenIcon size={15} />
            重新命名
          </button>
          <button
            type="button"
            role="menuitem"
            className="menu-item danger"
            onClick={() => {
              setMenu(undefined);
              setDeleting(true);
            }}
          >
            <TrashIcon size={15} />
            刪除專案
          </button>
        </FloatingMenu>
      )}
      {renaming && (
        <CategoryNameDialog
          title="重新命名專案"
          initial={category.name}
          confirmLabel="儲存"
          onSubmit={(name) => renameCategory(id, name)}
          onClose={() => setRenaming(false)}
        />
      )}
      {deleting && (
        <ConfirmDialog
          title="刪除這個專案？"
          confirmLabel="刪除專案"
          danger
          onConfirm={async () => {
            await deleteCategory(id);
            navigate("/chats", { replace: true });
            showToast(`已刪除專案「${category.name}」，對話移到未分類`);
          }}
          onClose={() => setDeleting(false)}
        >
          <p>
            「{category.name}」裡的 {category.sessionCount}{" "}
            段對話不會被刪除，會移到未分類。
          </p>
        </ConfirmDialog>
      )}
    </Page>
  );
}

function Page({
  title,
  icon,
  actions,
  children,
}: {
  title: string;
  icon?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <main className="main">
      <header className="main-header">
        <div className="main-title">
          <h1>{title}</h1>
        </div>
      </header>
      <div className="scroll">
        <div className="page chats-page">
          <div className="page-head">
            <h2 className="chats-title">
              {icon}
              {title}
            </h2>
            {actions && <div className="chats-actions">{actions}</div>}
          </div>
          {children}
        </div>
      </div>
    </main>
  );
}

/** 對話清單：搜尋、篩選、勾選後批次處理。category 有值時只列這個專案。 */
function ChatBrowser({ category }: { category?: string }) {
  const sessions = useStore((s) => s.sessions);
  const categories = useStore((s) => s.categories);
  const canSearch = useStore((s) => s.project?.features?.search === true);
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<Map<string, SearchResult>>();
  const [filter, setFilter] = useState<Filter>("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [moving, setMoving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const now = useNow(false);
  const allRef = useRef<HTMLInputElement>(null);

  // 搜尋標題與對話內容：伺服器支援時交給它（也比對訊息），否則只比對標題。
  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed || !canSearch) {
      setFound(undefined);
      return;
    }
    let alive = true;
    const timer = setTimeout(() => {
      api
        .searchSessions(trimmed)
        .then((results) => {
          if (alive) setFound(new Map(results.map((r) => [r.id, r])));
        })
        .catch(() => undefined);
    }, 200);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [query, canSearch]);

  const names = useMemo(
    () => new Map(categories.map((item) => [item.id, item.name])),
    [categories],
  );
  const scope = category ?? filter;
  const trimmed = query.trim().toLowerCase();
  const list = Object.values(sessions)
    .filter((item) => {
      if (scope === "none") return !item.category || !names.has(item.category);
      if (scope !== "all") return item.category === scope;
      return true;
    })
    .filter((item) => {
      if (!trimmed) return true;
      if (found) return found.has(item.id);
      return item.title.toLowerCase().includes(trimmed);
    })
    .sort((a, b) => b.updatedAt - a.updatedAt);

  // 篩選或搜尋改變後，看不到的對話不再算在勾選裡。
  const visible = new Set(list.map((item) => item.id));
  const chosen = [...selected].filter((id) => visible.has(id));
  const allChosen = list.length > 0 && chosen.length === list.length;

  useEffect(() => {
    if (allRef.current) {
      allRef.current.indeterminate = chosen.length > 0 && !allChosen;
    }
  });

  const toggle = (id: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const toggleAll = () =>
    setSelected(allChosen ? new Set() : new Set(list.map((item) => item.id)));

  const shared = chosen.map((id) => sessions[id]?.category);
  const sameCategory =
    shared.length > 0 && shared.every((value) => value === shared[0])
      ? (shared[0] ?? null)
      : undefined;
  const busyCount = chosen.filter((id) => isBusy(sessions[id])).length;

  const report = (verb: string, done: number, skipped: number) => {
    const extra = skipped
      ? `，${skipped} 段沒有處理（還在執行或已不存在）`
      : "";
    showToast(`已${verb} ${done} 段對話${extra}`, skipped ? "error" : "info");
  };

  return (
    <div className="chats">
      <div className="chats-tools">
        <label className="chats-search">
          <SearchIcon size={15} />
          <input
            type="search"
            value={query}
            placeholder={canSearch ? "搜尋對話標題或內容" : "搜尋對話標題"}
            aria-label="搜尋對話"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        {!category && categories.length > 0 && (
          <select
            className="field chats-filter"
            aria-label="依專案篩選"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          >
            <option value="all">所有對話</option>
            <option value="none">未分類</option>
            {categories.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        )}
      </div>

      <div
        className={`chats-bar ${chosen.length ? "selecting" : ""}`}
        role="toolbar"
        aria-label="選取的對話"
      >
        <label className="chats-check">
          <input
            ref={allRef}
            type="checkbox"
            checked={allChosen}
            disabled={list.length === 0}
            onChange={toggleAll}
            aria-label="全選"
          />
        </label>
        {chosen.length ? (
          <>
            <span className="chats-count">已選取 {chosen.length} 段</span>
            <button
              type="button"
              className="btn btn-outline btn-small"
              onClick={() => setMoving(true)}
            >
              <MoveIcon size={15} />
              移到專案
            </button>
            <button
              type="button"
              className="btn btn-outline btn-small danger-text"
              onClick={() => setDeleting(true)}
            >
              <TrashIcon size={15} />
              刪除
            </button>
            <button
              type="button"
              className="icon-btn"
              aria-label="取消選取"
              onClick={() => setSelected(new Set())}
            >
              <CloseIcon size={15} />
            </button>
          </>
        ) : (
          <span className="chats-count subtle">
            {trimmed ? `找到 ${list.length} 段對話` : `${list.length} 段對話`}
          </span>
        )}
      </div>

      {list.length === 0 ? (
        <p className="empty-state anim-in">
          {trimmed
            ? "找不到符合的對話。"
            : category
              ? "這個專案還沒有對話。按「在這個專案開新對話」開始，或從對話紀錄把對話移進來。"
              : "還沒有對話。"}
        </p>
      ) : (
        <ul className="chats-list">
          {list.map((item) => (
            <ChatRow
              key={item.id}
              summary={item}
              snippet={found?.get(item.id)?.snippet}
              categoryName={
                !category && item.category
                  ? names.get(item.category)
                  : undefined
              }
              checked={selected.has(item.id)}
              now={now}
              onToggle={() => toggle(item.id)}
            />
          ))}
        </ul>
      )}

      {moving && (
        <MoveDialog
          count={chosen.length}
          current={sameCategory}
          onMove={async (target) => {
            const result = await bulkSessions("move", chosen, target);
            const name = target ? names.get(target) : undefined;
            setSelected(new Set());
            if (result.skipped.length) {
              report("移動", result.done.length, result.skipped.length);
            } else {
              showToast(
                name
                  ? `已把 ${result.done.length} 段對話移到「${name}」`
                  : `已把 ${result.done.length} 段對話移出專案`,
              );
            }
          }}
          onClose={() => setMoving(false)}
        />
      )}
      {deleting && (
        <ConfirmDialog
          title={`刪除 ${chosen.length} 段對話？`}
          confirmLabel="刪除對話"
          danger
          disabled={busyCount === chosen.length}
          onConfirm={async () => {
            const result = await bulkSessions("delete", chosen);
            setSelected(new Set());
            report("刪除", result.done.length, result.skipped.length);
          }}
          onClose={() => setDeleting(false)}
        >
          <p>
            這些對話的訊息與紀錄會一起刪除，<strong>無法復原</strong>。
            {busyCount > 0 &&
              ` 其中 ${busyCount} 段還在執行或等你確認，不會被刪除。`}
          </p>
        </ConfirmDialog>
      )}
    </div>
  );
}

function ChatRow({
  summary,
  snippet,
  categoryName,
  checked,
  now,
  onToggle,
}: {
  summary: SessionSummary;
  snippet?: string;
  categoryName?: string;
  checked: boolean;
  now: number;
  onToggle: () => void;
}) {
  const busy = isBusy(summary);
  return (
    <li className={`chats-row ${checked ? "checked" : ""}`}>
      <label className="chats-check">
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          aria-label={`選取「${summary.title}」`}
        />
      </label>
      <Link to={`/s/${summary.id}`} className="chats-main">
        <span className="chats-row-title">
          {summary.title}
          {busy && (
            <span className="chats-live">
              {summary.status === "waiting" ? "等你確認" : "執行中"}
            </span>
          )}
        </span>
        {snippet && <span className="chats-snippet">{snippet}</span>}
      </Link>
      {categoryName && (
        <span className="chats-chip" title={`專案：${categoryName}`}>
          <FolderIcon size={13} />
          {categoryName}
        </span>
      )}
      <span className="chats-time">{formatAgo(summary.updatedAt, now)}</span>
    </li>
  );
}
