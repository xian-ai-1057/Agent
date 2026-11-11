/**
 * 回覆底下的動作：複製、讚、倒讚（伺服器支援時），以及產生這則回覆的模型。
 */

import { useEffect, useState } from "react";
import type { Feedback } from "../../../../src/web/protocol.ts";
import { CheckIcon } from "../Icons.tsx";
import { COPY_LABELS, useCopy } from "./copy.ts";
import { FeedbackDialog } from "./FeedbackDialog.tsx";
import { CopyIcon, ThumbDownIcon, ThumbUpIcon } from "./icons.tsx";

export interface ReplyActions {
  text: string;
  /** assistant 訊息在 messages 裡的索引；沒有時不能評價。 */
  index?: number;
  feedback?: Feedback;
  /** 伺服器支援評價時才有。 */
  onFeedback?: (index: number, feedback: Feedback | null) => Promise<void>;
  /** 產生這則回覆的模型（顯示用名稱）。 */
  model?: string;
}

export function MessageActions({
  text,
  index,
  feedback,
  onFeedback,
  model,
}: ReplyActions) {
  const [copyState, copy] = useCopy();
  const [current, setCurrent] = useState<Feedback | undefined>(feedback);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string>();

  // 伺服器的資料更新時（重新讀取對話）跟著更新。
  useEffect(() => setCurrent(feedback), [feedback]);

  const canRate = onFeedback !== undefined && index !== undefined;

  const save = async (next: Feedback | null) => {
    if (!onFeedback || index === undefined) return;
    const previous = current;
    setCurrent(next ?? undefined);
    setError(undefined);
    try {
      await onFeedback(index, next);
    } catch (failure) {
      setCurrent(previous);
      setError(`沒有存到：${(failure as Error).message}`);
    }
  };

  return (
    <div className="ch-actions">
      <button
        type="button"
        className="ch-action"
        aria-label={copyState === "idle" ? "複製回覆" : COPY_LABELS[copyState]}
        title={COPY_LABELS[copyState]}
        onClick={() => copy(text)}
      >
        {copyState === "copied" ? (
          <CheckIcon size={15} />
        ) : (
          <CopyIcon size={15} />
        )}
        {copyState !== "idle" && (
          <span className="ch-action-label" aria-live="polite">
            {COPY_LABELS[copyState]}
          </span>
        )}
      </button>
      {canRate && (
        <>
          <button
            type="button"
            className="ch-action"
            aria-label="讚"
            aria-pressed={current?.rating === "up"}
            title="讚"
            onClick={() =>
              void save(current?.rating === "up" ? null : { rating: "up" })
            }
          >
            <ThumbUpIcon size={15} />
          </button>
          <button
            type="button"
            className="ch-action"
            aria-label="倒讚"
            aria-pressed={current?.rating === "down"}
            title="倒讚"
            onClick={() => {
              if (current?.rating === "down") void save(null);
              else setAsking(true);
            }}
          >
            <ThumbDownIcon size={15} />
          </button>
        </>
      )}
      {model && (
        <span className="ch-model-tag" title="產生這則回覆的模型">
          {model}
        </span>
      )}
      {error && (
        <span className="ch-action-error" role="alert">
          {error}
        </span>
      )}
      {asking && (
        <FeedbackDialog
          initial={current?.rating === "down" ? current : undefined}
          onClose={() => setAsking(false)}
          onSubmit={(next) => {
            setAsking(false);
            void save(next);
          }}
        />
      )}
    </div>
  );
}

/** 使用者訊息的複製按鈕（滑過訊息時出現）。 */
export function UserCopy({ text }: { text: string }) {
  const [state, copy] = useCopy();
  return (
    <button
      type="button"
      className="ch-action ch-user-copy"
      aria-label={state === "idle" ? "複製訊息" : COPY_LABELS[state]}
      title={COPY_LABELS[state]}
      onClick={() => copy(text)}
    >
      {state === "copied" ? <CheckIcon size={14} /> : <CopyIcon size={14} />}
    </button>
  );
}
