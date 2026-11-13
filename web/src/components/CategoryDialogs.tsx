/**
 * 專案（對話的分類）用到的對話框：新增或改名，以及把對話移到專案。
 */

import { type FormEvent, useId, useRef, useState } from "react";
import { createCategory, useStore } from "../store.ts";
import { AddIcon, CheckIcon, FolderIcon } from "./Icons.tsx";
import { Modal } from "./shell/Dialog.tsx";
import { Field } from "./shell/Field.tsx";

/** 專案名稱的長度上限，和伺服器的 NAME_LIMIT 一致。 */
export const CATEGORY_NAME_LIMIT = 50;

/** 輸入專案名稱：新增或改名。 */
export function CategoryNameDialog({
  title,
  initial = "",
  confirmLabel,
  onSubmit,
  onClose,
}: {
  title: string;
  initial?: string;
  confirmLabel: string;
  onSubmit: (name: string) => Promise<void>;
  onClose: () => void;
}) {
  const titleId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState(initial);
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setError("請輸入專案名稱");
      inputRef.current?.focus();
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      await onSubmit(trimmed);
      onClose();
    } catch (failure) {
      setError((failure as Error).message);
      setBusy(false);
    }
  };

  return (
    <Modal
      onClose={onClose}
      className="confirm-dialog"
      labelledBy={titleId}
      initialFocus={inputRef}
    >
      <form onSubmit={submit} noValidate>
        <h2 id={titleId} className="dialog-title">
          {title}
        </h2>
        <div className="dialog-fields">
          <Field
            ref={inputRef}
            label="專案名稱"
            value={name}
            maxLength={CATEGORY_NAME_LIMIT}
            placeholder="例如：十月營運月報"
            error={error}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <div className="dialog-actions">
          <button type="button" className="btn btn-outline" onClick={onClose}>
            取消
          </button>
          <button type="submit" className="btn btn-accent" disabled={busy}>
            {confirmLabel}
          </button>
        </div>
      </form>
    </Modal>
  );
}

/**
 * 把對話移到專案：列出自己的專案，也可以直接新增一個再移進去。
 * current 是這些對話目前共同所在的專案（有的話不能再選它）。
 */
export function MoveDialog({
  count,
  current,
  onMove,
  onClose,
}: {
  count: number;
  current?: string | null;
  onMove: (category: string | null) => Promise<void>;
  onClose: () => void;
}) {
  const titleId = useId();
  const categories = useStore((s) => s.categories);
  const firstRef = useRef<HTMLButtonElement>(null);
  const [creating, setCreating] = useState(categories.length === 0);
  const [name, setName] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  const move = async (category: string | null) => {
    setBusy(true);
    setError(undefined);
    try {
      await onMove(category);
      onClose();
    } catch (failure) {
      setError((failure as Error).message);
      setBusy(false);
    }
  };

  const createAndMove = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setError("請輸入專案名稱");
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      const created = await createCategory(trimmed);
      await onMove(created.id);
      onClose();
    } catch (failure) {
      setError((failure as Error).message);
      setBusy(false);
    }
  };

  return (
    <Modal
      onClose={onClose}
      className="confirm-dialog move-dialog"
      labelledBy={titleId}
      initialFocus={firstRef}
    >
      <h2 id={titleId} className="dialog-title">
        {count > 1 ? `把 ${count} 段對話移到專案` : "移到專案"}
      </h2>
      <ul className="move-list">
        {categories.map((item, index) => (
          <li key={item.id}>
            <button
              ref={index === 0 ? firstRef : undefined}
              type="button"
              className="move-option"
              disabled={busy || item.id === current}
              onClick={() => move(item.id)}
            >
              <FolderIcon size={15} />
              <span className="move-name">{item.name}</span>
              {item.id === current ? (
                <span className="subtle">
                  <CheckIcon size={14} /> 目前所在
                </span>
              ) : (
                <span className="subtle">{item.sessionCount}</span>
              )}
            </button>
          </li>
        ))}
        {current && (
          <li>
            <button
              type="button"
              className="move-option"
              disabled={busy}
              onClick={() => move(null)}
            >
              <span className="move-none" aria-hidden="true" />
              <span className="move-name">移出專案（未分類）</span>
            </button>
          </li>
        )}
      </ul>
      {creating ? (
        <form className="move-create" onSubmit={createAndMove} noValidate>
          <Field
            label="新專案"
            value={name}
            maxLength={CATEGORY_NAME_LIMIT}
            placeholder="輸入專案名稱"
            autoFocus
            onChange={(event) => setName(event.target.value)}
          />
          <button type="submit" className="btn btn-accent" disabled={busy}>
            新增並移入
          </button>
        </form>
      ) : (
        <button
          type="button"
          className="btn btn-ghost move-new"
          onClick={() => setCreating(true)}
        >
          <AddIcon size={15} />
          新增專案
        </button>
      )}
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      <div className="dialog-actions">
        <button type="button" className="btn btn-outline" onClick={onClose}>
          取消
        </button>
      </div>
    </Modal>
  );
}
