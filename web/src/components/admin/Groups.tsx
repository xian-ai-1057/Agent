/**
 * 管理平台 → 群組（多人帳號模式）：管理者決定每位成員可以使用的
 * agent、工具組、知識庫與知識庫裡的標籤。模型不在這裡管：誰能用哪些
 * 模型、額度與限流由模型中台（LiteLLM Proxy）決定。
 *
 * 參考 MaiAgent 的角色與 LiteLLM 的 access group：每位成員自動屬於「所
 * 有人」，另外可以加入其他群組，能用的資源是所屬群組加起來。新建的群組
 * 什麼都不能用，再一類一類勾選。管理者不受群組限制。
 *
 * 列表點一個群組進入編輯；改動放在草稿裡，按「儲存」才送出。
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
  AccessGroup,
  AccessKind,
  GroupCatalog,
  MemberInfo,
} from "../../../../src/web/protocol.ts";
import { api, type GroupChanges } from "../../api.ts";
import { ArrowLeftIcon, ChevronIcon, TrashIcon, UsersIcon } from "../Icons.tsx";
import { ConfirmDialog, Modal } from "../shell/Dialog.tsx";
import { Field } from "../shell/Field.tsx";
import { showToast } from "../shell/toast.tsx";
import {
  ACCESS_KINDS,
  accessSummary,
  groupDirty,
  isKnowledgeKind,
  KIND_LABELS,
} from "./groupAccess.ts";

export function Groups() {
  const [groups, setGroups] = useState<AccessGroup[]>();
  const [catalog, setCatalog] = useState<GroupCatalog>();
  const [members, setMembers] = useState<MemberInfo[]>([]);
  const [loadError, setLoadError] = useState<string>();
  const [editing, setEditing] = useState<string>();
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    setLoadError(undefined);
    try {
      const [list, people] = await Promise.all([api.groups(), api.members()]);
      setGroups(list.groups);
      setCatalog(list.catalog);
      setMembers(people);
    } catch (error) {
      setLoadError((error as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const replace = (group: AccessGroup) =>
    setGroups((list) =>
      list?.map((item) => (item.id === group.id ? group : item)),
    );

  const current = groups?.find((item) => item.id === editing);
  if (current && catalog) {
    return (
      <GroupEditor
        key={current.id}
        group={current}
        catalog={catalog}
        members={members}
        onBack={() => setEditing(undefined)}
        onSaved={replace}
        onDeleted={() => {
          setGroups((list) => list?.filter((item) => item.id !== current.id));
          setEditing(undefined);
        }}
      />
    );
  }

  return (
    <section aria-labelledby="h-groups" className="settings-body">
      <div className="members-head">
        <div>
          <h2 id="h-groups">群組</h2>
          <p className="lead">
            決定每位成員可以使用哪些
            agent、工具與知識庫。每位成員都屬於「所有人」，
            加入其他群組後，能用的是這些群組加起來；管理者不受限制。模型、額度與限流由模型中台（LiteLLM）管理。
          </p>
        </div>
        <button
          type="button"
          className="btn btn-accent"
          disabled={!groups}
          onClick={() => setAdding(true)}
        >
          <UsersIcon size={15} />
          新增群組
        </button>
      </div>

      {loadError && (
        <div className="box box-row" role="alert">
          <span className="inline-error">讀不到群組：{loadError}</span>
          <button type="button" className="btn btn-outline" onClick={load}>
            重試
          </button>
        </div>
      )}
      {!groups && !loadError && (
        <p className="subtle" aria-busy="true">
          讀取中…
        </p>
      )}
      {groups && catalog && (
        <ul className="member-list" aria-label="群組">
          {groups.map((group) => (
            <li key={group.id} className="member-row group-row">
              <button
                type="button"
                className="group-open"
                onClick={() => setEditing(group.id)}
              >
                <span className="member-main">
                  <span className="member-name">
                    <span className="member-display">{group.name}</span>
                    {group.builtin && (
                      <span className="badge badge-muted">內建</span>
                    )}
                  </span>
                  <span className="member-meta">
                    {group.builtin
                      ? "每位成員"
                      : `${group.members.length} 位成員`}
                    {" · "}
                    {accessSummary(group, catalog)}
                  </span>
                  {group.description && (
                    <span className="member-meta">{group.description}</span>
                  )}
                </span>
                <ChevronIcon size={16} className="group-chevron" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {adding &&
        createPortal(
          <AddGroupDialog
            onClose={() => setAdding(false)}
            onAdded={(group) => {
              setGroups((list) => [...(list ?? []), group]);
              setAdding(false);
              setEditing(group.id);
            }}
          />,
          document.body,
        )}
    </section>
  );
}

function AddGroupDialog({
  onClose,
  onAdded,
}: {
  onClose: () => void;
  onAdded: (group: AccessGroup) => void;
}) {
  const titleId = useId();
  const nameRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim()) {
      setError("請輸入群組名稱");
      nameRef.current?.focus();
      return;
    }
    setBusy(true);
    try {
      onAdded(await api.createGroup({ name: name.trim() }));
    } catch (failure) {
      setError((failure as Error).message);
      setBusy(false);
    }
  };

  return (
    <Modal
      onClose={onClose}
      className="confirm-dialog form-dialog"
      labelledBy={titleId}
      initialFocus={nameRef}
    >
      <form onSubmit={submit} noValidate>
        <h2 id={titleId} className="dialog-title">
          新增群組
        </h2>
        <div className="dialog-fields">
          <Field
            ref={nameRef}
            label="群組名稱"
            autoComplete="off"
            placeholder="例如：資訊處、一般行員"
            value={name}
            error={error}
            hint={
              error ? undefined : "建立後再勾選這個群組可以使用的資源與成員。"
            }
            onChange={(event) => {
              setName(event.target.value);
              setError(undefined);
            }}
          />
        </div>
        <div className="dialog-actions">
          <button type="button" className="btn btn-outline" onClick={onClose}>
            取消
          </button>
          <button type="submit" className="btn btn-accent" disabled={busy}>
            建立
          </button>
        </div>
      </form>
    </Modal>
  );
}

function GroupEditor({
  group,
  catalog,
  members,
  onBack,
  onSaved,
  onDeleted,
}: {
  group: AccessGroup;
  catalog: GroupCatalog;
  members: MemberInfo[];
  onBack: () => void;
  onSaved: (group: AccessGroup) => void;
  onDeleted: () => void;
}) {
  const [draft, setDraft] = useState(group);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();
  const [deleting, setDeleting] = useState(false);
  const dirty = groupDirty(group, draft);

  const edit = (changes: Partial<AccessGroup>) => {
    setDraft((current) => ({ ...current, ...changes }));
    setError(undefined);
  };

  const save = async () => {
    if (!draft.name.trim()) {
      setError("請輸入群組名稱");
      return;
    }
    setSaving(true);
    const changes: GroupChanges = {
      name: draft.name.trim(),
      description: draft.description.trim(),
      agents: draft.agents,
      toolsets: draft.toolsets,
      knowledgeBases: draft.knowledgeBases,
      knowledge: draft.knowledge,
    };
    if (!group.builtin) changes.members = draft.members;
    try {
      const saved = await api.updateGroup(group.id, changes);
      onSaved(saved);
      setDraft(saved);
      showToast("已儲存群組");
    } catch (failure) {
      setError((failure as Error).message);
    }
    setSaving(false);
  };

  const options: Record<
    AccessKind,
    { name: string; label: string; hint?: string }[]
  > = {
    agents: catalog.agents.map((item) => ({
      name: item.name,
      label: item.name,
      hint: item.description,
    })),
    toolsets: catalog.toolsets.map((item) => ({
      name: item.name,
      label: item.name,
      hint: item.tools.join("、"),
    })),
    knowledgeBases: catalog.knowledgeBases.map((item) => ({
      name: item.id,
      label: item.name,
      hint: item.description || undefined,
    })),
    knowledge: catalog.knowledge.map((name) => ({ name, label: name })),
  };

  return (
    <section aria-labelledby="h-group" className="settings-body">
      <div>
        <button
          type="button"
          className="btn btn-ghost group-back"
          onClick={onBack}
        >
          <ArrowLeftIcon size={15} />
          所有群組
        </button>
        <h2 id="h-group">{group.name}</h2>
        <p className="lead">
          {group.builtin
            ? "每位成員都自動屬於這個群組。這裡開放的資源，所有成員都能用。"
            : "勾選這個群組可以使用的資源，再把成員加進來。"}
        </p>
      </div>

      <div className="dialog-fields">
        <Field
          label="群組名稱"
          value={draft.name}
          disabled={group.builtin}
          hint={group.builtin ? "內建群組不能改名稱" : undefined}
          onChange={(event) => edit({ name: event.target.value })}
        />
        <Field
          label="說明"
          value={draft.description}
          placeholder="選填，例如：可以執行程式與讀取人資規章"
          onChange={(event) => edit({ description: event.target.value })}
        />
      </div>

      {ACCESS_KINDS.filter(
        (kind) => !isKnowledgeKind(kind) || catalog.hasKnowledge,
      ).map((kind) => (
        <AccessField
          key={kind}
          kind={kind}
          value={draft[kind]}
          options={options[kind]}
          empty={emptyText(kind, catalog)}
          onChange={(value) => edit({ [kind]: value })}
        />
      ))}

      {!group.builtin && (
        <MemberPicker
          members={members}
          value={draft.members}
          onChange={(value) => edit({ members: value })}
        />
      )}

      {!group.builtin && (
        <div className="group-danger">
          <button
            type="button"
            className="btn btn-outline"
            onClick={() => setDeleting(true)}
          >
            <TrashIcon size={15} />
            刪除群組
          </button>
        </div>
      )}

      {dirty && (
        <section className="save-bar" aria-label="未儲存的變更">
          <span>{error ?? "群組有未儲存的變更"}</span>
          <div style={{ display: "flex", gap: 6 }}>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => {
                setDraft(group);
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

      {deleting &&
        createPortal(
          <ConfirmDialog
            title={`刪除「${group.name}」？`}
            confirmLabel="刪除群組"
            danger
            onConfirm={async () => {
              await api.deleteGroup(group.id);
              onDeleted();
            }}
            onClose={() => setDeleting(false)}
          >
            群組裡的成員不會被刪除，只是不再擁有這個群組開放的資源。
          </ConfirmDialog>,
          document.body,
        )}
    </section>
  );
}

function emptyText(kind: AccessKind, catalog: GroupCatalog): string {
  if (kind === "knowledge") {
    return catalog.hasKnowledge
      ? "知識庫裡的文件都沒有標籤，所有人都搜尋得到。"
      : "這個專案沒有知識庫。";
  }
  if (kind === "knowledgeBases") return "這個專案沒有知識庫。";
  return `這個專案沒有${KIND_LABELS[kind]}。`;
}

/** 一類資源：全部，或勾選指定的項目。 */
function AccessField({
  kind,
  value,
  options,
  empty,
  onChange,
}: {
  kind: AccessKind;
  value: string[] | null;
  options: { name: string; label: string; hint?: string }[];
  empty: string;
  onChange: (value: string[] | null) => void;
}) {
  const all = value === null;
  const chosen = value ?? [];
  const toggle = (name: string) =>
    onChange(
      chosen.includes(name)
        ? chosen.filter((item) => item !== name)
        : [...chosen, name],
    );
  return (
    <fieldset className="model-choices access-field">
      <legend>{KIND_LABELS[kind]}</legend>
      <div className="segmented segmented-sm">
        <button type="button" aria-pressed={all} onClick={() => onChange(null)}>
          全部
        </button>
        <button
          type="button"
          aria-pressed={!all}
          onClick={() => onChange(all ? [] : chosen)}
        >
          指定
        </button>
      </div>
      <div className="help">
        {all
          ? `所有${KIND_LABELS[kind]}都能用，之後新增的也一樣。`
          : kind === "knowledge"
            ? "細部過濾：在開放的知識庫裡，只能搜尋勾選的標籤；沒有標籤的文件所有人都搜尋得到。"
            : chosen.length === 0
              ? `沒有勾選：這個群組不開放任何${KIND_LABELS[kind]}。`
              : `已勾選 ${chosen.length} 項。`}
      </div>
      {!all &&
        (options.length === 0 ? (
          <p className="subtle">{empty}</p>
        ) : (
          <ul className="model-choices-list">
            {options.map((item) => (
              <li key={item.name}>
                <label className="model-choice">
                  <input
                    type="checkbox"
                    checked={chosen.includes(item.name)}
                    onChange={() => toggle(item.name)}
                  />
                  <span className="model-choice-label">{item.label}</span>
                  {item.hint && (
                    <span className="model-choice-name">{item.hint}</span>
                  )}
                </label>
              </li>
            ))}
          </ul>
        ))}
    </fieldset>
  );
}

function MemberPicker({
  members,
  value,
  onChange,
}: {
  members: MemberInfo[];
  value: string[];
  onChange: (value: string[]) => void;
}) {
  const toggle = (id: string) =>
    onChange(
      value.includes(id) ? value.filter((item) => item !== id) : [...value, id],
    );
  return (
    <fieldset className="model-choices access-field">
      <legend>成員</legend>
      <div className="help">
        已加入 {value.length} 位。管理者不受群組限制，加入與否都能使用全部資源。
      </div>
      <ul className="model-choices-list">
        {members.map((member) => (
          <li key={member.id}>
            <label className="model-choice">
              <input
                type="checkbox"
                checked={value.includes(member.id)}
                onChange={() => toggle(member.id)}
              />
              <span className="model-choice-label">{member.displayName}</span>
              <span className="model-choice-name mono">
                {member.username}
                {member.role === "admin" ? " · 管理者" : ""}
                {member.disabled ? " · 停用" : ""}
              </span>
            </label>
          </li>
        ))}
      </ul>
    </fieldset>
  );
}
