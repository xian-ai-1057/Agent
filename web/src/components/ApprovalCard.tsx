/**
 * 工具呼叫的權限確認：允許一次、（這段對話都允許、）這個專案以後都
 * 允許、拒絕，依序也可以按數字鍵。伺服器支援 sessionApproval 時才有
 * 「這段對話都允許」，按 1–4；否則按 1–3。
 */

import { type ReactNode, useEffect, useRef, useState } from "react";
import type { Approval, ApprovalDecision } from "../../../src/web/protocol.ts";
import { api } from "../api.ts";
import { approvalPreview } from "../transcript.ts";
import "./chat/chat.css";
import { describeStep, sentenceText, toolLabel } from "./chat/steps.ts";
import { lineStats } from "./FilePanel.tsx";

/** 正在打字、或對話框與選單開著時，數字鍵不當成快速鍵。 */
const TYPING =
  "input, textarea, select, [contenteditable]:not([contenteditable='false'])";
const LAYERS = "dialog, [role='dialog'], [role='menu']";
/** 開著的對話框（<dialog>）、彈出框與選單；關著時不在畫面上。 */
const OPEN_LAYERS = "dialog[open], [role='dialog'], [role='menu']";

function hotkeysAllowed(target: EventTarget | null): boolean {
  if (target instanceof Element && target.closest(`${TYPING}, ${LAYERS}`)) {
    return false;
  }
  // 焦點不在裡面，但還有對話框或選單開著（例如剛點開還沒移入焦點）。
  return !document.querySelector(OPEN_LAYERS);
}

export function ApprovalCard({
  approval,
  agent,
  sessionApproval = false,
  onShowFile,
}: {
  approval: Approval;
  /** 提出要求的 agent 名稱。 */
  agent: string;
  /** 伺服器支援「這段對話都允許」（features.sessionApproval）。 */
  sessionApproval?: boolean;
  onShowFile: () => void;
}) {
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();
  // 決定後先收起來，等伺服器的事件到了才真的移除。
  const [leaving, setLeaving] = useState(false);
  const card = useRef<HTMLElement>(null);

  const decide = async (decision: ApprovalDecision) => {
    if (sending) return;
    setSending(true);
    setError(undefined);
    try {
      await api.decide(approval.sessionId, approval.id, decision);
      setLeaving(true);
    } catch (failure) {
      setError((failure as Error).message);
      setSending(false);
    }
  };

  const keys: Record<string, ApprovalDecision> = sessionApproval
    ? { "1": "once", "2": "session", "3": "always", "4": "deny" }
    : { "1": "once", "2": "always", "3": "deny" };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || !hotkeysAllowed(event.target)) return;
      // 手機抽屜打開時主畫面是 inert，卡片看不到也不能按。
      if (card.current?.closest("[inert]")) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const choice = keys[event.key];
      if (!choice) return;
      event.preventDefault();
      void decide(choice);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const preview = approvalPreview(approval);
  const command =
    approval.tool === "run_command" &&
    typeof approval.arguments.command === "string"
      ? approval.arguments.command
      : undefined;
  const cwd =
    typeof approval.arguments.cwd === "string" &&
    approval.arguments.cwd !== "." &&
    approval.arguments.cwd !== ""
      ? approval.arguments.cwd
      : undefined;
  const sentence = describeStep({
    name: approval.tool,
    args: approval.arguments,
  });
  // 不認識的工具只說要執行哪個工具，參數在下面。
  const action =
    sentence.verb === approval.tool
      ? `執行 ${approval.tool}`
      : sentenceText(sentence);
  const stats = preview ? lineStats(preview.before, preview.after) : undefined;

  return (
    <div className={`ch-approval-wrap${leaving ? " leaving" : ""}`}>
      <div>
        <section
          ref={card}
          className="approval-card ch-approval"
          aria-label="權限確認"
        >
          <div className="approval-head">
            <div style={{ minWidth: 0 }}>
              <div className="title">
                {preview
                  ? `${agent} 想要${approval.tool === "edit_file" ? "修改" : "寫入"}檔案`
                  : command !== undefined
                    ? `${agent} 想要執行指令`
                    : `${agent} 想要${action}`}
              </div>
              <div className="meta">
                {approval.tool}
                {preview && (
                  <>
                    {" · "}
                    {preview.path}
                    {" · "}
                    {preview.before === null ? "新檔案" : "修改"}{" "}
                    <span className="plus">+{stats?.added}</span>{" "}
                    <span className="minus">−{stats?.removed}</span>
                  </>
                )}
              </div>
            </div>
            {preview && (
              <button
                type="button"
                className="btn btn-outline"
                onClick={onShowFile}
              >
                查看內容
              </button>
            )}
          </div>
          {!preview && command !== undefined && (
            <div className="ch-term ch-approval-term">
              {cwd && <div className="ch-term-cwd">{cwd}</div>}
              <pre className="ch-term-cmd">
                <span aria-hidden="true">$ </span>
                {command}
              </pre>
            </div>
          )}
          {!preview && command === undefined && (
            <pre className="approval-args">
              {JSON.stringify(approval.arguments, null, 2)}
            </pre>
          )}
          <div className="approval-options ch-approval-options">
            <Option
              n="1"
              kind="primary"
              disabled={sending}
              onClick={() => decide("once")}
            >
              允許這一次
            </Option>
            {sessionApproval && (
              <Option
                n="2"
                disabled={sending}
                onClick={() => decide("session")}
              >
                這段對話都允許
                <ToolName tool={approval.tool} />
              </Option>
            )}
            <Option
              n={sessionApproval ? "3" : "2"}
              disabled={sending}
              onClick={() => decide("always")}
            >
              這個專案以後都允許
              <ToolName tool={approval.tool} />
            </Option>
            <Option
              n={sessionApproval ? "4" : "3"}
              kind="outline"
              disabled={sending}
              onClick={() => decide("deny")}
            >
              拒絕
            </Option>
          </div>
          {error && (
            <div className="inline-error" role="alert">
              {error}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

/** 選項裡的工具：有白話名稱時顯示名稱（滑過看得到工具名稱），否則顯示工具名稱。 */
function ToolName({ tool }: { tool: string }) {
  const label = toolLabel(tool);
  if (!label) {
    return (
      <>
        {" "}
        <code>{tool}</code>
      </>
    );
  }
  return <span title={tool}>{label}</span>;
}

function Option({
  n,
  kind = "plain",
  disabled,
  onClick,
  children,
}: {
  n: string;
  kind?: "primary" | "plain" | "outline";
  disabled: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className={`approval-option ch-option ${kind}`}
      disabled={disabled}
      onClick={onClick}
    >
      <span className="kbd" aria-hidden="true">
        {n}
      </span>
      <span>{children}</span>
    </button>
  );
}
