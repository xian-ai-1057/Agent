/** 一段對話：內容、權限確認、輸入框，以及右側的檔案面板。 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  type Feedback,
  INTERRUPTED_STOP_REASON,
  type PermissionMode,
  type SessionDetail,
  type SessionOptions,
} from "../../../src/web/protocol.ts";
import { api } from "../api.ts";
import { isBusy, MODE_LABELS, STOP_NOTICES } from "../format.ts";
import {
  loadSession,
  setDetailMode,
  upsertSummary,
  useStore,
} from "../store.ts";
import {
  approvalPreview,
  buildTurns,
  collectAttachments,
  collectFiles,
  collectProduced,
  type FileChange,
  replyOf,
  type Step,
  type Turn,
} from "../transcript.ts";
import { ApprovalCard } from "./ApprovalCard.tsx";
import "./chat/chat.css";
import { Composer } from "./Composer.tsx";
import {
  agentTools,
  type ComposerOptions,
  effectiveModel,
  modelLabel,
  optionsEnabled,
} from "./chat/options.ts";
import { PlanChecklist } from "./chat/PlanChecklist.tsx";
import {
  forgetHandoff,
  type PendingMessage,
  peekHandoff,
} from "./chat/pending.ts";
import { latestPlan } from "./chat/plan.ts";
import { Thinking } from "./chat/Thinking.tsx";
import { FilePanel, type PanelTab } from "./FilePanel.tsx";
import {
  AlertIcon,
  ChevronDownIcon,
  PanelIcon,
  RetryIcon,
  ShieldIcon,
} from "./Icons.tsx";
import { Link } from "./Sidebar.tsx";
import { AttachmentChip, TurnView } from "./Transcript.tsx";

type Pending = PendingMessage & { sent: boolean };

/** App 以對話 id 當 key，換對話時整個重新建立，暫存的狀態不會帶過去。 */
export function Conversation({ id }: { id: string }) {
  const stored = useStore((s) => s.details[id]);
  const storedSummary = useStore((s) => s.sessions[id]);
  const previews = useStore((s) => s.previews);
  const project = useStore((s) => s.project);
  const [missing, setMissing] = useState<string>();
  const [panel, setPanel] = useState<{ open: boolean | null; path?: string }>({
    open: null,
  });
  const [tab, setTab] = useState<PanelTab>("diff");
  // 新對話頁交過來、或剛在這裡送出的訊息；伺服器的事件到了就換掉。
  const [pending, setPending] = useState<Pending | undefined>(() => {
    const handed = peekHandoff(id);
    return handed ? { ...handed, sent: true } : undefined;
  });
  const [sendError, setSendError] = useState<string>();
  const [optionsOverride, setOptionsOverride] = useState<SessionOptions>();
  const [optionsError, setOptionsError] = useState<string>();
  const [runModel, setRunModel] = useState<string>();
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const autoOpened = useRef<string>(undefined);

  useEffect(() => {
    forgetHandoff(id);
    loadSession(id).catch((error: Error) => setMissing(error.message));
  }, [id]);

  // 對話內容還在讀取、但已經有摘要時（剛建立的對話），先用空的內容顯示。
  const detail = useMemo<SessionDetail | undefined>(
    () =>
      stored ??
      (storedSummary
        ? {
            summary: storedSummary,
            messages: [],
            partial: "",
            approvals: [],
            mode: null,
          }
        : undefined),
    [stored, storedSummary],
  );

  const turns = useMemo(
    () => (detail ? buildTurns(detail, previews) : []),
    [detail, previews],
  );
  const files = useMemo(() => collectFiles(turns), [turns]);
  const attachments = useMemo(() => collectAttachments(turns), [turns]);
  const produced = useMemo(() => collectProduced(turns), [turns]);
  const summary = detail?.summary;
  const busy = isBusy(summary);
  const approval = detail?.approvals[0];

  // 伺服器記下這則訊息（或這一輪已經結束）之後，就不再顯示暫時的訊息。
  const pendingShown =
    pending &&
    !(
      stored &&
      (stored.messages.slice(pending.base).some((m) => m.role === "user") ||
        (pending.sent && !isBusy(stored.summary)))
    )
      ? pending
      : undefined;
  useEffect(() => {
    if (pending && !pendingShown) setPending(undefined);
  }, [pending, pendingShown]);

  // 這一輪的計畫（最後一則使用者訊息之後的 update_plan）。
  const plan = useMemo(() => {
    if (!detail || !busy) return undefined;
    const messages = detail.messages;
    const start = messages.findLastIndex((m) => m.role === "user");
    return latestPlan(messages.slice(Math.max(0, start)));
  }, [detail, busy]);

  useEffect(() => {
    document.title = summary ? `${summary.title} · Harness` : "Harness";
  }, [summary?.title, summary]);

  // 有寫檔的確認進來時，自動打開面板顯示差異。
  const approvalFile = useMemo(
    () => (approval ? approvalPreview(approval) : undefined),
    [approval],
  );
  useEffect(() => {
    if (!approval || !approvalFile || autoOpened.current === approval.id) {
      return;
    }
    autoOpened.current = approval.id;
    setPanel({ open: true, path: approvalFile.path });
    setTab("diff");
  }, [approval, approvalFile]);

  // 內容變多時，如果使用者本來就在最底下，就跟著捲到底。
  // biome-ignore lint/correctness/useExhaustiveDependencies: 內容改變時才需要捲動
  useEffect(() => {
    const element = scrollRef.current;
    if (element && stickToBottom.current)
      element.scrollTop = element.scrollHeight;
  }, [detail, pendingShown]);

  const agentInfo = project?.agents.find((a) => a.name === summary?.agent);
  const models = project?.models ?? [];
  const optionsValue: SessionOptions = optionsOverride ??
    detail?.options ?? {
      model: summary?.model ?? null,
      tools: null,
      skills: [],
    };
  const currentModel = effectiveModel(optionsValue, agentInfo?.model, models);

  // 每一輪開始時記下用的模型，顯示在回覆底下。
  // biome-ignore lint/correctness/useExhaustiveDependencies: 只在開始跑的時候記
  useEffect(() => {
    if (busy) setRunModel(currentModel);
  }, [busy]);

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
  const openPath = (path: string) => {
    setPanel({ open: true, path });
    setTab("preview");
  };

  const send = async (text: string, files: string[]) => {
    setSendError(undefined);
    stickToBottom.current = true;
    setPending({
      text,
      attachments: files,
      base: stored?.messages.length ?? 0,
      sent: false,
    });
    try {
      upsertSummary(await api.send(id, text, files));
      setPending((current) => current && { ...current, sent: true });
      return true;
    } catch (error) {
      setPending(undefined);
      setSendError(`訊息沒有送出：${(error as Error).message}`);
      return false;
    }
  };

  const changeMode = async (value: string) => {
    const mode = (value || null) as PermissionMode | null;
    setDetailMode(id, mode);
    await api.setMode(id, mode).catch(() => undefined);
  };

  const changeOptions = (change: Partial<SessionOptions>) => {
    const previous = optionsValue;
    setOptionsOverride({ ...optionsValue, ...change });
    setOptionsError(undefined);
    api.setOptions(id, change).then(
      (saved) => {
        if (saved && Array.isArray(saved.skills)) setOptionsOverride(saved);
      },
      (error: Error) => {
        setOptionsOverride(previous);
        setOptionsError(`選項沒有存到：${error.message}`);
      },
    );
  };

  const composerOptions: ComposerOptions | undefined =
    project && optionsEnabled(project)
      ? {
          models,
          skills: project.skills ?? [],
          tools: agentInfo ? agentTools(project, agentInfo) : [],
          value: optionsValue,
          fallbackModel: agentInfo?.model,
          onChange: changeOptions,
          nextTurn: true,
        }
      : undefined;

  const rate = project?.features?.feedback
    ? (index: number, feedback: Feedback | null) =>
        api.setFeedback(id, index, feedback).then(() => undefined)
    : undefined;
  // 回覆底下顯示產生它的模型（伺服器記下的 replyModels）。這次開著畫面
  // 時剛跑完、紀錄還沒讀回來的最後一輪，用開始跑時記下的模型。舊的回覆
  // 沒有紀錄就不顯示，不拿現在選的模型充數。
  const replyModel = (index: number | undefined, latest: boolean) => {
    const recorded =
      index === undefined ? undefined : detail.replyModels?.[String(index)];
    return modelLabel(recorded ?? (latest ? runModel : undefined), models);
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
    Boolean(pendingShown) ||
    (summary.status === "running" &&
      (summary.activity === undefined ||
        summary.activity.kind === "thinking") &&
      !detail.partial);
  const canLoadFiles = Boolean(project?.features?.files);
  const interrupted =
    summary.status === "stopped" &&
    summary.stopReason === INTERRUPTED_STOP_REASON;
  const approvalAgent =
    (approval && findApprovalStep(turns, approval.id)?.agent) ?? summary.agent;

  return (
    <>
      <main className="main">
        <header className="main-header">
          <div className="main-title">
            <h1>{summary.title}</h1>
            <span className="subtle mono ch-hide-narrow">
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
          <div className="thread ch-thread">
            {turns.map((turn, index) => {
              const last = index === turns.length - 1;
              const finished = !last || !busy || Boolean(pendingShown);
              const reply = finished ? replyOf(turn) : undefined;
              return (
                <TurnView
                  key={turn.key}
                  turn={turn}
                  onOpenFile={openFile}
                  onOpenPath={canLoadFiles ? openPath : undefined}
                  canLoadFiles={canLoadFiles}
                  reply={
                    reply?.text
                      ? {
                          text: reply.text,
                          index: reply.index,
                          feedback:
                            reply.index === undefined
                              ? undefined
                              : detail.feedback?.[String(reply.index)],
                          onFeedback: rate,
                          model: replyModel(
                            reply.index,
                            last && !pendingShown && !busy,
                          ),
                        }
                      : undefined
                  }
                />
              );
            })}
            {pendingShown && (
              <PendingView
                pending={pendingShown}
                onOpenPath={canLoadFiles ? openPath : undefined}
              />
            )}
            {showThinking && <Thinking />}
            {summary.status === "error" && !pendingShown && (
              <ErrorCard id={id} message={summary.error ?? "未知的錯誤"} />
            )}
            {interrupted && !pendingShown && (
              <ErrorCard
                id={id}
                tone="interrupted"
                message={
                  summary.notice ?? "伺服器重新啟動，這一輪沒有做完就停下來了。"
                }
              />
            )}
            {summary.status === "stopped" && !interrupted && !pendingShown && (
              <div className="notice anim-in">你停止了這一輪。</div>
            )}
            {blockedNotice && !pendingShown && (
              <div className="notice anim-in">
                <ShieldIcon />
                這則訊息被防護規則擋下，沒有送給模型：{blockedNotice}
              </div>
            )}
            {summary.status === "done" &&
              !pendingShown &&
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

        <div className="composer-wrap ch-composer-wrap">
          <div className="composer-inner">
            {plan && <PlanChecklist items={plan} />}
            {approval && (
              <ApprovalCard
                key={approval.id}
                approval={approval}
                agent={approvalAgent}
                sessionApproval={Boolean(project?.features?.sessionApproval)}
                onShowFile={() => {
                  if (approvalFile) {
                    setPanel({ open: true, path: approvalFile.path });
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
              onUpload={project?.uploads ? api.upload : undefined}
              maxUploadBytes={project?.uploads?.maxBytes}
              options={composerOptions}
              controls={
                <>
                  {!composerOptions && (
                    <span className="select-chip">agent：{summary.agent}</span>
                  )}
                  <label className="select-chip">
                    <ShieldIcon />
                    <span className="ch-hide-narrow">{modeLabel}</span>
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
            {(sendError || optionsError) && (
              <p className="inline-error" role="alert">
                {sendError ?? optionsError}
              </p>
            )}
          </div>
        </div>
      </main>
      {panelOpen && (
        <FilePanel
          files={files}
          attachments={attachments}
          produced={produced}
          selected={panel.path}
          tab={tab}
          canLoad={canLoadFiles}
          onSelect={(path) => setPanel({ open: true, path })}
          onTab={setTab}
          onClose={() => setPanel((p) => ({ ...p, open: false }))}
        />
      )}
    </>
  );
}

/** 剛送出、還沒出現在紀錄裡的訊息。 */
export function PendingView({
  pending,
  onOpenPath,
}: {
  pending: { text: string; attachments: string[] };
  onOpenPath?: (path: string) => void;
}) {
  return (
    <>
      {pending.text && (
        <div className="ch-user anim-in">
          <div className="msg-user">{pending.text}</div>
        </div>
      )}
      {pending.attachments.length > 0 && (
        <ul className="msg-attachments anim-in" aria-label="附加的檔案">
          {pending.attachments.map((path) => (
            <li key={path}>
              <AttachmentChip path={path} onOpen={onOpenPath} />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

/** 等待確認的工具呼叫是哪一個步驟（subagent 的步驟才知道是誰要求的）。 */
function findApprovalStep(turns: Turn[], approvalId: string): Step | undefined {
  const search = (steps: Step[] | undefined): Step | undefined => {
    for (const step of steps ?? []) {
      if (step.approval?.id === approvalId) return step;
      const child = search(step.children);
      if (child) return child;
    }
    return undefined;
  };
  for (const turn of turns) {
    for (const block of turn.blocks) {
      if (block.kind !== "steps") continue;
      const found = search(block.steps);
      if (found) return found;
    }
  }
  return undefined;
}

/**
 * 這一輪沒做完、可以重試的說明卡片。出錯（error）用紅色；伺服器重新
 * 啟動而中斷（interrupted）不是誰做錯了，用警告色。
 */
function ErrorCard({
  id,
  message,
  tone = "error",
}: {
  id: string;
  message: string;
  tone?: "error" | "interrupted";
}) {
  const [retrying, setRetrying] = useState(false);
  const [error, setError] = useState<string>();
  const interrupted = tone === "interrupted";
  const modelProblem =
    !interrupted && /模型|端點|HARNESS_|API|Connection/i.test(message);
  return (
    <section
      className={`error-card anim-rise${interrupted ? " ch-interrupted" : ""}`}
      role={interrupted ? "status" : "alert"}
    >
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
        <span className="ch-error-icon">
          <AlertIcon />
        </span>
        <div style={{ minWidth: 0 }}>
          <div className="title">
            {interrupted ? "這一輪被中斷了" : "這一輪出錯停下來了"}
          </div>
          <p className="body">{message}</p>
          <p className="body subtle">
            {interrupted
              ? "你的訊息已經保留，按重試就會接著做。"
              : "你的訊息已經保留，問題排除後按重試就會接著跑。"}
          </p>
          {error && <p className="inline-error">重試失敗：{error}</p>}
        </div>
      </div>
      <div className="actions">
        <button
          type="button"
          className="btn btn-dark"
          disabled={retrying}
          onClick={async () => {
            setRetrying(true);
            setError(undefined);
            try {
              upsertSummary(await api.retry(id));
            } catch (failure) {
              setError((failure as Error).message);
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
