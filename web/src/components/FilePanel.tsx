/**
 * 右側面板：預覽檔案、看 agent 寫入（或想寫入）的差異，以及這段對話
 * 碰過的所有檔案。
 *
 * 伺服器支援 /api/files（Python 版）時，預覽讀取真正的檔案；否則顯示
 * 工具呼叫裡要寫入的內容。
 */

import { diffLines } from "diff";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { fileUrl } from "../api.ts";
import type { FileChange } from "../transcript.ts";
import "./chat/chat.css";
import { FileBadge } from "./chat/FileBadge.tsx";
import { FilePreview } from "./chat/FilePreview.tsx";
import { fileName } from "./chat/filetype.ts";
import { DownloadIcon, ExpandIcon, ShrinkIcon } from "./chat/icons.tsx";
import { CloseIcon } from "./Icons.tsx";
import { Markdown } from "./Markdown.tsx";

export type PanelTab = "preview" | "diff" | "files";

const NOTES: Record<FileChange["status"], string> = {
  pending: "等你同意後才會寫入這個檔案。",
  writing: "寫入中…",
  written: "已寫入。",
  denied: "已拒絕，這個檔案沒有被寫入。",
  failed: "沒有寫入成功。",
};

/** 計算新增與刪除的行數。 */
export function lineStats(before: string | null | undefined, after: string) {
  let added = 0;
  let removed = 0;
  for (const part of diffLines(before ?? "", after)) {
    if (part.added) added += part.count ?? 0;
    if (part.removed) removed += part.count ?? 0;
  }
  return { added, removed };
}

