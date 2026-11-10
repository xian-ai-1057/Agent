/** 右側面板：agent 寫入（或想寫入）的檔案，可以看預覽或差異。 */

import { diffLines } from "diff";
import { useMemo } from "react";
import type { FileChange } from "../transcript.ts";
import { CloseIcon } from "./Icons.tsx";
import { Markdown } from "./Markdown.tsx";

export type PanelTab = "preview" | "diff";

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
  selected,
  tab,
  onSelect,
  onTab,
  onClose,
}: {
  files: FileChange[];
  selected?: string;
  tab: PanelTab;
  onSelect: (path: string) => void;
  onTab: (tab: PanelTab) => void;
  onClose: () => void;
}) {
  const current = files.find((file) => file.path === selected) ?? files[0];
  return (
    <aside className="panel anim-panel" aria-label="檔案">
      <div className="panel-header">
        <div className="tabs" role="tablist">
          <button
            type="button"
            role="tab"
            className="tab"
            aria-selected={tab === "preview"}
            onClick={() => onTab("preview")}
          >
            預覽
          </button>
          <button
            type="button"
            role="tab"
            className="tab"
            aria-selected={tab === "diff"}
            onClick={() => onTab("diff")}
          >
            變更
            {files.length > 0 && (
              <span className="count-pill anim-pop">{files.length}</span>
            )}
          </button>
        </div>
        <button
          type="button"
          className="icon-btn"
          aria-label="關閉面板"
          onClick={onClose}
        >
          <CloseIcon />
        </button>
      </div>

      {!current && (
        <div className="panel-empty">
          agent 寫入檔案時，內容會出現在這裡。
          <br />
          寫入前可以先看差異再決定要不要允許。
        </div>
      )}

      {current && (
        <div className="scroll">
          {files.length > 1 && (
            <div className="file-switch">
              {files.map((file) => (
                <button
                  key={file.path}
                  type="button"
                  aria-pressed={file.path === current.path}
                  onClick={() => onSelect(file.path)}
                >
                  {file.path}
                </button>
              ))}
            </div>
          )}
          <FileHeader file={current} />
          <div
            className={`file-note ${current.status === "pending" ? "pending" : ""}`}
          >
            {NOTES[current.status]}
            {current.before === undefined &&
              tab === "diff" &&
              "（從對話紀錄還原，沒有原本的內容可以比較）"}
          </div>
          {tab === "preview" ? (
            <Preview file={current} key={`p-${current.callId}`} />
          ) : (
            <Diff file={current} key={`d-${current.callId}`} />
          )}
        </div>
      )}
    </aside>
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

function Preview({ file }: { file: FileChange }) {
  const markdown = /\.(md|markdown)$/i.test(file.path);
  return (
    <div className="preview-body anim-in">
      {markdown ? <Markdown text={file.after} /> : <pre>{file.after}</pre>}
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
