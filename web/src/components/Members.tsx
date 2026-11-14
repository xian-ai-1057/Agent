/**
 * 管理平台 → 成員（多人帳號模式，只有管理者看得到）：列出成員，新增成員、
 * 重設密碼、換角色、停用或啟用。
 *
 * 密碼由伺服器產生，新增或重設後只顯示這一次，請管理者交給對方。
 * 伺服器拒絕的動作（例如停用最後一位管理者）把原因顯示在那一列。
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
import type {
  CurrentUser,
  IssuedPassword,
  MemberInfo,
} from "../../../src/web/protocol.ts";
import { api } from "../api.ts";
import { currentUser, memberFormErrors, ROLE_LABELS } from "../auth.ts";
import { formatAgo, useNow } from "../format.ts";
import { refreshProject, useStore } from "../store.ts";
import { COPY_LABELS, useCopy } from "./chat/copy.ts";
import { CopyIcon } from "./chat/icons.tsx";
import {
  CheckIcon,
  CrossIcon,
  KeyIcon,
  MoreIcon,
  ShieldIcon,
  UserPlusIcon,
} from "./Icons.tsx";
import { ConfirmDialog, Modal } from "./shell/Dialog.tsx";
import { Field } from "./shell/Field.tsx";
import { FloatingMenu } from "./shell/FloatingMenu.tsx";
import { Avatar } from "./shell/UserMenu.tsx";

type Pending =
  | { kind: "disable"; member: MemberInfo }
  | { kind: "reset"; member: MemberInfo };

export function Members() {
  const me = useStore((s) => currentUser(s.project));
  const [members, setMembers] = useState<MemberInfo[]>();
  const [loadError, setLoadError] = useState<string>();
  const [adding, setAdding] = useState(false);
  const [issued, setIssued] = useState<{
    title: string;
    password: string;
  }>();
  const [menu, setMenu] = useState<{ id: string; anchor: HTMLElement }>();
  const [pending, setPending] = useState<Pending>();
  const [rowError, setRowError] = useState<{ id: string; message: string }>();
  const [busy, setBusy] = useState<string>();
  const now = useNow(true, 60_000);
  const closeMenu = useCallback(() => setMenu(undefined), []);

  const load = useCallback(() => {
    setLoadError(undefined);
    api.members().then(setMembers, (error: Error) => {
      setLoadError(error.message);
    });
  }, []);
  useEffect(load, [load]);

  const replace = (updated: MemberInfo) =>
    setMembers((list) =>
      list?.map((item) => (item.id === updated.id ? updated : item)),
    );

  const update = async (
    member: MemberInfo,
    changes: Parameters<typeof api.updateMember>[1],
  ) => {
    setRowError(undefined);
    setBusy(member.id);
    try {
      replace(await api.updateMember(member.id, changes));
      // 改了自己的角色：帳號選單與管理平台看得到的東西跟著變。
      if (member.id === me?.id) void refreshProject();
    } catch (error) {
      setRowError({ id: member.id, message: (error as Error).message });
    }
    setBusy(undefined);
  };

  const showIssued = (title: string, result: IssuedPassword) => {
    replace(result.user);
    setIssued({ title, password: result.password });
  };

  const menuMember = members?.find((item) => item.id === menu?.id);

  return (
    <section aria-labelledby="h-members" className="settings-body">
      <div className="members-head">
        <div>
          <h2 id="h-members">成員</h2>
          <p className="lead">
            每位成員用自己的帳號登入，只看得到自己的對話、檔案與記憶。
          </p>
        </div>
        <button
          type="button"
          className="btn btn-accent"
          onClick={() => setAdding(true)}
        >
          <UserPlusIcon size={15} />
          新增成員
        </button>
      </div>

      {loadError && (
        <div className="box box-row" role="alert">
          <span className="inline-error">讀不到成員：{loadError}</span>
          <button type="button" className="btn btn-outline" onClick={load}>
            重試
          </button>
        </div>
      )}
      {!members && !loadError && (
        <p className="subtle" aria-busy="true">
          讀取中…
        </p>
      )}
      {members && (
        <ul className="member-list" aria-label="成員">
          {members.map((member) => (
            <li
              key={member.id}
              className={`member-row${member.disabled ? " disabled" : ""}`}
            >
              <Avatar name={member} />
              <div className="member-main">
                <div className="member-name">
                  <span className="member-display">{member.displayName}</span>
                  {member.id === me?.id && (
                    <span className="subtle">（你）</span>
                  )}
                  {member.role === "admin" && (
                    <span className="badge badge-muted">管理者</span>
                  )}
                  {member.disabled && (
                    <span className="badge badge-danger">停用</span>
                  )}
                </div>
                <div className="member-meta">
                  <span className="mono">{member.username}</span>
                  {" · "}
                  {member.lastSeenAt
                    ? `最後使用 ${formatAgo(member.lastSeenAt, now)}`
                    : "還沒登入過"}
                </div>
              </div>
              {busy === member.id && (
                <>
                  <span className="spinner spin" aria-hidden="true" />
                  <span className="sr-only">處理中</span>
                </>
              )}
              <button
                type="button"
                className="icon-btn"
                aria-label={`「${member.displayName}」的選項`}
                aria-haspopup="menu"
                aria-expanded={menu?.id === member.id}
                onClick={(event) =>
                  setMenu(
                    menu?.id === member.id
                      ? undefined
                      : { id: member.id, anchor: event.currentTarget },
                  )
                }
              >
                <MoreIcon />
              </button>
              {rowError?.id === member.id && (
                <p className="inline-error member-error" role="alert">
                  {rowError.message}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}

      {menu && menuMember && (
        <FloatingMenu anchor={menu.anchor} label="成員選項" onClose={closeMenu}>
          {/* 自己的密碼到使用者選單的「修改密碼」改。 */}
          {menuMember.id !== me?.id && (
            <button
              type="button"
              role="menuitem"
              className="menu-item"
              onClick={() => {
                closeMenu();
                setPending({ kind: "reset", member: menuMember });
              }}
            >
              <KeyIcon size={15} />
              重設密碼
            </button>
          )}
          <button
            type="button"
            role="menuitem"
            className="menu-item"
            onClick={() => {
              closeMenu();
              void update(menuMember, {
                role: menuMember.role === "admin" ? "member" : "admin",
              });
            }}
          >
            <ShieldIcon size={15} />
            {menuMember.role === "admin" ? "設為成員" : "設為管理者"}
          </button>
          {/* 不能停用自己，不然會把自己鎖在外面。 */}
          {menuMember.id !== me?.id && (
            <button
              type="button"
              role="menuitem"
              className={`menu-item${menuMember.disabled ? "" : " danger"}`}
              onClick={() => {
                closeMenu();
                if (menuMember.disabled) {
                  void update(menuMember, { disabled: false });
                } else {
                  setPending({ kind: "disable", member: menuMember });
                }
              }}
            >
              {menuMember.disabled ? (
                <CheckIcon size={15} />
              ) : (
                <CrossIcon size={15} />
              )}
              {menuMember.disabled ? "啟用" : "停用"}
            </button>
          )}
        </FloatingMenu>
      )}

      {pending?.kind === "disable" &&
        createPortal(
          <ConfirmDialog
            title={`停用「${pending.member.displayName}」？`}
            confirmLabel="停用"
            onConfirm={async () => {
              replace(
                await api.updateMember(pending.member.id, { disabled: true }),
              );
            }}
            onClose={() => setPending(undefined)}
          >
            <p>
              停用後 {pending.member.username}{" "}
              就不能登入，對話與檔案會保留，之後可以再啟用。
            </p>
          </ConfirmDialog>,
          document.body,
        )}
      {pending?.kind === "reset" &&
        createPortal(
          <ConfirmDialog
            title={`重設「${pending.member.displayName}」的密碼？`}
            confirmLabel="重設密碼"
            onConfirm={async () => {
              const result = await api.resetPassword(pending.member.id);
              showIssued(`${pending.member.displayName} 的新密碼`, result);
            }}
            onClose={() => setPending(undefined)}
          >
            <p>原本的密碼會馬上失效，會產生一組新的密碼。</p>
          </ConfirmDialog>,
          document.body,
        )}
      {adding &&
        createPortal(
          <AddMemberDialog
            onClose={() => setAdding(false)}
            onAdded={(result) => {
              setMembers((list) => [...(list ?? []), result.user]);
              setAdding(false);
              setIssued({
                title: `已新增 ${result.user.displayName}`,
                password: result.password,
              });
            }}
          />,
          document.body,
        )}
      {issued &&
        createPortal(
          <PasswordDialog
            title={issued.title}
            password={issued.password}
            onClose={() => setIssued(undefined)}
          />,
          document.body,
        )}
    </section>
  );
}