export function FilePanel({
  files,
  attachments = [],
  produced = [],
  selected,
  tab,
  canLoad = false,
  onSelect,
  onTab,
  onClose,
}: {
  files: FileChange[];
  /** 使用者在這段對話附上的檔案路徑。 */
  attachments?: string[];
  /** 沙盒在這段對話產生的檔案路徑。 */
  produced?: string[];
  selected?: string;
  tab: PanelTab;
  /** 伺服器可以讀取工作資料夾的檔案（features.files）。 */
  canLoad?: boolean;
  onSelect: (path: string) => void;
  onTab: (tab: PanelTab) => void;
  onClose: () => void;
}) {
  const [full, setFull] = useState(false);
  const change = files.find((file) => file.path === selected);
  const diffFile = change ?? files[0];
  // 預覽的對象：選到的路徑，沒有時是最新的變更或附件。
  const previewPath =
    selected ?? files[0]?.path ?? produced[0] ?? attachments[0];
  const total = new Set([
    ...files.map((f) => f.path),
    ...produced,
    ...attachments,
  ]).size;

  // 全螢幕時按 Esc 回到原本的大小。對話框、彈出框或選單開著時，Esc
  // 是關掉那一層，不能連全螢幕一起退出。
  useEffect(() => {
    if (!full) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      if (document.querySelector("dialog[open]")) return;
      const target = event.target;
      if (
        target instanceof Element &&
        target.closest("dialog, [role='dialog'], [role='menu']")
      ) {
        return;
      }
      setFull(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [full]);

  return (
    <aside
      className={`panel anim-panel ch-panel${full ? " ch-panel-full" : ""}`}
      aria-label="檔案"
    >
      <div className="panel-header">
        <div className="tabs" role="tablist">
          <Tab id="preview" tab={tab} onTab={onTab}>
            預覽
          </Tab>
          <Tab id="diff" tab={tab} onTab={onTab}>
            變更
            {files.length > 0 && (
              <span className="count-pill anim-pop">{files.length}</span>
            )}
          </Tab>
          <Tab id="files" tab={tab} onTab={onTab}>
            這段對話的檔案
          </Tab>
        </div>
        <div className="ch-panel-tools">
          <button
            type="button"
            className="icon-btn"
            aria-label={full ? "結束全螢幕" : "全螢幕"}
            aria-pressed={full}
            title={full ? "結束全螢幕（Esc）" : "全螢幕"}
            onClick={() => setFull(!full)}
          >
            {full ? <ShrinkIcon /> : <ExpandIcon />}
          </button>
          <button
            type="button"
            className="icon-btn"
            aria-label="關閉面板"
            onClick={onClose}
          >
            <CloseIcon />
          </button>
        </div>
      </div>

      <div key={tab} className="ch-tab-body">
        {tab === "files" && (
          <FileList
            files={files}
            attachments={attachments}
            produced={produced}
            total={total}
            onOpen={(path) => {
              onSelect(path);
              onTab("preview");
            }}
          />
        )}

        {tab === "preview" &&
          (previewPath ? (
            <div className="scroll">
              <PreviewHeader path={previewPath} canLoad={canLoad} />
              <PreviewBody
                path={previewPath}
                change={files.find((file) => file.path === previewPath)}
                canLoad={canLoad}
              />
            </div>
          ) : (
            <Empty />
          ))}

        {tab === "diff" &&
          (diffFile ? (
            <div className="scroll">
              {files.length > 1 && (
                <div className="file-switch">
                  {files.map((file) => (
                    <button
                      key={file.path}
                      type="button"
                      aria-pressed={file.path === diffFile.path}
                      onClick={() => onSelect(file.path)}
                    >
                      {file.path}
                    </button>
                  ))}
                </div>
              )}
              <FileHeader file={diffFile} />
              <div
                className={`file-note ${diffFile.status === "pending" ? "pending" : ""}`}
              >
                {NOTES[diffFile.status]}
                {diffFile.before === undefined &&
                  "（從對話紀錄還原，沒有原本的內容可以比較）"}
                {diffFile.fragment && "（從對話紀錄還原，只有被取代的那一段）"}
              </div>
              <Diff file={diffFile} key={`d-${diffFile.callId}`} />
            </div>
          ) : (
            <Empty />
          ))}
      </div>
    </aside>
  );
}

function Tab({
  id,
  tab,
  onTab,
  children,
}: {
  id: PanelTab;
  tab: PanelTab;
  onTab: (tab: PanelTab) => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      className="tab"
      aria-selected={tab === id}
      onClick={() => onTab(id)}
    >
      {children}
    </button>
  );
}

function Empty() {
  return (
    <div className="panel-empty">
      agent 寫入檔案時，內容會出現在這裡。
      <br />
      寫入前可以先看差異再決定要不要允許。
    </div>
  );
}

/** 預覽上方：檔名、路徑、下載。 */
function PreviewHeader({ path, canLoad }: { path: string; canLoad: boolean }) {
  return (
    <div className="file-head ch-preview-head">
      <div className="ch-preview-title">
        <span className="ch-preview-name">{fileName(path)}</span>
        <span className="mono subtle ch-preview-path">{path}</span>
      </div>
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
  );
}

function PreviewBody({
  path,
  change,
  canLoad,
}: {
  path: string;
  change?: FileChange;
  canLoad: boolean;
}) {
  // 還沒寫入（或沒寫成功）的檔案，磁碟上不是這份內容，顯示工具呼叫裡的。
  const fromArgs = change && (!canLoad || change.status !== "written");
  if (change && fromArgs) {
    const markdown = /\.(md|markdown)$/i.test(change.path);
    return (
      <>
        <div
          className={`file-note ${change.status === "pending" ? "pending" : ""}`}
        >
          {NOTES[change.status]}
          {change.fragment && "（只有修改後的那一段）"}
        </div>
        <div className="preview-body anim-in">
          {markdown ? (
            <Markdown text={change.after} />
          ) : (
            <pre>{change.after}</pre>
          )}
        </div>
      </>
    );
  }
  if (!canLoad) {
    return (
      <div className="ch-preview-state">
        這個版本的伺服器不能預覽上傳的檔案。
      </div>
    );
  }
  return (
    <div className="anim-in">
      <FilePreview key={path} path={path} />
    </div>
  );
}

/** 這段對話寫入、程式產生或附上的檔案。 */
function FileList({
  files,
  attachments,
  produced,
  total,
  onOpen,
}: {
  files: FileChange[];
  attachments: string[];
  produced: string[];
  total: number;
  onOpen: (path: string) => void;
}) {
  if (total === 0) {
    return (
      <div className="panel-empty">
        這段對話還沒有寫入、產生或附上任何檔案。
      </div>
    );
  }
  const seen = new Set(files.map((file) => file.path));
  const rows = [
    ...files.map((file) => ({
      path: file.path,
      meta:
        file.edited && file.status === "written"
          ? "已修改"
          : NOTES[file.status].replace(/。$/, ""),
    })),
    ...produced
      .filter((path) => !seen.has(path))
      .map((path) => ({ path, meta: "程式產生的檔案" })),
  ];
  for (const path of produced) seen.add(path);
  rows.push(
    ...attachments
      .filter((path) => !seen.has(path))
      .map((path) => ({ path, meta: "你附上的檔案" })),
  );
  return (
    <div className="scroll">
      <ul className="ch-file-list">
        {rows.map((row) => (
          <li key={row.path}>
            <button
              type="button"
              className="ch-file-row"
              onClick={() => onOpen(row.path)}
            >
              <FileBadge path={row.path} />
              <span className="ch-file-main">
                <span className="ch-file-name">{fileName(row.path)}</span>
                <span className="ch-file-meta">
                  {row.path} · {row.meta}
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function FileHeader({ file }: { file: FileChange }) {
  const stats = lineStats(file.before, file.after);
  return (
    <div className="file-head">
      <span className="mono" style={{ fontSize: 13, fontWeight: 500 }}>
        {file.path}
      </span>
      <span className="mono subtle">
        {file.before === null ? "新檔案 " : ""}
        <span className="plus">+{stats.added}</span>{" "}
        <span className="minus">−{stats.removed}</span>
      </span>
    </div>
  );
}

interface Line {
  key: string;
  kind: "add" | "del" | "same";
  oldNo?: number;
  newNo?: number;
  text: string;
}

function Diff({ file }: { file: FileChange }) {
  const lines = useMemo(() => {
    const result: Line[] = [];
    let oldNo = 1;
    let newNo = 1;
    for (const part of diffLines(file.before ?? "", file.after)) {
      const texts = part.value.replace(/\n$/, "").split("\n");
      for (const text of texts) {
        if (part.added) {
          result.push({ key: `a${newNo}`, kind: "add", newNo: newNo++, text });
        } else if (part.removed) {
          result.push({ key: `d${oldNo}`, kind: "del", oldNo: oldNo++, text });
        } else {
          result.push({
            key: `s${oldNo}-${newNo}`,
            kind: "same",
            oldNo: oldNo++,
            newNo: newNo++,
            text,
          });
        }
      }
    }
    return result;
  }, [file.before, file.after]);

  return (
    <div className="diff anim-in">
      {lines.map((line) => (
        <div key={line.key} className={`diff-line ${line.kind}`}>
          <span className="num">{line.oldNo ?? ""}</span>
          <span className="num">{line.newNo ?? ""}</span>
          <span className="sign">
            {line.kind === "add" ? "+" : line.kind === "del" ? "−" : ""}
          </span>
          <span>{line.text || " "}</span>
        </div>
      ))}
    </div>
  );
}
