/** 對話內容：使用者訊息、步驟群組、agent 的文字、檔案卡片與產生的圖片。 */

import { useState } from "react";
import { fileUrl } from "../api.ts";
import {
  type Block,
  type FileChange,
  resultPreview,
  type Step,
  summarizeSteps,
  type Turn,
} from "../transcript.ts";
import "./chat/chat.css";
import { FileBadge } from "./chat/FileBadge.tsx";
import { fileName, isThumbnail } from "./chat/filetype.ts";
import { DownloadIcon } from "./chat/icons.tsx";
import {
  MessageActions,
  type ReplyActions,
  UserCopy,
} from "./chat/MessageActions.tsx";
import { PlanList } from "./chat/PlanChecklist.tsx";
import { planFromArguments, planProgress } from "./chat/plan.ts";
import { describeStep, type StepSentence, sentenceText } from "./chat/steps.ts";
import {
  CheckIcon,
  ChevronIcon,
  CrossIcon,
  FileIcon,
  RetryIcon,
  ShieldIcon,
  WarnIcon,
} from "./Icons.tsx";
import { Markdown } from "./Markdown.tsx";

export function TurnView({
  turn,
  onOpenFile,
  onOpenPath,
  canLoadFiles = false,
  reply,
}: {
  turn: Turn;
  onOpenFile: (change: FileChange) => void;
  /** 點了使用者附上（或程式產生）的檔案；沒有提供時不能點。 */
  onOpenPath?: (path: string) => void;
  /**
   * 伺服器可以讀取工作資料夾的檔案（features.files）：顯示產生的圖片
   * 縮圖、下載按鈕，回覆裡相對路徑的圖片也從伺服器讀取。
   */
  canLoadFiles?: boolean;
  /** 這一輪已經結束時，回覆底下的動作。 */
  reply?: ReplyActions;
}) {
  const written = turn.files.filter((change) => change.status === "written");
  // 寫入的檔案已經有卡片，不再重複顯示。
  const produced = turn.produced.filter(
    (path) => !written.some((change) => change.path === path),
  );
  return (
    <>
      {/* 附件放在訊息上方，和 Claude、ChatGPT 一樣先看到附了什麼。 */}
      {turn.attachments.length > 0 && (
        <ul className="msg-attachments anim-in" aria-label="附加的檔案">
          {turn.attachments.map((path) => (
            <li key={path}>
              <AttachmentChip path={path} onOpen={onOpenPath} />
            </li>
          ))}
        </ul>
      )}
      {turn.user && (
        <div className="ch-user anim-in">
          <div className="msg-user">{turn.user}</div>
          <UserCopy text={turn.user} />
        </div>
      )}
      {turn.blocks.map((block) => (
        <BlockView key={block.key} block={block} localFiles={canLoadFiles} />
      ))}
      {written.map((change) => (
        <FileCard
          key={change.callId}
          change={change}
          onOpen={() => onOpenFile(change)}
        />
      ))}
      {produced.length > 0 && (
        <ProducedFiles
          paths={produced}
          canLoad={canLoadFiles}
          onOpen={onOpenPath}
        />
      )}
      {reply?.text && <MessageActions {...reply} />}
    </>
  );
}

/** 使用者訊息上的附件；可以預覽時是按鈕。 */
export function AttachmentChip({
  path,
  onOpen,
}: {
  path: string;
  onOpen?: (path: string) => void;
}) {
  const name = path.split("/").at(-1);
  if (!onOpen) {
    return (
      <span className="attachment done" title={path}>
        <FileIcon size={14} />
        <span className="attachment-name">{name}</span>
      </span>
    );
  }
  return (
    <button
      type="button"
      className="attachment done ch-attach-btn"
      title={`預覽 ${path}`}
      onClick={() => onOpen(path)}
    >
      <FileIcon size={14} />
      <span className="attachment-name">{name}</span>
    </button>
  );
}

function BlockView({
  block,
  localFiles,
}: {
  block: Block;
  localFiles: boolean;
}) {
  if (block.kind === "text") {
    return (
      <div className="anim-in">
        <Markdown
          text={block.text}
          streaming={block.streaming}
          localFiles={localFiles}
        />
      </div>
    );
  }
  if (block.kind === "notice") {
    return (
      <div className="notice anim-in">
        <RetryIcon />
        {block.text}
      </div>
    );
  }
  return <StepGroup steps={block.steps} />;
}

/**
 * 連續的工具呼叫。執行中與有還沒修正的錯誤時預設展開，做完就收起來；
 * 之後已經自己修正的錯誤只在標題用淡色註記。
 */
