/** 左側欄：新對話、工作中、專案的對話列表、設定。 */

import type { MouseEvent, ReactNode } from "react";
import type { SessionSummary } from "../../../src/web/protocol.ts";
import { navigate, type Route } from "../router.ts";
import { useStore } from "../store.ts";
import { FolderIcon, GearIcon, ListIcon, PenIcon } from "./Icons.tsx";

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

export function Sidebar({ route }: { route: Route }) {
  const project = useStore((s) => s.project);
  const sessions = useStore((s) => s.sessions);
  const list = Object.values(sessions).sort(
    (a, b) => b.updatedAt - a.updatedAt,
  );
  const active = list.filter(
    (item) => item.status === "running" || item.status === "waiting",
  ).length;
  const currentId = route.page === "session" ? route.id : undefined;

  return (
    <nav className="sidebar" aria-label="專案與對話">
      <div className="side-brand">
        <span className="logo" aria-hidden="true" />
        Harness
      </div>
      <Link
        to="/"
        className={`side-item ${route.page === "new" ? "active" : ""}`}
        current={route.page === "new"}
      >
        <PenIcon />
        <span className="grow">新對話</span>
      </Link>
      <Link
        to="/work"
        className={`side-item ${route.page === "work" ? "active" : ""}`}
        current={route.page === "work"}
      >
        <ListIcon />
        <span className="grow">工作中</span>
        {active > 0 && <span className="hint">{active}</span>}
      </Link>

      <div className="side-heading">專案</div>
      <div className="side-project">
        <FolderIcon size={15} />
        {project?.name ?? "…"}
      </div>
      <ul className="side-sessions">
        {list.length === 0 && <li className="side-empty">還沒有對話</li>}
        {list.map((item) => (
          <li key={item.id}>
            <Link
              to={`/s/${item.id}`}
              className={`side-item ${item.id === currentId ? "active" : ""}`}
              current={item.id === currentId}
            >
              <span className="grow">{item.title}</span>
              <StatusMark summary={item} />
            </Link>
          </li>
        ))}
      </ul>

      <div className="side-footer">
        <Link
          to="/settings/permissions"
          className={`side-item ${route.page === "settings" ? "active" : ""}`}
          current={route.page === "settings"}
        >
          <GearIcon />
          <span className="grow">設定</span>
        </Link>
      </div>
    </nav>
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
