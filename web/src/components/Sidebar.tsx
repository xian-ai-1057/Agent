/**
 * 左側欄：新對話、搜尋、工作中、對話紀錄、專案、最近的對話、設定。
 * 管理者另外有「管理平台」的入口（要登入時在帳號選單裡）。
 *
 * 伺服器支援專案時（features.categories），「專案」列出自己建立的專案，
 * 點了打開專案頁，旁邊的 ＋ 新增專案；對話的 ⋮ 選單可以移到專案。不支援
 * 時只顯示 harness 專案的名稱。
 *
 * 對話依最後更新時間分成今天、昨天、過去 7 天、更早。伺服器支援時
 * （Python 版），每段對話有 ⋮ 選單可以改名或刪除。窄螢幕時側欄變成
 * 從左邊滑出的抽屜，開關由 App 控制。
 */

import {
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
  useEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import type { SessionSummary } from "../../../src/web/protocol.ts";
import { canAdmin, currentUser } from "../auth.ts";
import { navigate, type Route } from "../router.ts";
import {
  createCategory,
  deleteSession,
  moveSession,
  renameSession,
  useStore,
} from "../store.ts";
import { CategoryNameDialog, MoveDialog } from "./CategoryDialogs.tsx";
import {
  AddIcon,
  ChatsIcon,
  FolderIcon,
  GearIcon,
  ListIcon,
  MoreIcon,
  MoveIcon,
  PenIcon,
  SearchIcon,
  ShieldIcon,
  TrashIcon,
} from "./Icons.tsx";
import { ConfirmDialog } from "./shell/Dialog.tsx";
import { FloatingMenu } from "./shell/FloatingMenu.tsx";
import { showToast } from "./shell/toast.tsx";
import { UserMenu } from "./shell/UserMenu.tsx";

/** 用 <a> 讓中鍵、複製連結都正常，點擊時改由前端路由處理。 */
export function Link({
  to,
  className,
  children,
  current,
}: {
  to: string;
  className?: string;
  children: ReactNode;
  current?: boolean;
}) {
  const onClick = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0)
      return;
    event.preventDefault();
    navigate(to);
  };
  return (
    <a
      href={to}
      className={className}
      onClick={onClick}
      aria-current={current ? "page" : undefined}
    >
      {children}
    </a>
  );
}

/** Mac 顯示 ⌘K，其他系統顯示 Ctrl K。 */
export const SEARCH_SHORTCUT = /Mac|iPhone|iPad/.test(navigator.userAgent)
  ? "⌘K"
  : "Ctrl K";

const DAY = 24 * 60 * 60 * 1000;

/** 依最後更新時間分組，空的組不列出。list 要先由新到舊排好。 */
function groupByDay(list: SessionSummary[]): [string, SessionSummary[]][] {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const start = today.getTime();
  const groups: [string, number, SessionSummary[]][] = [
    ["今天", start, []],
    ["昨天", start - DAY, []],
    ["過去 7 天", start - 6 * DAY, []],
    ["更早", Number.NEGATIVE_INFINITY, []],
  ];
  for (const item of list) {
    const group = groups.find(([, from]) => item.updatedAt >= from);
    group?.[2].push(item);
  }
  return groups
    .filter(([, , items]) => items.length > 0)
    .map(([label, , items]) => [label, items]);
}

