/** 對話內容：使用者訊息、步驟群組、agent 的文字、檔案卡片。 */

import { useState } from "react";
import {
  type Block,
  type FileChange,
  firstLine,
  formatArgs,
  type Step,
  summarizeSteps,
  type Turn,
} from "../transcript.ts";
import {
  CheckIcon,
  ChevronIcon,
  CrossIcon,
  FileIcon,
  ShieldIcon,
  WarnIcon,
} from "./Icons.tsx";
import { Markdown } from "./Markdown.tsx";

export function TurnView({
  turn,
  onOpenFile,
}: {
  turn: Turn;
  onOpenFile: (change: FileChange) => void;
}) {
  return (
    <>
      {turn.user !== undefined && (
        <div className="msg-user anim-in">{turn.user}</div>
      )}
      {turn.blocks.map((block) => (
        <BlockView key={block.key} block={block} />
      ))}
      {turn.files
        .filter((change) => change.status === "written")
        .map((change) => (
          <FileCard
            key={change.callId}
            change={change}
            onOpen={() => onOpenFile(change)}
          />
        ))}
    </>
  );
}

function BlockView({ block }: { block: Block }) {
  if (block.kind === "text") {
    return (
      <div className="anim-in">
        <Markdown text={block.text} streaming={block.streaming} />
      </div>
    );
  }
  return <StepGroup steps={block.steps} />;
}

/** 連續的工具呼叫。執行中與有錯誤時預設展開，做完就收起來。 */
function StepGroup({ steps }: { steps: Step[] }) {
  const { active, errors } = summarizeSteps(steps);
  const [choice, setChoice] = useState<boolean | null>(null);
  const open = choice ?? (Boolean(active) || errors > 0);

  let label = `已執行 ${steps.length} 個步驟`;
  if (errors > 0) label += ` · ${errors} 個工具錯誤`;
  if (active?.status === "waiting") label = `等你確認 ${active.name}`;
  else if (active) label = `執行 ${active.name}…`;

  return (
    <div className="anim-in">
      <button
        type="button"
        className="steps-toggle"
        aria-expanded={open}
        onClick={() => setChoice(!open)}
      >
        {active && <span className="dot pulse" />}
        {!active && errors > 0 && (
          <span style={{ color: "var(--warn)", display: "flex" }}>
            <WarnIcon />
          </span>
        )}
        <span className="label">{label}</span>
        <ChevronIcon className="chevron" />
      </button>
      <div className={`collapse ${open ? "open" : ""}`}>
        <div>
          <ol className="step-list">
            {steps.map((step) => (
              <StepRow key={step.id} step={step} />
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}

function StepRow({ step }: { step: Step }) {
  const [expanded, setExpanded] = useState(false);
  const args = formatArgs(step);
  return (
    <li className="anim-in">
      <button
        type="button"
        className="step-row"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
      >
        <span className="step-icon">
          <StepIcon status={step.status} />
        </span>
        <span className="step-name">{step.name}</span>
        <span className="step-args">{args}</span>
        {step.status === "done" && step.result && (
          <span className="step-result anim-in">{firstLine(step.result)}</span>
        )}
        {step.status === "error" && (
          <span className="step-flag warn anim-in">工具錯誤</span>
        )}
        {step.status === "waiting" && (
          <span className="step-flag accent">等你確認</span>
        )}
        {step.status === "interrupted" && (
          <span className="step-flag warn">已中斷</span>
        )}
      </button>
      {step.status === "error" && !expanded && (
        <div className="step-error anim-shake">{step.result}</div>
      )}
      {expanded && (
        <div className="step-detail anim-in">
          <span className="label">參數</span>
          <pre>{prettyArgs(step)}</pre>
          {step.result !== undefined && (
            <>
              <span className="label">
                {step.status === "error" ? "錯誤" : "結果"}
              </span>
              <pre>{step.result}</pre>
            </>
          )}
        </div>
      )}
    </li>
  );
}

function StepIcon({ status }: { status: Step["status"] }) {
  if (status === "done") {
    return (
      <span className="anim-pop" style={{ color: "var(--accent)" }}>
        <CheckIcon />
      </span>
    );
  }
  if (status === "error" || status === "interrupted") {
    return (
      <span className="anim-pop" style={{ color: "var(--warn)" }}>
        <CrossIcon />
      </span>
    );
  }
  if (status === "waiting") {
    return (
      <span style={{ color: "var(--accent)" }}>
        <ShieldIcon />
      </span>
    );
  }
  return <span className="dot pulse" />;
}

function FileCard({
  change,
  onOpen,
}: {
  change: FileChange;
  onOpen: () => void;
}) {
  return (
    <button type="button" className="file-card anim-in" onClick={onOpen}>
      <span className="icon">
        <FileIcon />
      </span>
      <span>
        <span className="name">{change.path.split("/").at(-1)}</span>
        <span className="meta">{change.path} · 已寫入</span>
      </span>
    </button>
  );
}

function prettyArgs(step: Step): string {
  if (Object.keys(step.args).length === 0) return step.rawArgs;
  return JSON.stringify(step.args, null, 2);
}
