/** 一段對話：內容、權限確認、輸入框，以及右側的檔案面板。 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { PermissionMode } from "../../../src/web/protocol.ts";
import { api } from "../api.ts";
import { isBusy, MODE_LABELS, STOP_NOTICES } from "../format.ts";
import {
  loadSession,
  setDetailMode,
  upsertSummary,
  useStore,
} from "../store.ts";
import { buildTurns, collectFiles, type FileChange } from "../transcript.ts";
import { ApprovalCard } from "./ApprovalCard.tsx";
import { Composer } from "./Composer.tsx";
import { FilePanel, type PanelTab } from "./FilePanel.tsx";
import {
  AlertIcon,
  ChevronDownIcon,
  PanelIcon,
  RetryIcon,
  ShieldIcon,
} from "./Icons.tsx";
import { Link } from "./Sidebar.tsx";
import { TurnView } from "./Transcript.tsx";

export function Conversation({ id }: { id: string }) {
  const detail = useStore((s) => s.details[id]);
  const previews = useStore((s) => s.previews);
  const project = useStore((s) => s.project);
  const [missing, setMissing] = useState<string>();
  const [panel, setPanel] = useState<{ open: boolean | null; path?: string }>({
    open: null,
  });
  const [tab, setTab] = useState<PanelTab>("diff");
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const autoOpened = useRef<string>(undefined);

  useEffect(() => {
    setMissing(undefined);
    setPanel({ open: null });
    stickToBottom.current = true;
    loadSession(id).catch((error: Error) => setMissing(error.message));
  }, [id]);

  const turns = useMemo(
    () => (detail ? buildTurns(detail, previews) : []),
    [detail, previews],
  );
  const files = useMemo(() => collectFiles(turns), [turns]);
  const summary = detail?.summary;
  const busy = isBusy(summary);
  const approval = detail?.approvals[0];

  useEffect(() => {
    document.title = summary ? `${summary.title} · Harness` : "Harness";
  }, [summary?.title, summary]);

  // 有寫檔的確認進來時，自動打開面板顯示差異。
  useEffect(() => {
    if (!approval?.preview || autoOpened.current === approval.id) return;
    autoOpened.current = approval.id;
    setPanel({ open: true, path: approval.preview.path });
    setTab("diff");
  }, [approval]);

  // 內容變多時，如果使用者本來就在最底下，就跟著捲到底。
  // biome-ignore lint/correctness/useExhaustiveDependencies: 內容改變時才需要捲動
  useEffect(() => {
    const element = scrollRef.current;
    if (element && stickToBottom.current)
      element.scrollTop = element.scrollHeight;
  }, [detail]);

  if (missing) {
    return (
      <main className="main">
        <div className="empty-thread">
          <p>{missing}</p>
          <Link to="/" className="btn btn-outline">
            開新對話
          </Link>
        </div>
      </main>
    );
  }
  if (!detail || !summary) return <main className="main" aria-busy="true" />;

  const panelOpen = panel.open ?? false;
  const openFile = (change: FileChange, nextTab: PanelTab = "preview") => {
    setPanel({ open: true, path: change.path });
    setTab(nextTab);
  };

  const send = async (text: string) => {
    try {
      stickToBottom.current = true;
      upsertSummary(await api.send(id, text));
      return true;
    } catch (error) {
      alert((error as Error).message);
      return false;
    }
  };

  const changeMode = async (value: string) => {
    const mode = (value || null) as PermissionMode | null;
    setDetailMode(id, mode);
    await api.setMode(id, mode).catch(() => undefined);
  };

  const projectMode = project?.permissions.mode ?? "ask";
  const modeLabel = detail.mode
    ? MODE_LABELS[detail.mode]
    : MODE_LABELS[projectMode];
  // 回覆被擋下時，換上的說明已經是 agent 的回覆；只有使用者的訊息被
  // 擋下（沒有存進紀錄）時才另外顯示。
  const lastReply = detail.messages.findLast((m) => m.role === "assistant");
  const blockedNotice =
    summary.stopReason === "blocked" &&
    summary.notice &&
    summary.notice !== lastReply?.content
      ? summary.notice
      : undefined;
  const showThinking =
    summary.status === "running" &&
    summary.activity?.kind === "thinking" &&
    !detail.partial;

  return (
    <>
      <main className="main">
        <header className="main-header">
          <div className="main-title">
            <h1>{summary.title}</h1>
            <span className="subtle mono">
              {project?.name} · {summary.agent}
            </span>
          </div>
          <button
            type="button"
            className="icon-btn"
            aria-label="切換檔案面板"
            aria-pressed={panelOpen}
            onClick={() => setPanel((p) => ({ ...p, open: !panelOpen }))}
          >
            <PanelIcon />
          </button>
        </header>

        <div
          className="scroll"
          ref={scrollRef}
          onScroll={(event) => {
            const element = event.currentTarget;
            stickToBottom.current =
              element.scrollHeight - element.scrollTop - element.clientHeight <
              80;
          }}
        >
          <div className="thread">
            {turns.map((turn) => (
              <TurnView key={turn.key} turn={turn} onOpenFile={openFile} />
            ))}
            {showThinking && (
              <div className="thinking anim-in">
                <span className="dot pulse" />
                思考中…
              </div>
            )}
            {summary.status === "error" && (
              <ErrorCard id={id} message={summary.error ?? "未知的錯誤"} />
            )}
            {summary.status === "stopped" && (
              <div className="notice anim-in">你停止了這一輪。</div>
            )}
            {blockedNotice && (
              <div className="notice anim-in">
                <ShieldIcon />
                這則訊息被防護規則擋下，沒有送給模型：{blockedNotice}
              </div>
            )}
            {summary.status === "done" &&
              summary.stopReason &&
              summary.stopReason !== "end_turn" &&
              summary.stopReason !== "blocked" && (
                <div className="notice anim-in">
                  {STOP_NOTICES[summary.stopReason] ??
                    `模型停止：${summary.stopReason}`}
                </div>
              )}
          </div>
        </div>

        <div className="composer-wrap">
          <div className="composer-inner">
            {approval && (
              <ApprovalCard
                key={approval.id}
                approval={approval}
                agent={project?.name ?? summary.agent}
                onShowFile={() => {
                  if (approval.preview) {
                    setPanel({ open: true, path: approval.preview.path });
                    setTab("diff");
                  }
                }}
              />
            )}
            <Composer
              placeholder="接著問，或請它修改"
              busy={busy}
              onSubmit={send}
              onStop={() => void api.stop(id)}
              controls={
                <>
                  <span className="select-chip">agent：{summary.agent}</span>
                  <label className="select-chip">
                    <ShieldIcon />
                    {modeLabel}
                    <ChevronDownIcon />
                    <select
                      aria-label="這段對話的權限模式"
                      value={detail.mode ?? ""}
                      onChange={(event) => void changeMode(event.target.value)}
                    >
                      <option value="">
                        跟著專案設定（{MODE_LABELS[projectMode]}）
                      </option>
                      <option value="ask">{MODE_LABELS.ask}</option>
                      <option value="bypass">{MODE_LABELS.bypass}</option>
                      <option value="dontAsk">{MODE_LABELS.dontAsk}</option>
                    </select>
                  </label>
                </>
              }
            />
          </div>
        </div>
      </main>
      {panelOpen && (
        <FilePanel
          files={files}
          selected={panel.path}
          tab={tab}
          onSelect={(path) => setPanel({ open: true, path })}
          onTab={setTab}
          onClose={() => setPanel((p) => ({ ...p, open: false }))}
        />
      )}
    </>
  );
}

function ErrorCard({ id, message }: { id: string; message: string }) {
  const [retrying, setRetrying] = useState(false);
  const modelProblem = /模型|端點|HARNESS_|API|Connection/i.test(message);
  return (
    <section className="error-card anim-rise" role="alert">
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
        <span style={{ color: "var(--danger)", display: "flex" }}>
          <AlertIcon />
        </span>
        <div style={{ minWidth: 0 }}>
          <div className="title">這一輪出錯停下來了</div>
          <p className="body">{message}</p>
          <p className="body subtle">
            你的訊息已經保留，問題排除後按重試就會接著跑。
          </p>
        </div>
      </div>
      <div className="actions">
        <button
          type="button"
          className="btn btn-dark"
          disabled={retrying}
          onClick={async () => {
            setRetrying(true);
            try {
              upsertSummary(await api.retry(id));
            } catch (error) {
              alert((error as Error).message);
            }
            setRetrying(false);
          }}
        >
          <RetryIcon />
          重試
        </button>
        {modelProblem && (
          <Link to="/settings/model" className="btn btn-outline">
            開啟模型設定
          </Link>
        )}
      </div>
    </section>
  );
}