function AddMemberDialog({
  onClose,
  onAdded,
}: {
  onClose: () => void;
  onAdded: (result: IssuedPassword) => void;
}) {
  const titleId = useId();
  const formRef = useRef<HTMLFormElement>(null);
  const firstRef = useRef<HTMLInputElement>(null);
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [role, setRole] = useState<CurrentUser["role"]>("member");
  const [errors, setErrors] = useState<ReturnType<typeof memberFormErrors>>({});
  const [failure, setFailure] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (attempt === 0) return;
    formRef.current
      ?.querySelector<HTMLInputElement>("[aria-invalid='true']")
      ?.focus();
  }, [attempt]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const found = memberFormErrors({ username, displayName });
    setErrors(found);
    setAttempt((count) => count + 1);
    if (Object.keys(found).length > 0) return;
    setBusy(true);
    setFailure(undefined);
    try {
      onAdded(await api.addMember(username.trim(), displayName.trim(), role));
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
          新增成員
        </h2>
        <div className="dialog-fields">
          <Field
            ref={firstRef}
            label="帳號"
            className="mono"
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            value={username}
            error={errors.username}
            hint={
              errors.username ? undefined : "登入時輸入的名稱，建立後不能改"
            }
            onChange={(event) => {
              setUsername(event.target.value);
              setErrors((current) => ({ ...current, username: undefined }));
            }}
          />
          <Field
            label="顯示名稱"
            autoComplete="off"
            value={displayName}
            error={errors.displayName}
            onChange={(event) => {
              setDisplayName(event.target.value);
              setErrors((current) => ({ ...current, displayName: undefined }));
            }}
          />
          <fieldset className="form-row role-field">
            <legend className="label">角色</legend>
            <div className="segmented segmented-sm">
              {(["member", "admin"] as const).map((item) => (
                <button
                  key={item}
                  type="button"
                  aria-pressed={role === item}
                  onClick={() => setRole(item)}
                >
                  {ROLE_LABELS[item]}
                </button>
              ))}
            </div>
            <div className="help">
              {role === "admin"
                ? "管理者可以管理成員、模型連線、權限與工作資料夾。"
                : "成員只能使用自己的對話、檔案與記憶。"}
            </div>
          </fieldset>
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
            新增
          </button>
        </div>
      </form>
    </Modal>
  );
}

