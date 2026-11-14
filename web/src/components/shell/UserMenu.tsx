/**
 * 側欄底部登入的人（多人帳號模式才顯示）：頭像、名稱、管理者標籤，
 * 點了打開選單，可以修改密碼或登出。管理者在聊天介面多一個「管理平台」，
 * 在管理平台裡則是「回到聊天」（放在側欄，這裡不重複）。
 */

import {
  type FormEvent,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { api } from "../../api.ts";
import {
  currentUser,
  initialOf,
  MIN_PASSWORD,
  type PasswordErrors,
  type PasswordForm,
  passwordFormErrors,
} from "../../auth.ts";
import { navigate, useRoute } from "../../router.ts";
import { signOut, useStore } from "../../store.ts";
import { KeyIcon, LogoutIcon, ShieldIcon } from "../Icons.tsx";
import { Modal } from "./Dialog.tsx";
import { Field } from "./Field.tsx";
import { FloatingMenu } from "./FloatingMenu.tsx";
import { showToast } from "./toast.tsx";

export function UserMenu() {
  const user = useStore((s) => currentUser(s.project));
  const inAdmin = useRoute().page === "admin";
  const [anchor, setAnchor] = useState<HTMLElement>();
  const [changing, setChanging] = useState(false);
  const close = useCallback(() => setAnchor(undefined), []);
  if (!user) return null;

  return (
    <>
      <button
        type="button"
        className="side-item side-user"
        aria-haspopup="menu"
        aria-expanded={Boolean(anchor)}
        title={user.username}
        onClick={(event) => setAnchor(anchor ? undefined : event.currentTarget)}
      >
        <Avatar name={user} />
        <span className="grow">{user.displayName}</span>
        {user.role === "admin" && (
          <span className="badge badge-muted">管理者</span>
        )}
      </button>
      {anchor && (
        <FloatingMenu
          anchor={anchor}
          align="start"
          label="帳號選項"
          onClose={close}
        >
          {user.role === "admin" && !inAdmin && (
            <button
              type="button"
              role="menuitem"
              className="menu-item"
              onClick={() => {
                close();
                navigate("/admin");
              }}
            >
              <ShieldIcon size={15} />
              管理平台
            </button>
          )}
          <button
            type="button"
            role="menuitem"
            className="menu-item"
            onClick={() => {
              close();
              setChanging(true);
            }}
          >
            <KeyIcon size={15} />
            修改密碼
          </button>
          <button
            type="button"
            role="menuitem"
            className="menu-item"
            onClick={() => {
              close();
              void signOut();
            }}
          >
            <LogoutIcon size={15} />
            登出
          </button>
        </FloatingMenu>
      )}
      {changing &&
        // 放在 body 底下：窄螢幕的抽屜收起時，對話框不能跟著被藏起來。
        createPortal(
          <ChangePasswordDialog onClose={() => setChanging(false)} />,
          document.body,
        )}
    </>
  );
}

/** 圓形頭像，顯示名稱的第一個字。 */
export function Avatar({
  name,
}: {
  name: { displayName: string; username: string };
}) {
  return (
    <span className="avatar" aria-hidden="true">
      {initialOf(name)}
    </span>
  );
}

const EMPTY: PasswordForm = { current: "", next: "", confirm: "" };

function ChangePasswordDialog({ onClose }: { onClose: () => void }) {
  const titleId = useId();
  const formRef = useRef<HTMLFormElement>(null);
  const firstRef = useRef<HTMLInputElement>(null);
  const [form, setForm] = useState<PasswordForm>(EMPTY);
  const [errors, setErrors] = useState<PasswordErrors>({});
  const [failure, setFailure] = useState<string>();
  const [busy, setBusy] = useState(false);
  // 每按一次送出加一：檢查出錯誤後把焦點移到第一個有問題的欄位。
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (attempt === 0) return;
    formRef.current
      ?.querySelector<HTMLInputElement>("[aria-invalid='true']")
      ?.focus();
  }, [attempt]);

  const edit = (key: keyof PasswordForm, value: string) => {
    setForm((current) => ({ ...current, [key]: value }));
    // 改了的欄位先拿掉錯誤，下次送出再檢查。
    setErrors((current) => ({ ...current, [key]: undefined }));
    setFailure(undefined);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const found = passwordFormErrors(form);
    setErrors(found);
    setAttempt((count) => count + 1);
    if (Object.keys(found).length > 0) return;
    setBusy(true);
    try {
      await api.changePassword(form.current, form.next);
      showToast("密碼已更新");
      onClose();
    } catch (error) {
      setFailure((error as Error).message);
      setBusy(false);
    }
  };

  return (
    <Modal
      onClose={onClose}
      className="confirm-dialog form-dialog"
      labelledBy={titleId}
      initialFocus={firstRef}
    >
      <form ref={formRef} onSubmit={submit} noValidate>
        <h2 id={titleId} className="dialog-title">
          修改密碼
        </h2>
        <div className="dialog-fields">
          <Field
            ref={firstRef}
            label="目前密碼"
            type="password"
            autoComplete="current-password"
            value={form.current}
            error={errors.current}
            onChange={(event) => edit("current", event.target.value)}
          />
          <Field
            label="新密碼"
            type="password"
            autoComplete="new-password"
            value={form.next}
            error={errors.next}
            hint={errors.next ? undefined : `至少 ${MIN_PASSWORD} 個字`}
            onChange={(event) => edit("next", event.target.value)}
          />
          <Field
            label="再輸入一次"
            type="password"
            autoComplete="new-password"
            value={form.confirm}
            error={errors.confirm}
            onChange={(event) => edit("confirm", event.target.value)}
          />
        </div>
        {failure && (
          <p className="inline-error" role="alert">
            {failure}
          </p>
        )}
        <div className="dialog-actions">
          <button type="button" className="btn btn-outline" onClick={onClose}>
            取消
          </button>
          <button type="submit" className="btn btn-accent" disabled={busy}>
            {busy && <span className="spinner spin" aria-hidden="true" />}
            更新密碼
          </button>
        </div>
      </form>
    </Modal>
  );
}
