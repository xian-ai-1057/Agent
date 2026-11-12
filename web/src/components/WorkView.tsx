/** 工作中：同時進行的對話一覽，可以直接允許、拒絕或停止。 */

import { useEffect, useState } from "react";
import {
  type ApprovalDecision,
  INTERRUPTED_STOP_REASON,
  type SessionSummary,
} from "../../../src/web/protocol.ts";
import { api } from "../api.ts";
import { activityLabel, formatAgo, formatDuration, useNow } from "../format.ts";
import { useStore } from "../store.ts";
import { CheckIcon, CrossIcon, ShieldIcon } from "./Icons.tsx";
import { Link } from "./Sidebar.tsx";
import { showToast } from "./shell/toast.tsx";

type Filter = "all" | "waiting" | "running" | "finished";

const RANK: Record<SessionSummary["status"], number> = {
  waiting: 0,
  error: 1,
  running: 2,
  stopped: 3,
  done: 3,
  idle: 4,
};

export function WorkView() {
  const sessions = useStore((s) => s.sessions);
  const [filter, setFilter] = useState<Filter>("all");

  useEffect(() => {
    document.title = "工作中 · Harness";
  }, []);

  // 只列出這次啟動後跑過的對話。
  const list = Object.values(sessions)
    .filter((item) => item.status !== "idle")
    .sort(
      (a, b) => RANK[a.status] - RANK[b.status] || b.updatedAt - a.updatedAt,
    );
  const waiting = list.filter((item) => item.status === "waiting");
  const running = list.filter((item) => item.status === "running");
  const finished = list.filter(
    (item) => item.status !== "waiting" && item.status !== "running",
  );
  const now = useNow(running.length + waiting.length > 0);

  const shown = { all: list, waiting, running, finished }[filter];
  let headline = "目前沒有進行中的對話";
  if (running.length) headline = `${running.length} 個對話執行中`;
  if (waiting.length) headline = `${waiting.length} 個對話在等你確認`;

  const filters: [Filter, string, number][] = [
    ["all", "全部", list.length],
    ["waiting", "需要你", waiting.length],
    ["running", "執行中", running.length],
    ["finished", "已結束", finished.length],
  ];

  return (
    <main className="main">
      <header className="main-header">
        <div className="main-title">
          <h1>工作中</h1>
        </div>
      </header>
      <div className="scroll">
        <div className="page">
          <div className="page-head">
            <div>
              <h2 key={headline} className="anim-swap">
                {headline}
              </h2>
              <p>每個對話各自執行，需要你確認的會排在最上面。</p>
            </div>
            <div className="segmented">
              {filters.map(([id, label, count]) => (
                <button
                  key={id}
                  type="button"
                  aria-pressed={filter === id}
                  onClick={() => setFilter(id)}
                >
                  {label}
                  <span className="n">{count}</span>
                </button>
              ))}
            </div>
          </div>

          {shown.length === 0 ? (
            <p className="empty-state anim-in">
              {list.length === 0
                ? "開始對話後，正在跑的、等你確認的都會列在這裡。"
                : "這個分類目前沒有對話。"}
            </p>
          ) : (
            <ol className="work-list">
              {shown.map((item) => (
                <WorkRow key={item.id} summary={item} now={now} />
              ))}
            </ol>
          )}
        </div>
      </div>
    </main>
  );
}

function WorkRow({ summary, now }: { summary: SessionSummary; now: number }) {
  const [busy, setBusy] = useState(false);
  const { status, approval } = summary;
  const live = status === "running" || status === "waiting";

  const decide = async (decision: ApprovalDecision) => {
    if (!approval) return;
    setBusy(true);
    await api
      .decide(summary.id, approval.id, decision)
      .catch((error: Error) => showToast(error.message, "error"));
    setBusy(false);
  };

  let line = "";
  let code = "";
  if (status === "waiting" && approval) {
    line = approval.preview ? "想要寫入檔案" : "想要執行工具";
    code = approval.preview
      ? `${approval.tool} · ${approval.preview.path}`
      : approval.tool;
  } else if (status === "running") {
    line = `${activityLabel(summary.activity)}…`;
  } else if (status === "error") {
    line = summary.error ?? "出錯";
  } else if (status === "stopped") {
    line =
      summary.stopReason === INTERRUPTED_STOP_REASON
        ? "伺服器重新啟動，這一輪中斷了；打開對話可以重試"
        : "你停止了這一輪";
  } else {
    line =
      summary.toolErrors > 0
        ? `完成 · ${summary.toolErrors} 個工具錯誤已處理`
        : "完成";
  }

  const time = live
    ? formatDuration(now - (summary.startedAt ?? now))
    : summary.endedAt
      ? formatAgo(summary.endedAt, now)
      : "";

  return (
    <li className={`work-row anim-in ${status}`}>
      <span className="work-status">
        {status === "running" && <span className="ring" />}
        {status === "waiting" && (
          <span className="anim-pop accent-text">
            <ShieldIcon size={18} />
          </span>
        )}
        {(status === "done" || status === "stopped") && (
          <span className="anim-pop">
            <CheckIcon />
          </span>
        )}
        {status === "error" && (
          <span className="anim-pop danger-text">
            <CrossIcon />
          </span>
        )}
      </span>
      <div className="work-main">
        <Link to={`/s/${summary.id}`} className="work-title">
          {summary.title}
        </Link>
        <div className="work-line" key={`${line}${code}`}>
          <span className="anim-swap">{line}</span>
          {code && <span className="mono anim-swap">{code}</span>}
        </div>
      </div>
      <span className="work-time">{time}</span>
      <div className="work-actions">
        {status === "waiting" && approval && (
          <>
            <button
              type="button"
              className="btn btn-outline"
              disabled={busy}
              onClick={() => decide("deny")}
            >
              拒絕
            </button>
            <button
              type="button"
              className="btn btn-accent"
              disabled={busy}
              onClick={() => decide("once")}
            >
              允許
            </button>
          </>
        )}
        {status === "running" && (
          <button
            type="button"
            className="btn btn-outline"
            onClick={() =>
              void api
                .stop(summary.id)
                .catch((error: Error) => showToast(error.message, "error"))
            }
          >
            停止
          </button>
        )}
        {!live && (
          <Link to={`/s/${summary.id}`} className="btn btn-outline">
            開啟
          </Link>
        )}
      </div>
    </li>
  );
}
