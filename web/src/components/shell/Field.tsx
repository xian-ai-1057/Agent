/**
 * 有標籤、說明與錯誤訊息的輸入框。錯誤訊息用 aria-describedby 連到
 * 輸入框，螢幕閱讀器聚焦時會一起唸出來。
 */

import { type ComponentProps, useId } from "react";

export function Field({
  label,
  error,
  hint,
  className = "",
  ...input
}: ComponentProps<"input"> & {
  label: string;
  /** 這一欄的錯誤；有時輸入框標成 aria-invalid。 */
  error?: string;
  hint?: string;
}) {
  const id = useId();
  const errorId = `${id}-error`;
  const hintId = `${id}-hint`;
  const described =
    [error ? errorId : "", hint ? hintId : ""].filter(Boolean).join(" ") ||
    undefined;
  return (
    <div className="form-row field-row">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        className={`field ${className}`}
        aria-invalid={error ? true : undefined}
        aria-describedby={described}
        {...input}
      />
      {error && (
        <div id={errorId} className="inline-error field-error" role="alert">
          {error}
        </div>
      )}
      {hint && (
        <div id={hintId} className="help">
          {hint}
        </div>
      )}
    </div>
  );
}
