/** 工具呼叫的權限確認：允許一次、以後都允許、拒絕。按 1、2、3 也可以。 */

import { type ReactNode, useEffect, useState } from "react";
import type { Approval, ApprovalDecision } from "../../../src/web/protocol.ts";
import { api } from "../api.ts";
import { lineStats } from "./FilePanel.tsx";

export function ApprovalCard({
  approval,
  agent,
  onShowFile,
}: {
  approval: Approval;
  agent: string;
  onShowFile: () => void;
}) {
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();

  const decide = async (decision: ApprovalDecision) => {
    if (sending) return;
    setSending(true);
    setError(undefined);
    try {
      await api.decide(approval.sessionId, approval.id, decision);
    } catch (failure) {
      setError((failure as Error).message);
      setSending(false);
    }
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [contenteditable]")) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const choice = { "1": "once", "2": "always", "3": "deny" }[event.key];
      if (!choice) return;
      event.preventDefault();
      void decide(choice as ApprovalDecision);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const { preview } = approval;
  const stats = preview ? lineStats(preview.before, preview.after) : undefined;

  return (
    <section className="approval-card anim-rise" aria-label="權限確認">
      <div className="approval-head">
        <div style={{ minWidth: 0 }}>
          <div className="title">
            {preview
              ? `${agent} 想要寫入檔案`
              : `${agent} 想要執行 ${approval.tool}`}
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
      {!preview && (
        <pre className="approval-args">
          {JSON.stringify(approval.arguments, null, 2)}
        </pre>
      )}
      <div className="approval-options">
        <Option n="1" disabled={sending} onClick={() => decide("once")}>
          允許這一次
        </Option>
        <Option n="2" disabled={sending} onClick={() => decide("always")}>
          這個專案以後都允許 <code>{approval.tool}</code>
        </Option>
        <Option n="3" disabled={sending} onClick={() => decide("deny")}>
          拒絕
        </Option>
      </div>
      {error && <div className="inline-error">{error}</div>}
    </section>
  );
}

function Option({
  n,
  disabled,
  onClick,
  children,
}: {
  n: string;
  disabled: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className="approval-option"
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
