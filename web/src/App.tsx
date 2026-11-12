/**
 * 整個網頁：左側欄加上依網址切換的主畫面。
 *
 * 窄螢幕時側欄收成抽屜，上方改顯示一條頂端列（選單、目前頁面名稱、
 * 新對話）。⌘K / Ctrl+K 打開搜尋對話（伺服器支援時）。
 *
 * 多人帳號模式下任何 API 回 401 時只顯示登入畫面；網址不變，登入後
 * 回到原本的頁面。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Conversation } from "./components/Conversation.tsx";
import { MenuIcon, PenIcon } from "./components/Icons.tsx";
import { NewChat } from "./components/NewChat.tsx";
import { Settings } from "./components/Settings.tsx";
import { Link, Sidebar } from "./components/Sidebar.tsx";
import { Login } from "./components/shell/Login.tsx";
import { SearchDialog } from "./components/shell/SearchDialog.tsx";
import { Toaster } from "./components/shell/toast.tsx";
import { WorkView } from "./components/WorkView.tsx";
import { type Route, subscribe, useRoute } from "./router.ts";
import { connect, type State, useStore } from "./store.ts";

/** 頂端列顯示的頁面名稱。 */
function pageTitle(route: Route, state: State): string {
  if (route.page === "session")
    return state.sessions[route.id]?.title ?? "對話";
  if (route.page === "work") return "工作中";
  if (route.page === "settings") return "設定";
  return "新對話";
}

export function App() {
  const needsLogin = useStore((s) => s.needsLogin);
  // 要登入時不連 SSE；登入後重新連上，伺服器的 hello 會讓畫面重新讀取。
  useEffect(() => (needsLogin ? undefined : connect()), [needsLogin]);
  if (needsLogin) {
    return (
      <>
        <Login />
        <Toaster />
      </>
    );
  }
  return <Shell />;
}

function Shell() {
  const route = useRoute();
  const connected = useStore((s) => s.connected);
  const loadError = useStore((s) => s.loadError);
  const searchEnabled = useStore((s) => s.project?.features?.search === true);
  const title = useStore((s) => pageTitle(route, s));
  const [drawer, setDrawer] = useState(false);
  const [searching, setSearching] = useState(false);
  const menuRef = useRef<HTMLButtonElement>(null);

  // 換頁時收起抽屜。
  useEffect(() => subscribe(() => setDrawer(false)), []);

  const closeDrawer = useCallback(() => {
    setDrawer(false);
    menuRef.current?.focus();
  }, []);

  // 抽屜收起時（換頁、點背景、Esc），焦點還留在抽屜裡就移回選單按鈕，
  // 不然鍵盤使用者會停在看不到的抽屜裡。焦點已經移到別處（例如搜尋
  // 對話框）時不動它。
  useEffect(() => {
    if (!drawer) return;
    return () => {
      const focused = document.activeElement;
      const sidebar = document.getElementById("sidebar");
      if (!focused || focused === document.body || sidebar?.contains(focused))
        menuRef.current?.focus();
    };
  }, [drawer]);

  // 視窗拉寬後抽屜變回一般側欄，主畫面不能還停在 inert。
  useEffect(() => {
    if (!drawer) return;
    const narrow = window.matchMedia("(max-width: 760px)");
    const onChange = () => {
      if (!narrow.matches) setDrawer(false);
    };
    narrow.addEventListener("change", onChange);
    return () => narrow.removeEventListener("change", onChange);
  }, [drawer]);

  useEffect(() => {
    if (!drawer) return;
    const onKey = (event: KeyboardEvent) => {
      // 對話框開著時 Esc 是關對話框。
      if (event.key !== "Escape" || document.querySelector("dialog[open]"))
        return;
      closeDrawer();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawer, closeDrawer]);

  useEffect(() => {
    if (!searchEnabled) return;
    const onKey = (event: KeyboardEvent) => {
      if (
        (event.metaKey || event.ctrlKey) &&
        !event.altKey &&
        event.key.toLowerCase() === "k"
      ) {
        event.preventDefault();
        setSearching(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [searchEnabled]);

  return (
    <div className={`app ${drawer ? "drawer-open" : ""}`}>
      {/* 抽屜打開時頂端列和主畫面都設成 inert：Tab 與螢幕閱讀器只會停在抽屜裡。 */}
      <header className="topbar" inert={drawer}>
        <button
          ref={menuRef}
          type="button"
          className="icon-btn"
          aria-label="開啟選單"
          aria-controls="sidebar"
          aria-expanded={drawer}
          onClick={() => setDrawer(true)}
        >
          <MenuIcon />
        </button>
        <span className="topbar-title">{title}</span>
        <Link to="/" className="icon-btn">
          <PenIcon size={18} />
          <span className="sr-only">新對話</span>
        </Link>
      </header>
      <Sidebar
        route={route}
        open={drawer}
        onSearch={
          searchEnabled
            ? () => {
                setDrawer(false);
                setSearching(true);
              }
            : undefined
        }
      />
      {drawer && (
        <button
          type="button"
          className="scrim"
          aria-label="關閉選單"
          tabIndex={-1}
          onClick={closeDrawer}
        />
      )}
      {/* display: contents，不影響版面，只用來一次把主畫面設成 inert。 */}
      <div className="app-content" inert={drawer}>
        {route.page === "new" && <NewChat />}
        {route.page === "session" && (
          // 換對話時重新掛載，主畫面的淡入動畫才會重播。
          <Conversation key={route.id} id={route.id} />
        )}
        {route.page === "work" && <WorkView />}
        {route.page === "settings" && <Settings section={route.section} />}
      </div>
      {searching && <SearchDialog onClose={() => setSearching(false)} />}
      <Toaster />
      {(!connected || loadError) && (
        <div className="banner banner-fixed" role="status">
          {loadError ?? "和 harness web 的連線中斷了，正在重新連線…"}
        </div>
      )}
    </div>
  );
}
