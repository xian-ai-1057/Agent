/** 倒讚時詢問原因的對話框（用共用的 Modal，Esc 與點背景會關閉）。 */

import { useId, useState } from "react";
import type { Feedback } from "../../../../src/web/protocol.ts";
import { CheckIcon } from "../Icons.tsx";
import { Modal } from "../shell/Dialog.tsx";

export const DOWN_REASONS = [
  "資訊不正確",
  "沒有照我的要求做",
  "回答不完整",
  "太長或太囉嗦",
];

export function FeedbackDialog({
  initial,
  onSubmit,
  onClose,
}: {
  initial?: Feedback;
  onSubmit: (feedback: Feedback) => void;
  onClose: () => void;
}) {
  const titleId = useId();
  const [reasons, setReasons] = useState<string[]>(initial?.reasons ?? []);
  const [comment, setComment] = useState(initial?.comment ?? "");

  const toggle = (reason: string) =>
    setReasons((items) =>
      items.includes(reason)
        ? items.filter((item) => item !== reason)
        : [...items, reason],
    );

  return (
    <Modal onClose={onClose} className="ch-dialog" labelledBy={titleId}>
      <form
        className="ch-dialog-body"
        onSubmit={(event) => {
          event.preventDefault();
          const text = comment.trim();
          onSubmit({
            rating: "down",
            reasons,
            ...(text ? { comment: text } : {}),
          });
        }}
      >
        <h2 id={titleId} className="dialog-title">
          哪裡不好？
        </h2>
        <p className="ch-dialog-lead">可以複選，也可以直接送出。</p>
        <fieldset className="ch-reasons">
          <legend className="sr-only">原因</legend>
          {DOWN_REASONS.map((reason) => {
            const checked = reasons.includes(reason);
            return (
              <label
                key={reason}
                className={`ch-reason${checked ? " on" : ""}`}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggle(reason)}
                />
                <span className={`ch-box${checked ? " on" : ""}`}>
                  {checked && <CheckIcon size={12} />}
                </span>
                {reason}
              </label>
            );
          })}
        </fieldset>
        <label className="ch-dialog-field">
          <span>補充說明（選填）</span>
          <textarea
            rows={3}
            value={comment}
            placeholder="例如：應該要怎麼回答才對"
            onChange={(event) => setComment(event.target.value)}
          />
        </label>
        <div className="dialog-actions ch-dialog-actions">
          <button type="button" className="btn btn-outline" onClick={onClose}>
            取消
          </button>
          <button type="submit" className="btn btn-accent">
            送出
          </button>
        </div>
      </form>
    </Modal>
  );
}
