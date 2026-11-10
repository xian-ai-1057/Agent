/**
 * 輸入框。Enter 送出、Shift+Enter 換行；輸入法選字時按 Enter 不會送出。
 */

import {
  type KeyboardEvent,
  type ReactNode,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { ArrowUpIcon } from "./Icons.tsx";

const MAX_HEIGHT = 240;

export function Composer({
  placeholder,
  busy,
  rows = 2,
  autoFocus,
  controls,
  draft,
  onSubmit,
  onStop,
}: {
  placeholder: string;
  busy: boolean;
  rows?: number;
  autoFocus?: boolean;
  controls?: ReactNode;
  /** 從外部填入的文字（例如點了建議）。 */
  draft?: { text: string };
  /** 回傳 false 表示送出失敗，保留輸入的內容。 */
  onSubmit: (text: string) => Promise<boolean>;
  onStop?: () => void;
}) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!draft) return;
    setText(draft.text);
    ref.current?.focus();
  }, [draft]);

  // 依內容自動調整高度。
  // biome-ignore lint/correctness/useExhaustiveDependencies: 內容改變時要重新量
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, MAX_HEIGHT)}px`;
  }, [text]);

  const canSend = text.trim().length > 0 && !busy && !sending;

  const submit = async () => {
    if (!canSend) return;
    setSending(true);
    const ok = await onSubmit(text.trim());
    setSending(false);
    if (ok) setText("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey) return;
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    void submit();
  };

  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
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
      />
      <div className="composer-bar">
        {controls}
        <span style={{ flex: 1 }} />
        {busy && onStop ? (
          <button
            type="button"
            className="send-btn"
            aria-label="停止"
            onClick={onStop}
          >
            <span className="stop-square" />
          </button>
        ) : (
          <button
            type="submit"
            className="send-btn"
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
