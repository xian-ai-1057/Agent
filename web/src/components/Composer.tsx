/**
 * 輸入框。Enter 送出、Shift+Enter 換行；輸入法選字時按 Enter 不會送出。
 *
 * 有提供 onUpload 時可以附加檔案：按迴紋針（或 ＋ 選單）、把檔案拖進來
 * 或直接貼上。檔案選好就開始上傳，全部上傳完才能送出。
 *
 * 有提供 options 時（Python 版），左下角是 ＋ 選單（上傳、skills、工具），
 * 右下角是模型選單。
 *
 * 按下送出後輸入框立刻清空；送出失敗時把文字與附件放回來。
 */

import {
  type ClipboardEvent,
  type DragEvent,
  type KeyboardEvent,
  type ReactNode,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import type { UploadedFile } from "../../../src/web/protocol.ts";
import { formatBytes } from "../format.ts";
import "./chat/chat.css";
import { ModelMenu } from "./chat/ModelMenu.tsx";
import type { ComposerOptions } from "./chat/options.ts";
import { PlusMenu } from "./chat/PlusMenu.tsx";
import { ArrowUpIcon, CloseIcon, FileIcon, PaperclipIcon } from "./Icons.tsx";

const MAX_HEIGHT = 240;

interface Attachment {
  key: string;
  name: string;
  size: number;
  status: "uploading" | "done" | "error";
  path?: string;
  error?: string;
}

let nextKey = 0;

export function Composer({
  placeholder,
  busy,
  rows = 2,
  autoFocus,
  controls,
  draft,
  onSubmit,
  onStop,
  onUpload,
  maxUploadBytes,
  options,
}: {
  placeholder: string;
  busy: boolean;
  rows?: number;
  autoFocus?: boolean;
  controls?: ReactNode;
  /** 從外部填入的文字（例如點了建議）。 */
  draft?: { text: string };
  /** 回傳 false 表示送出失敗，保留輸入的內容與附件。 */
  onSubmit: (text: string, attachments: string[]) => Promise<boolean>;
  onStop?: () => void;
  /** 上傳一個檔案；沒有提供時不能附加檔案。 */
  onUpload?: (file: File) => Promise<UploadedFile>;
  maxUploadBytes?: number;
  /** 模型、工具、skills 選項；沒有提供時不顯示 ＋ 選單與模型選單。 */
  options?: ComposerOptions;
}) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [dragging, setDragging] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!draft) return;
    setText(draft.text);
    ref.current?.focus();
  }, [draft]);

  // 依內容自動調整高度；視窗寬度改變時（換行數不同）也重新量。
  const fit = () => {
    const element = ref.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, MAX_HEIGHT)}px`;
  };
  // biome-ignore lint/correctness/useExhaustiveDependencies: 內容改變時要重新量
  useLayoutEffect(fit, [text]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: fit 只用到 ref
  useEffect(() => {
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, []);

  const ready = attachments.filter((item) => item.status === "done");
  const pending = attachments.some((item) => item.status !== "done");
  const canSend =
    (text.trim().length > 0 || ready.length > 0) &&
    !pending &&
    !busy &&
    !sending;

  const submit = async () => {
    if (!canSend) return;
    setSending(true);
    const saved = { text, attachments };
    const paths = ready.map((item) => item.path ?? "");
    setText("");
    setAttachments([]);
    const ok = await onSubmit(saved.text.trim(), paths);
    setSending(false);
    if (!ok) {
      // 使用者在等待時又打了字就不覆蓋。
      setText((current) => current || saved.text);
      setAttachments((current) =>
        current.length > 0 ? current : saved.attachments,
      );
      ref.current?.focus();
    }
  };

  const update = (key: string, change: Partial<Attachment>) =>
    setAttachments((items) =>
      items.map((item) => (item.key === key ? { ...item, ...change } : item)),
    );

  const addFiles = (files: Iterable<File>) => {
    if (!onUpload) return;
    for (const file of files) {
      const key = `a${nextKey++}`;
      const tooBig = maxUploadBytes !== undefined && file.size > maxUploadBytes;
      setAttachments((items) => [
        ...items,
        {
          key,
          name: file.name,
          size: file.size,
          status: tooBig ? "error" : "uploading",
          error: tooBig
            ? `超過上限 ${formatBytes(maxUploadBytes ?? 0)}`
            : undefined,
        },
      ]);
      if (tooBig) continue;
      onUpload(file).then(
        (uploaded) =>
          update(key, {
            status: "done",
            path: uploaded.path,
            name: uploaded.name,
          }),
        (error: Error) =>
          update(key, { status: "error", error: error.message }),
      );
    }
  };

  const remove = (key: string) =>
    setAttachments((items) => items.filter((item) => item.key !== key));

  const onDragOver = (event: DragEvent) => {
    if (!onUpload || !event.dataTransfer.types.includes("Files")) return;
    event.preventDefault();
    setDragging(true);
  };

  const onDrop = (event: DragEvent) => {
    setDragging(false);
    if (!onUpload || event.dataTransfer.files.length === 0) return;
    event.preventDefault();
    addFiles(event.dataTransfer.files);
  };

  const onPaste = (event: ClipboardEvent) => {
    if (!onUpload || event.clipboardData.files.length === 0) return;
    event.preventDefault();
    addFiles(event.clipboardData.files);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey) return;
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    void submit();
  };

  return (
    <form
      className={`composer ch-composer${dragging ? " dragging" : ""}`}
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
      onDragOver={onDragOver}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      {attachments.length > 0 && (
        <ul className="attachments" aria-label="附加的檔案">
          {attachments.map((item) => (
            <li
              key={item.key}
              className={`attachment ${item.status}`}
              title={item.error ?? item.name}
            >
              {item.status === "uploading" ? (
                <span className="dot pulse" />
              ) : (
                <FileIcon size={14} />
              )}
              <span className="attachment-name">{item.name}</span>
              <span className="attachment-meta">
                {item.status === "uploading" && "上傳中…"}
                {item.status === "done" && formatBytes(item.size)}
                {item.status === "error" && item.error}
              </span>
              <button
                type="button"
                className="attachment-remove"
                aria-label={`移除 ${item.name}`}
                onClick={() => remove(item.key)}
              >
                <CloseIcon size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}
      <label className="sr-only" htmlFor="composer-input">
        {placeholder}
      </label>
      <textarea
        id="composer-input"
        ref={ref}
        rows={rows}
        value={text}
        placeholder={placeholder}
        // biome-ignore lint/a11y/noAutofocus: 新對話頁一打開就能輸入
        autoFocus={autoFocus}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
        onPaste={onPaste}
      />
      {options && <OptionChips options={options} />}
      <div className="composer-bar">
        {options && (
          <PlusMenu
            options={options}
            onUpload={onUpload ? () => fileInput.current?.click() : undefined}
          />
        )}
        {onUpload && (
          <>
            {!options && (
              <button
                type="button"
                className="icon-btn attach-btn"
                aria-label="附加檔案"
                title="附加檔案（也可以把檔案拖進來或貼上）"
                onClick={() => fileInput.current?.click()}
              >
                <PaperclipIcon />
              </button>
            )}
            <input
              ref={fileInput}
              type="file"
              multiple
              hidden
              onChange={(event) => {
                if (event.target.files) addFiles(event.target.files);
                event.target.value = "";
              }}
            />
          </>
        )}
        {controls}
        <span className="ch-bar-gap" />
        {options && <ModelMenu options={options} />}
        {busy && onStop ? (
          <button
            key="stop"
            type="button"
            className="send-btn ch-send stop"
            aria-label="停止"
            onClick={onStop}
          >
            <span className="stop-square" />
          </button>
        ) : (
          <button
            key="send"
            type="submit"
            className="send-btn ch-send"
            aria-label="送出"
            disabled={!canSend}
          >
            <ArrowUpIcon />
          </button>
        )}
      </div>
    </form>
  );
}

/** 移除標籤前先播完淡出（減少動態效果時直接移除）。 */
const CHIP_EXIT_MS = 140;

/** 選了的 skills 與停用的工具，顯示成可以移除的小標籤。 */
function OptionChips({ options }: { options: ComposerOptions }) {
  const [leaving, setLeaving] = useState<string[]>([]);
  const { value, tools } = options;
  const disabled =
    value.tools === null
      ? 0
      : tools.filter((tool) => !value.tools?.includes(tool.name)).length;
  if (value.skills.length === 0 && disabled === 0) return null;

  const remove = (
    key: string,
    change: Parameters<typeof options.onChange>[0],
  ) => {
    const reduce = window.matchMedia?.(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    if (reduce) {
      options.onChange(change);
      return;
    }
    setLeaving((items) => [...items, key]);
    setTimeout(() => {
      setLeaving((items) => items.filter((item) => item !== key));
      options.onChange(change);
    }, CHIP_EXIT_MS);
  };

  return (
    <ul className="ch-chips" aria-label="這段對話的選項">
      {value.skills.map((name) => (
        <li
          key={name}
          className={`ch-chip${leaving.includes(name) ? " leaving" : ""}`}
        >
          <span className="ch-chip-kind">skill</span>
          <span className="ch-chip-name">{name}</span>
          <button
            type="button"
            aria-label={`移除 skill ${name}`}
            onClick={() =>
              remove(name, {
                skills: value.skills.filter((item) => item !== name),
              })
            }
          >
            <CloseIcon size={12} />
          </button>
        </li>
      ))}
      {disabled > 0 && (
        <li
          className={`ch-chip${leaving.includes(":tools") ? " leaving" : ""}`}
        >
          <span className="ch-chip-name">已停用 {disabled} 個工具</span>
          <button
            type="button"
            aria-label="恢復使用全部工具"
            onClick={() => remove(":tools", { tools: null })}
          >
            <CloseIcon size={12} />
          </button>
        </li>
      )}
    </ul>
  );
}