function StepGroup({ steps }: { steps: Step[] }) {
  const { active, errors, fixed } = summarizeSteps(steps);
  const [choice, setChoice] = useState<boolean | null>(null);
  const open = choice ?? (Boolean(active) || errors > 0);

  let label = `已執行 ${steps.length} 個步驟`;
  if (errors > 0) label += ` · ${errors} 個工具錯誤`;
  const fixedNote =
    !active && fixed > 0 ? `${fixed} 個步驟出錯，已繼續處理` : undefined;
  const who = active?.agent ? `${active.agent}：` : "";
  if (active?.status === "waiting") {
    label = `${who}等你確認：${sentenceText(describeStep(active))}`;
  } else if (active) label = `${who}${sentenceText(describeStep(active))}…`;

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
        {fixedNote && <span className="ch-fixed-note">· {fixedNote}</span>}
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

function Sentence({ sentence }: { sentence: StepSentence }) {
  const { verb, target, style } = sentence;
  return (
    <span className="ch-step-text">
      <span className="ch-step-verb">{verb}</span>
      {target && style === "code" && (
        <code className="ch-step-code">{target}</code>
      )}
      {target && style === "quote" && (
        <span className="ch-step-target">「{target}」</span>
      )}
      {target && style === "plain" && (
        <span className="ch-step-target">{target}</span>
      )}
    </span>
  );
}

function StepRow({ step }: { step: Step }) {
  const [expanded, setExpanded] = useState(false);
  const sentence = describeStep(step);
  const plan =
    step.name === "update_plan" ? planFromArguments(step.args) : undefined;
  const progress = plan ? planProgress(plan) : undefined;
  return (
    <li className="anim-in">
      <button
        type="button"
        className="step-row"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
      >
        <span className="step-icon">
          <StepIcon status={step.status} recovered={step.recovered} />
        </span>
        {step.agent && <span className="step-agent">{step.agent}</span>}
        <Sentence sentence={sentence} />
        {progress && (
          <span className="step-result">
            {progress.done} / {progress.total}
          </span>
        )}
        {!plan && step.status === "done" && step.result && (
          <span className="step-result anim-in">
            {resultPreview(step.result)}
          </span>
        )}
        {step.status === "denied" && (
          <span className="step-result anim-in">你拒絕了這個動作</span>
        )}
        {step.status === "error" && !step.recovered && (
          <span className="step-flag warn anim-in">工具錯誤</span>
        )}
        {step.status === "error" && step.recovered && (
          <span className="step-flag ch-flag-muted">出錯，已繼續</span>
        )}
        {step.status === "waiting" && (
          <span className="step-flag accent">等你確認</span>
        )}
        {step.status === "interrupted" && (
          <span className="step-flag warn">已中斷</span>
        )}
      </button>
      {/* 已經修正的錯誤不顯示內容，展開後才看得到。 */}
      {step.status === "error" && !step.recovered && !expanded && (
        <div className="step-error anim-shake">{step.result}</div>
      )}
      {expanded && <StepDetail step={step} plan={plan} />}
      {step.children && (
        <ol className="step-list substeps" aria-label="subagent 的步驟">
          {step.children.map((child) => (
            <StepRow key={child.id} step={child} />
          ))}
        </ol>
      )}
    </li>
  );
}

/** 展開後的內容：指令用終端機的樣子，計畫用清單，其他顯示原始參數與結果。 */
function StepDetail({
  step,
  plan,
}: {
  step: Step;
  plan: ReturnType<typeof planFromArguments>;
}) {
  const { command, cwd } = step.args;
  if (step.name === "run_command" && typeof command === "string") {
    return (
      <div className="step-detail anim-in">
        <div className="ch-term">
          {typeof cwd === "string" && cwd !== "." && cwd !== "" && (
            <div className="ch-term-cwd">{cwd}</div>
          )}
          <pre className="ch-term-cmd">
            <span aria-hidden="true">$ </span>
            {command}
          </pre>
          {step.result !== undefined && (
            <pre
              className={`ch-term-out${step.status === "error" ? " error" : ""}`}
            >
              {step.result}
            </pre>
          )}
        </div>
      </div>
    );
  }
  return (
    <div className="step-detail anim-in">
      {plan && <PlanList items={plan} />}
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
  );
}

function StepIcon({
  status,
  recovered,
}: {
  status: Step["status"];
  recovered?: boolean;
}) {
  if (status === "denied" || (status === "error" && recovered)) {
    return (
      <span className="ch-step-muted">
        <CrossIcon />
      </span>
    );
  }
  if (status === "done") {
    return (
      <span className="anim-pop ch-step-ok">
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
    <button
      type="button"
      className="file-card anim-in"
      onClick={onOpen}
      aria-label={`預覽 ${change.path}`}
    >
      <FileBadge path={change.path} />
      <span>
        <span className="name">{change.path.split("/").at(-1)}</span>
        <span className="meta">
          {change.path} · {change.edited ? "已修改" : "已寫入"}
        </span>
      </span>
    </button>
  );
}

/**
 * 沙盒產生的檔案：圖片顯示成縮圖，其他檔案是卡片（可以預覽與下載）。
 * 伺服器不能讀取檔案時沒有縮圖與下載，只顯示檔名。
 */
function ProducedFiles({
  paths,
  canLoad,
  onOpen,
}: {
  paths: string[];
  canLoad: boolean;
  onOpen?: (path: string) => void;
}) {
  const images = canLoad ? paths.filter(isThumbnail) : [];
  const others = paths.filter((path) => !images.includes(path));
  return (
    <>
      {images.length > 0 && (
        <ul className="ch-thumbs anim-in" aria-label="產生的圖片">
          {images.map((path) => (
            <li key={path}>
              <button
                type="button"
                className="ch-thumb"
                title={path}
                aria-label={`預覽 ${fileName(path)}`}
                disabled={!onOpen}
                onClick={() => onOpen?.(path)}
              >
                <img src={fileUrl(path)} alt={fileName(path)} loading="lazy" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {others.map((path) => (
        <div key={path} className="file-card ch-produced anim-in">
          <button
            type="button"
            className="ch-produced-open"
            aria-label={`預覽 ${path}`}
            disabled={!onOpen}
            onClick={() => onOpen?.(path)}
          >
            <FileBadge path={path} />
            <span className="ch-produced-main">
              <span className="name">{fileName(path)}</span>
              <span className="meta">{path} · 程式產生</span>
            </span>
          </button>
          {canLoad && (
            <a
              className="btn btn-outline ch-btn-small"
              href={fileUrl(path, true)}
              download
              aria-label={`下載 ${fileName(path)}`}
            >
              <DownloadIcon size={14} />
              下載
            </a>
          )}
        </div>
      ))}
    </>
  );
}

function prettyArgs(step: Step): string {
  if (Object.keys(step.args).length === 0) return step.rawArgs;
  return JSON.stringify(step.args, null, 2);
}
