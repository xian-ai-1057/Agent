/**
 * 管理平台（網址 /admin/<分類>）：和聊天介面分開的版面，只有管理者能用。
 *
 * 聊天介面對每個人都一樣；模型連線、全站權限與成員管理這些會影響所有
 * 人的設定集中在這裡。左邊是管理平台自己的選單，底下可以回到聊天介面。
 * 成員打開這個網址時只看到沒有權限的說明（伺服器本來就會拒絕他們的
 * 修改）。
 *
 * 模型與權限的改動先放在草稿裡，按「儲存」才寫回 harness.yaml。
 */

import { useEffect, useState } from "react";
import { api } from "../../api.ts";
import { adminSections, canAdmin } from "../../auth.ts";
import { type AdminSection, navigate } from "../../router.ts";
import { refreshProject, useStore } from "../../store.ts";
import {
  ArrowLeftIcon,
  CheckIcon,
  ChipIcon,
  ShieldIcon,
  UsersIcon,
} from "../Icons.tsx";
import { Members } from "../Members.tsx";
import { Link } from "../Sidebar.tsx";
import { UserMenu } from "../shell/UserMenu.tsx";
import {
  type Draft,
  ModelSection,
  PermissionSection,
  toDraft,
} from "./sections.tsx";

export const ADMIN_LABELS: Record<AdminSection, string> = {
  model: "模型",
  permissions: "權限",
  members: "成員",
};

const ADMIN_ICONS: Record<AdminSection, typeof ChipIcon> = {
  model: ChipIcon,
  permissions: ShieldIcon,
  members: UsersIcon,
};

export function AdminApp({ section }: { section?: AdminSection }) {
  const project = useStore((s) => s.project);
  const sections = adminSections(project);
  const shown = section && sections.includes(section) ? section : sections[0];
  const allowed = canAdmin(project);

  // 網址沒有指定分類、指到看不到的分類，或是搬過來的舊網址
  // （/settings/model）時，換成 /admin/<分類>。
  useEffect(() => {
    if (!project || !allowed || !shown) return;
    const path = `/admin/${shown}`;
    if (location.pathname !== path) navigate(path, { replace: true });
  }, [project, allowed, shown]);

  useEffect(() => {
    document.title = shown
      ? `${ADMIN_LABELS[shown]} · 管理平台 · Harness`
      : "管理平台 · Harness";
  }, [shown]);

  if (!project) return <div className="app admin-app" aria-busy="true" />;
  if (!allowed) return <NoAccess />;

  return (
    <div className="app admin-app">
      <nav className="admin-nav" aria-label="管理平台">
        <div className="side-brand">
          <span className="logo" aria-hidden="true" />
          Harness
          <span className="admin-tag">管理平台</span>
        </div>
        <div className="admin-links">
          {sections.map((id) => {
            const Icon = ADMIN_ICONS[id];
            return (
              <Link
                key={id}
                to={`/admin/${id}`}
                className={`side-item ${id === shown ? "active" : ""}`}
                current={id === shown}
              >
                <Icon />
                <span className="grow">{ADMIN_LABELS[id]}</span>
              </Link>
            );
          })}
        </div>
        <div className="side-footer">
          <Link to="/" className="side-item">
            <ArrowLeftIcon />
            <span className="grow">回到聊天</span>
          </Link>
          <UserMenu />
        </div>
      </nav>
      {shown && <AdminMain key={shown} section={shown} />}
    </div>
  );
}

function AdminMain({ section }: { section: AdminSection }) {
  const project = useStore((s) => s.project);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!saved) return;
    const timer = setTimeout(() => setSaved(false), 2400);
    return () => clearTimeout(timer);
  }, [saved]);

  if (!project) return null;

  const base = toDraft(project);
  const current = draft ?? base;
  const modelDirty =
    current.model !== base.model || current.maxTurns !== base.maxTurns;
  const permissionsDirty =
    JSON.stringify(current.permissions) !== JSON.stringify(base.permissions);
  const dirty = modelDirty || permissionsDirty;

  const edit = (change: (value: Draft) => Draft) => {
    setDraft(change(structuredClone(current)));
    setError(undefined);
  };

  const save = async () => {
    const maxTurns = current.maxTurns.trim() ? Number(current.maxTurns) : null;
    if (maxTurns !== null && !(Number.isInteger(maxTurns) && maxTurns > 0)) {
      setError("每次最多回合必須是正整數");
      return;
    }
    setSaving(true);
    try {
      if (modelDirty) {
        await api.saveModel(current.model.trim() || null, maxTurns);
      }
      if (permissionsDirty) await api.savePermissions(current.permissions);
      await refreshProject();
      setDraft(null);
      setSaved(true);
    } catch (failure) {
      setError((failure as Error).message);
    }
    setSaving(false);
  };

  return (
    <main className="main">
      <header className="main-header">
        <div className="main-title">
          <h1>管理平台</h1>
          <span className="subtle mono">
            {section === "members"
              ? project.name
              : `${project.name} / harness.yaml`}
          </span>
        </div>
      </header>
      <div className="scroll">
        <div className="settings admin-body">
          <div className="settings-body anim-in">
            {section === "model" && (
              <ModelSection
                project={project}
                draft={current}
                edit={edit}
                saveBarShown={dirty}
              />
            )}
            {section === "permissions" && (
              <PermissionSection
                project={project}
                draft={current}
                edit={edit}
              />
            )}
            {section === "members" && <Members />}
          </div>
        </div>
      </div>

      {dirty && (
        <section className="save-bar" aria-label="未儲存的變更">
          <span>{error ?? "harness.yaml 有未儲存的變更"}</span>
          <div style={{ display: "flex", gap: 6 }}>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => {
                setDraft(null);
                setError(undefined);
              }}
            >
              捨棄
            </button>
            <button
              type="button"
              className="btn btn-accent"
              disabled={saving}
              onClick={save}
            >
              儲存
            </button>
          </div>
        </section>
      )}
      {saved && !dirty && (
        <div className="toast" role="status">
          <CheckIcon size={14} />
          已儲存到 harness.yaml
        </div>
      )}
    </main>
  );
}

/** 成員打開管理平台的網址時顯示。 */
function NoAccess() {
  return (
    <main className="login-page">
      <div className="login-card anim-rise" role="alert">
        <div className="login-brand">
          <span className="logo" aria-hidden="true" />
          Harness
        </div>
        <h1 className="login-title">管理平台只有管理者能使用</h1>
        <p className="help">
          模型、權限與成員由管理者設定。需要調整的話，請聯絡你的管理者。
        </p>
        <Link to="/" className="btn btn-accent login-submit">
          回到聊天
        </Link>
      </div>
    </main>
  );
}