export function Sidebar({
  route,
  open = false,
  onSearch,
}: {
  route: Route;
  /** 窄螢幕時抽屜是否打開。 */
  open?: boolean;
  /** 打開搜尋對話框；沒有時不顯示「搜尋對話」。 */
  onSearch?: () => void;
}) {
  const project = useStore((s) => s.project);
  const sessions = useStore((s) => s.sessions);
  const navRef = useRef<HTMLElement>(null);
  const [menu, setMenu] = useState<{ id: string; anchor: HTMLElement }>();
  const [renaming, setRenaming] = useState<string>();
  const [deleting, setDeleting] = useState<SessionSummary>();
  const [moving, setMoving] = useState<SessionSummary>();
  const [creating, setCreating] = useState(false);
  const categories = useStore((s) => s.categories);
  const grouping = project?.features?.categories === true;
  const list = Object.values(sessions).sort(
    (a, b) => b.updatedAt - a.updatedAt,
  );
  const active = list.filter(
    (item) => item.status === "running" || item.status === "waiting",
  ).length;
  const currentId = route.page === "session" ? route.id : undefined;
  const admin = project?.features?.sessionAdmin === true;
  // 用最新的狀態判斷：對話框開著時對話可能剛開始或停止執行。
  const deletingStatus = deleting
    ? (sessions[deleting.id] ?? deleting).status
    : undefined;
  const deleteBlocked =
    deletingStatus === "running" || deletingStatus === "waiting";

  // 抽屜打開時把焦點移進來，鍵盤使用者才不會留在後面的畫面。
  useEffect(() => {
    if (open) navRef.current?.focus();
  }, [open]);

  return (
    <nav
      ref={navRef}
      id="sidebar"
      className={`sidebar ${open ? "open" : ""}`}
      aria-label="專案與對話"
      tabIndex={-1}
    >
      <div className="side-brand">
        <span className="logo" aria-hidden="true" />
        Harness
        {grouping && project && (
          <span className="side-brand-project" title="harness 專案資料夾">
            {project.name}
          </span>
        )}
      </div>
      <Link
        to="/"
        className={`side-item ${route.page === "new" ? "active" : ""}`}
        current={route.page === "new"}
      >
        <PenIcon />
        <span className="grow">新對話</span>
      </Link>
      {onSearch && (
        <button type="button" className="side-item" onClick={onSearch}>
          <SearchIcon />
          <span className="grow">搜尋對話</span>
          <kbd className="key-hint">{SEARCH_SHORTCUT}</kbd>
        </button>
      )}
      <Link
        to="/work"
        className={`side-item ${route.page === "work" ? "active" : ""}`}
        current={route.page === "work"}
      >
        <ListIcon />
        <span className="grow">工作中</span>
        {active > 0 && <span className="hint">{active}</span>}
      </Link>
      {grouping && (
        <Link
          to="/chats"
          className={`side-item ${route.page === "chats" ? "active" : ""}`}
          current={route.page === "chats"}
        >
          <ChatsIcon />
          <span className="grow">對話紀錄</span>
        </Link>
      )}

      <div className="side-sessions">
        {grouping ? (
          <>
            <div className="side-heading side-heading-row">
              <span className="grow">專案</span>
              <button
                type="button"
                className="side-add"
                aria-label="新增專案"
                title="新增專案"
                onClick={() => setCreating(true)}
              >
                <AddIcon size={14} />
              </button>
            </div>
            {categories.length === 0 ? (
              <button
                type="button"
                className="side-item side-hint"
                onClick={() => setCreating(true)}
              >
                <AddIcon size={15} />
                <span className="grow">新增專案來整理對話</span>
              </button>
            ) : (
              <ul className="side-list side-categories">
                {categories.map((item) => {
                  const here =
                    route.page === "category" && route.id === item.id;
                  return (
                    <li key={item.id} className="side-row">
                      <Link
                        to={`/p/${item.id}`}
                        className={`side-item ${here ? "active" : ""}`}
                        current={here}
                      >
                        <FolderIcon size={15} />
                        <span className="grow">{item.name}</span>
                        {item.sessionCount > 0 && (
                          <span className="hint">{item.sessionCount}</span>
                        )}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            )}
            <div className="side-heading">最近的對話</div>
          </>
        ) : (
          <>
            <div className="side-heading">專案</div>
            <div className="side-project">
              <FolderIcon size={15} />
              <span className="grow">{project?.name ?? "…"}</span>
            </div>
          </>
        )}
        {list.length === 0 && <p className="side-empty">還沒有對話</p>}
        {groupByDay(list).map(([label, items]) => (
          <section key={label} className="side-group" aria-label={label}>
            <h3 className="side-group-label">{label}</h3>
            <ul className="side-list">
              {items.map((item) => (
                <SessionRow
                  key={item.id}
                  summary={item}
                  current={item.id === currentId}
                  admin={admin}
                  renaming={renaming === item.id}
                  menuOpen={menu?.id === item.id}
                  onMenu={(anchor) =>
                    setMenu(
                      menu?.id === item.id
                        ? undefined
                        : { id: item.id, anchor },
                    )
                  }
                  onRenameEnd={() => setRenaming(undefined)}
                />
              ))}
            </ul>
          </section>
        ))}
      </div>

      <div className="side-footer">
        <Link
          to="/settings/tools"
          className={`side-item ${route.page === "settings" ? "active" : ""}`}
          current={route.page === "settings"}
        >
          <GearIcon />
          <span className="grow">設定</span>
        </Link>
        {/* 要登入時管理平台在帳號選單裡；不必登入（只有自己一個人）時沒有
            帳號選單，直接放在這裡。 */}
        {canAdmin(project) && !currentUser(project) && (
          <Link to="/admin" className="side-item">
            <ShieldIcon />
            <span className="grow">管理平台</span>
          </Link>
        )}
        <UserMenu />
      </div>

      {menu && (
        <SessionMenu
          anchor={menu.anchor}
          onClose={() => setMenu(undefined)}
          onRename={() => {
            setRenaming(menu.id);
            setMenu(undefined);
          }}
          onDelete={() => {
            setDeleting(sessions[menu.id]);
            setMenu(undefined);
          }}
          onMove={
            grouping
              ? () => {
                  setMoving(sessions[menu.id]);
                  setMenu(undefined);
                }
              : undefined
          }
        />
      )}
      {moving &&
        createPortal(
          <MoveDialog
            count={1}
            current={moving.category ?? null}
            onMove={async (category) => {
              await moveSession(moving.id, category);
              const name = categories.find((c) => c.id === category)?.name;
              showToast(name ? `已移到「${name}」` : "已移出專案");
            }}
            onClose={() => setMoving(undefined)}
          />,
          document.body,
        )}
      {creating &&
        createPortal(
          <CategoryNameDialog
            title="新增專案"
            confirmLabel="新增"
            onSubmit={async (name) => {
              const created = await createCategory(name);
              navigate(`/p/${created.id}`);
            }}
            onClose={() => setCreating(false)}
          />,
          document.body,
        )}
      {deleting &&
        // 放在 body 底下：刪除正在看的對話會換頁並收起抽屜，對話框不能跟著被藏起來。
        createPortal(
          <ConfirmDialog
            title="刪除這段對話？"
            confirmLabel="刪除對話"
            danger
            disabled={deleteBlocked}
            onConfirm={() => deleteSession(deleting.id)}
            onClose={() => setDeleting(undefined)}
          >
            {deleteBlocked ? (
              <p>
                「{deleting.title}」
                {deletingStatus === "waiting" ? "正在等你確認" : "還在執行"}
                ，要先停止才能刪除。
              </p>
            ) : (
              <p>
                「{deleting.title}」的訊息與紀錄會一起刪除，
                <strong>無法復原</strong>。
              </p>
            )}
          </ConfirmDialog>,
          document.body,
        )}
    </nav>
  );
}

function SessionRow({
  summary,
  current,
  admin,
  renaming,
  menuOpen,
  onMenu,
  onRenameEnd,
}: {
  summary: SessionSummary;
  current: boolean;
  admin: boolean;
  renaming: boolean;
  menuOpen: boolean;
  onMenu: (anchor: HTMLElement) => void;
  onRenameEnd: () => void;
}) {
  if (renaming) {
    return (
      <li className="side-row editing">
        <RenameInput summary={summary} onDone={onRenameEnd} />
      </li>
    );
  }
  return (
    <li className={`side-row ${current ? "current" : ""}`}>
      <Link
        to={`/s/${summary.id}`}
        className={`side-item ${current ? "active" : ""} ${admin ? "with-more" : ""}`}
        current={current}
      >
        <span className="grow">{summary.title}</span>
        <StatusMark summary={summary} />
      </Link>
      {admin && (
        <button
          type="button"
          className="side-more"
          aria-label={`「${summary.title}」的選項`}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={(event) => onMenu(event.currentTarget)}
        >
          <MoreIcon />
        </button>
      )}
    </li>
  );
}

/** 對話名稱的長度上限，和伺服器的 _CUSTOM_TITLE_LIMIT 一致。 */
const TITLE_LIMIT = 100;

/** 就地改名：Enter 或離開輸入框時儲存，Esc 取消。 */
function RenameInput({
  summary,
  onDone,
}: {
  summary: SessionSummary;
  onDone: () => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  // Enter 儲存後輸入框消失也會觸發 blur，用這個避免存兩次。
  const finished = useRef(false);

  useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);

  const finish = (save: boolean) => {
    if (finished.current) return;
    finished.current = true;
    // 伺服器只收 1 到 100 個字（去掉前後空白後）；renameSession 也會去空白。
    const title = (ref.current?.value ?? "").trim().slice(0, TITLE_LIMIT);
    onDone();
    if (!save) return;
    renameSession(summary.id, title).catch((error: Error) =>
      showToast(`改名失敗：${error.message}`, "error"),
    );
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" && !event.nativeEvent.isComposing) {
      event.preventDefault();
      finish(true);
    } else if (event.key === "Escape") {
      event.preventDefault();
      // 不要讓 Esc 同時關掉窄螢幕的抽屜。
      event.stopPropagation();
      finish(false);
    }
  };

  return (
    <input
      ref={ref}
      className="side-rename"
      aria-label="對話名稱"
      defaultValue={summary.title}
      maxLength={TITLE_LIMIT}
      onKeyDown={onKeyDown}
      onBlur={() => finish(true)}
    />
  );
}

/** ⋮ 按鈕打開的小選單。 */
function SessionMenu({
  anchor,
  onClose,
  onRename,
  onDelete,
  onMove,
}: {
  anchor: HTMLElement;
  onClose: () => void;
  onRename: () => void;
  onDelete: () => void;
  /** 移到專案；伺服器不支援專案時沒有。 */
  onMove?: () => void;
}) {
  return (
    <FloatingMenu anchor={anchor} label="對話選項" onClose={onClose}>
      <button
        type="button"
        role="menuitem"
        className="menu-item"
        onClick={onRename}
      >
        <PenIcon size={15} />
        重新命名
      </button>
      {onMove && (
        <button
          type="button"
          role="menuitem"
          className="menu-item"
          onClick={onMove}
        >
          <MoveIcon size={15} />
          移到專案
        </button>
      )}
      <button
        type="button"
        role="menuitem"
        className="menu-item danger"
        onClick={onDelete}
      >
        <TrashIcon size={15} />
        刪除
      </button>
    </FloatingMenu>
  );
}

function StatusMark({ summary }: { summary: SessionSummary }) {
  if (summary.status === "running") {
    return (
      <>
        <span className="dot pulse" aria-hidden="true" />
        <span className="sr-only">執行中</span>
      </>
    );
  }
  if (summary.status === "waiting") {
    return <span className="badge badge-accent anim-pop">待確認</span>;
  }
  if (summary.status === "error") {
    return <span className="badge badge-danger anim-pop">出錯</span>;
  }
  return null;
}