/** 只顯示一次的密碼，附複製按鈕。 */
function PasswordDialog({
  title,
  password,
  onClose,
}: {
  title: string;
  password: string;
  onClose: () => void;
}) {
  const titleId = useId();
  const copyRef = useRef<HTMLButtonElement>(null);
  const [state, copy] = useCopy();
  return (
    <Modal
      onClose={onClose}
      className="confirm-dialog"
      labelledBy={titleId}
      initialFocus={copyRef}
    >
      <h2 id={titleId} className="dialog-title">
        {title}
      </h2>
      <div className="dialog-body">
        <div className="issued-password">
          <span className="sr-only">密碼：</span>
          <code className="mono issued-code">{password}</code>
          <button
            ref={copyRef}
            type="button"
            className="btn btn-outline ch-btn-small"
            onClick={() => copy(password)}
          >
            {state === "copied" ? (
              <CheckIcon size={14} />
            ) : (
              <CopyIcon size={14} />
            )}
            <span aria-live="polite">{COPY_LABELS[state]}</span>
          </button>
        </div>
        <p className="help issued-note">
          這組密碼只會顯示這一次，請交給對方，登入後可以自己修改。
        </p>
      </div>
      <div className="dialog-actions">
        <button type="button" className="btn btn-accent" onClick={onClose}>
          完成
        </button>
      </div>
    </Modal>
  );
}
