/**
 * 管理平台 → 知識庫：每個知識庫有自己的名稱、說明、資料夾與檢索設定。
 *
 * - 管理者：新增或拿掉知識庫、修改設定、指定維護者，也能上傳文件與試搜尋。
 * - 維護者（帳號模式的成員）：只看得到自己維護的知識庫，可以上傳、更新、
 *   刪除文件與試搜尋，不能改設定。
 *
 * 哪些人能在對話裡搜尋哪個知識庫，由「群組」決定。
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
  KnowledgeAdminBase,
  KnowledgeAdminList,
  KnowledgeDocuments,
  KnowledgeHit,
  MemberInfo,
} from "../../../../src/web/protocol.ts";
import { api } from "../../api.ts";
import { formatBytes } from "../../format.ts";
import {
  AddIcon,
  ArrowLeftIcon,
  BookIcon,
  ChevronIcon,
  RetryIcon,
  SearchIcon,
  TrashIcon,
} from "../Icons.tsx";
import { ConfirmDialog, Modal } from "../shell/Dialog.tsx";
import { Field } from "../shell/Field.tsx";
import { showToast } from "../shell/toast.tsx";
import {
  draftDirty,
  type SettingsDraft,
  settingsChanges,
  toSettingsDraft,
} from "./knowledgeForm.ts";

export function Knowledge() {
  const [list, setList] = useState<KnowledgeAdminList>();
  const [members, setMembers] = useState<MemberInfo[]>([]);
  const [loadError, setLoadError] = useState<string>();
  const [editing, setEditing] = useState<string>();
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    setLoadError(undefined);
    try {
      const found = await api.knowledge();
      setList(found);
      if (found.canCreate && found.maintainers) {
        setMembers(await api.members());
      }
    } catch (error) {
      setLoadError((error as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const current = list?.bases.find((item) => item.id === editing);
  if (current && list) {
    return (
      <KnowledgeEditor
        key={current.id}
        base={current}
        list={list}
        members={members}
        onBack={() => setEditing(undefined)}
        onChanged={load}
        onDeleted={() => {
          setEditing(undefined);
          void load();
        }}
      />
    );
  }

  return (
    <section aria-labelledby="h-knowledge" className="settings-body">
      <div className="members-head">
        <div>
          <h2 id="h-knowledge">知識庫</h2>
          <p className="lead">
            {list && !list.canCreate
              ? "你是這些知識庫的維護者，可以上傳、更新、刪除文件並試搜尋。"
              : "依主題或部門分成好幾個知識庫，各自上傳文件、調整檢索設定。哪些人可以在對話裡搜尋哪個知識庫，到「群組」設定。"}
          </p>
        </div>
        {list?.canCreate && (
          <button
            type="button"
            className="btn btn-accent"
            onClick={() => setAdding(true)}
          >
            <AddIcon size={15} />
            新增知識庫
          </button>
        )}
      </div>

      {loadError && (
        <div className="box box-row" role="alert">
          <span className="inline-error">讀不到知識庫：{loadError}</span>
          <button type="button" className="btn btn-outline" onClick={load}>
            重試
          </button>
        </div>
      )}
      {!list && !loadError && (
        <p className="subtle" aria-busy="true">
          讀取中…
        </p>
      )}
      {list && list.bases.length === 0 && (
        <div className="box">
          <p className="subtle" style={{ margin: 0 }}>
            還沒有知識庫。
            {list.canCreate
              ? "按「新增知識庫」建立第一個，或在專案裡建立 knowledge/ 資料夾。"
              : ""}
          </p>
        </div>
      )}
      {list && list.bases.length > 0 && (
        <ul className="member-list" aria-label="知識庫">
          {list.bases.map((base) => (
            <li key={base.id} className="member-row group-row">
              <button
                type="button"
                className="group-open"
                onClick={() => setEditing(base.id)}
              >
                <span className="kb-icon" aria-hidden="true">
                  <BookIcon size={16} />
                </span>
                <span className="member-main">
                  <span className="member-name">
                    <span className="member-display">{base.name}</span>
                    {base.builtin && (
                      <span className="badge badge-muted">預設</span>
                    )}
                  </span>
                  <span className="member-meta">
                    {base.documents} 份文件 ·{" "}
                    <span className="mono">{base.path}/</span>
                    {base.agents.length > 0 &&
                      ` · 預設給 ${base.agents.join("、")}`}
                  </span>
                  {base.description && (
                    <span className="member-meta">{base.description}</span>
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
          <AddKnowledgeDialog
            onClose={() => setAdding(false)}
            onAdded={async (id) => {
              setAdding(false);
              await load();
              setEditing(id);
            }}
          />,
          document.body,
        )}
    </section>
  );
}

function AddKnowledgeDialog({
  onClose,
  onAdded,
}: {
  onClose: () => void;
  onAdded: (id: string) => void;
}) {
  const titleId = useId();
  const nameRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");
  const [id, setId] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<{ name?: string; id?: string }>({});
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const errors: typeof error = {};
    if (!name.trim()) errors.name = "請輸入名稱";
    if (!/^[a-z0-9][a-z0-9_-]{0,39}$/.test(id.trim())) {
      errors.id = "只能用小寫英文、數字、- 與 _，例如 hr、it-manual";
    }
    setError(errors);
    if (errors.name || errors.id) return;
    setBusy(true);
    try {
      const created = await api.createKnowledge({
        id: id.trim(),
        name: name.trim(),
        description: description.trim(),
      });
      onAdded(created.id);
    } catch (failure) {
      setError({ id: (failure as Error).message });
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
          新增知識庫
        </h2>
        <div className="dialog-fields">
          <Field
            ref={nameRef}
            label="名稱"
            autoComplete="off"
            placeholder="例如：人資規章"
            value={name}
            error={error.name}
            onChange={(event) => {
              setName(event.target.value);
              setError({});
            }}
          />
          <Field
            label="識別碼"
            autoComplete="off"
            placeholder="例如：hr"
            className="mono"
            value={id}
            error={error.id}
            hint={
              error.id
                ? undefined
                : `文件會放在 knowledge/${id.trim() || "<識別碼>"}/，建立後不能改。`
            }
            onChange={(event) => {
              setId(event.target.value.toLowerCase());
              setError({});
            }}
          />
          <Field
            label="說明"
            autoComplete="off"
            placeholder="選填：裡面有什麼文件，agent 也會看到這段說明"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
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

function KnowledgeEditor({
  base,
  list,
  members,
  onBack,
  onChanged,
  onDeleted,
}: {
  base: KnowledgeAdminBase;
  list: KnowledgeAdminList;
  members: MemberInfo[];
  onBack: () => void;
  onChanged: () => Promise<void>;
  onDeleted: () => void;
}) {
  const admin = list.canCreate;
  const [deleting, setDeleting] = useState(false);
  return (
    <section aria-labelledby="h-kb" className="settings-body">
      <div>
        <button
          type="button"
          className="btn btn-ghost group-back"
          onClick={onBack}
        >
          <ArrowLeftIcon size={15} />
          所有知識庫
        </button>
        <h2 id="h-kb">{base.name}</h2>
        <p className="lead">
          {base.description || "沒有說明。"}
          <span className="subtle mono"> · {base.path}/</span>
        </p>
      </div>

      <Documents base={base} formats={list.formats} onChanged={onChanged} />
      <SearchTest base={base} />
      {admin && (
        <SettingsForm
          base={base}
          members={members}
          maintainers={list.maintainers}
          onSaved={onChanged}
        />
      )}

      {admin && !base.builtin && (
        <div className="group-danger">
          <button
            type="button"
            className="btn btn-outline"
            onClick={() => setDeleting(true)}
          >
            <TrashIcon size={15} />
            拿掉知識庫
          </button>
        </div>
      )}
      {deleting &&
        createPortal(
          <ConfirmDialog
            title={`拿掉「${base.name}」？`}
            confirmLabel="拿掉知識庫"
            danger
            disabled={base.agents.length > 0}
            onConfirm={async () => {
              await api.deleteKnowledge(base.id);
              showToast("已拿掉知識庫");
              onDeleted();
            }}
            onClose={() => setDeleting(false)}
          >
            {base.agents.length > 0
              ? `agent ${base.agents.join("、")} 預設會搜尋這個知識庫，請先修改它的 knowledge 設定。`
              : `從 harness.yaml 拿掉這個知識庫，對話就搜尋不到它。文件不會刪除，${base.path}/ 資料夾會留著。`}
          </ConfirmDialog>,
          document.body,
        )}
    </section>
  );
}

function Documents({
  base,
  formats,
  onChanged,
}: {
  base: KnowledgeAdminBase;
  formats: string[];
  onChanged: () => Promise<void>;
}) {
  const [data, setData] = useState<KnowledgeDocuments>();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<string>();
  const input = useRef<HTMLInputElement>(null);
  const replaceInput = useRef<HTMLInputElement>(null);
  const replacing = useRef<string>(undefined);

  const load = useCallback(async () => {
    setError(undefined);
    try {
      setData(await api.knowledgeDocuments(base.id));
    } catch (failure) {
      setError((failure as Error).message);
    }
  }, [base.id]);

  useEffect(() => {
    void load();
  }, [load]);

  const upload = async (files: File[], replace?: string) => {
    if (files.length === 0) return;
    setBusy(true);
    setError(undefined);
    const failed: string[] = [];
    for (const file of files) {
      try {
        await api.uploadKnowledge(base.id, file, { replace });
      } catch (failure) {
        failed.push(`${file.name}：${(failure as Error).message}`);
      }
    }
    if (failed.length > 0) setError(failed.join("\n"));
    else showToast(replace ? "已更新文件" : `已上傳 ${files.length} 份文件`);
    await load();
    await onChanged();
    setBusy(false);
  };

  const index = data?.index;
  return (
    <section className="access-field kb-section" aria-labelledby="h-kb-docs">
      <div className="box-row">
        <h3 id="h-kb-docs" className="kb-heading">
          文件
        </h3>
        <div style={{ display: "flex", gap: 6 }}>
          <button
            type="button"
            className="btn btn-ghost"
            aria-label="重新讀取"
            onClick={load}
          >
            <RetryIcon size={15} />
          </button>
          <button
            type="button"
            className="btn btn-accent"
            disabled={busy}
            onClick={() => input.current?.click()}
          >
            <AddIcon size={15} />
            {busy ? "上傳中…" : "上傳文件"}
          </button>
        </div>
      </div>
      <input
        ref={input}
        type="file"
        multiple
        hidden
        accept={formats.join(",")}
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          event.target.value = "";
          void upload(files);
        }}
      />
      <input
        ref={replaceInput}
        type="file"
        hidden
        accept={formats.join(",")}
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []).slice(0, 1);
          event.target.value = "";
          void upload(files, replacing.current);
        }}
      />
      <p className="help">
        支援 {formats.join("、")}。上傳後下次搜尋會自動更新索引。
      </p>
      {index && "error" in index && (
        <div className="box" role="alert">
          <span className="inline-error">建立索引失敗：{index.error}</span>
        </div>
      )}
      {index && !("error" in index) && (
        <p className="member-meta">
          {index.chunks} 個片段 · 檢索方式：
          {index.embedding ? "關鍵字 + 語意（embedding）" : "關鍵字"}
          {index.reranker ? "，再用 reranker 重新排序" : ""}
        </p>
      )}
      {index && !("error" in index) && index.warnings.length > 0 && (
        <ul className="kb-warnings">
          {index.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
      {error && (
        <div className="inline-error kb-error" role="alert">
          {error}
        </div>
      )}
      {!data && !error && (
        <p className="subtle" aria-busy="true">
          讀取中…
        </p>
      )}
      {data && data.documents.length === 0 && (
        <p className="subtle">還沒有文件。</p>
      )}
      {data && data.documents.length > 0 && (
        <ul className="member-list" aria-label="文件">
          {data.documents.map((doc) => (
            <li key={doc.path} className="member-row">
              <span className="member-main">
                <span className="member-name">
                  <span className="member-display mono kb-path">
                    {doc.path}
                  </span>
                  {!doc.supported && (
                    <span className="badge badge-muted">不支援的格式</span>
                  )}
                  {doc.labels.map((label) => (
                    <span key={label} className="badge badge-muted">
                      {label}
                    </span>
                  ))}
                </span>
                <span className="member-meta">
                  {formatBytes(doc.size)} · {doc.chunks} 個片段 ·{" "}
                  {new Date(doc.modifiedAt * 1000).toLocaleString()}
                </span>
              </span>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={busy}
                onClick={() => {
                  replacing.current = doc.path;
                  replaceInput.current?.click();
                }}
              >
                更新
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                aria-label={`刪除 ${doc.path}`}
                disabled={busy}
                onClick={() => setRemoving(doc.path)}
              >
                <TrashIcon size={15} />
              </button>
            </li>
          ))}
        </ul>
      )}
      {removing &&
        createPortal(
          <ConfirmDialog
            title={`刪除「${removing}」？`}
            confirmLabel="刪除文件"
            danger
            onConfirm={async () => {
              await api.deleteKnowledgeDocument(base.id, removing);
              showToast("已刪除文件");
              await load();
              await onChanged();
            }}
            onClose={() => setRemoving(undefined)}
          >
            檔案會從知識庫的資料夾刪除，之後的對話搜尋不到它。
          </ConfirmDialog>,
          document.body,
        )}
    </section>
  );
}

function SearchTest({ base }: { base: KnowledgeAdminBase }) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<KnowledgeHit[]>();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  const search = async (event: FormEvent) => {
    event.preventDefault();
    if (!query.trim()) return;
    setBusy(true);
    setError(undefined);
    try {
      setHits((await api.searchKnowledge(base.id, query.trim())).hits);
    } catch (failure) {
      setError((failure as Error).message);
    }
    setBusy(false);
  };

  return (
    <section className="access-field kb-section" aria-labelledby="h-kb-try">
      <h3 id="h-kb-try" className="kb-heading">
        搜尋測試
      </h3>
      <p className="help">
        用真實的問題試試看能不能找到對的段落；分數可以拿來調整下面的門檻。
      </p>
      <form className="kb-search" onSubmit={search}>
        <input
          className="field"
          type="search"
          aria-label="要搜尋的內容"
          placeholder="例如：特休怎麼計算"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <button type="submit" className="btn btn-outline" disabled={busy}>
          <SearchIcon size={15} />
          搜尋
        </button>
      </form>
      {error && (
        <div className="inline-error" role="alert">
          {error}
        </div>
      )}
      {hits && hits.length === 0 && (
        <p className="subtle">沒有找到相關的內容（或都低於門檻）。</p>
      )}
      {hits && hits.length > 0 && (
        <ol className="kb-hits">
          {hits.map((hit, index) => (
            <li
              key={`${hit.source}\n${hit.location}\n${hit.text}`}
              className="box"
            >
              <div className="member-meta">
                <span className="mono">
                  [{index + 1}] {hit.source}
                  {hit.location ? ` › ${hit.location}` : ""}
                </span>
                {hit.similarity !== null &&
                  ` · 相似度 ${hit.similarity.toFixed(3)}`}
                {hit.rerankScore !== null &&
                  ` · rerank ${hit.rerankScore.toFixed(3)}`}
              </div>
              <p className="kb-hit-text">{hit.text}</p>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function SettingsForm({
  base,
  members,
  maintainers,
  onSaved,
}: {
  base: KnowledgeAdminBase;
  members: MemberInfo[];
  maintainers: boolean;
  onSaved: () => Promise<void>;
}) {
  const initial = toSettingsDraft(base);
  const [draft, setDraft] = useState<SettingsDraft>(initial);
  const [error, setError] = useState<string>();
  const [saving, setSaving] = useState(false);
  const dirty = draftDirty(base, draft);

  const edit = (changes: Partial<SettingsDraft>) => {
    setDraft((current) => ({ ...current, ...changes }));
    setError(undefined);
  };

  const save = async () => {
    const result = settingsChanges(base, draft);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    setSaving(true);
    try {
      await api.updateKnowledge(base.id, result.changes);
      await onSaved();
      showToast("已儲存知識庫設定");
    } catch (failure) {
      setError((failure as Error).message);
    }
    setSaving(false);
  };

  const effective = base.retrieval;
  const placeholder = (value: string | number | null, fallback: string) =>
    value === null ? fallback : `目前：${value}`;
  const field = (
    key: keyof Omit<SettingsDraft, "maintainers">,
    label: string,
    hint: string,
    fallback = "沒有設定",
  ) => (
    <Field
      label={label}
      value={draft[key]}
      hint={hint}
      autoComplete="off"
      placeholder={
        key in effective
          ? placeholder(
              effective[key as keyof typeof effective] as
                | string
                | number
                | null,
              fallback,
            )
          : undefined
      }
      onChange={(event) => edit({ [key]: event.target.value })}
    />
  );

  const toggleMaintainer = (id: string) =>
    edit({
      maintainers: draft.maintainers.includes(id)
        ? draft.maintainers.filter((item) => item !== id)
        : [...draft.maintainers, id],
    });

  return (
    <section className="access-field kb-section" aria-labelledby="h-kb-set">
      <h3 id="h-kb-set" className="kb-heading">
        設定
      </h3>
      <p className="help">
        寫在 harness.yaml。檢索設定留空時沿用
        {base.builtin ? "預設值" : "預設知識庫（knowledge 區塊）的設定"}。
      </p>
      <div className="dialog-fields">
        {field("name", "名稱", "顯示在對話的知識庫選單與搜尋結果的出處。")}
        {field("description", "說明", "agent 會看到這段說明，判斷要不要搜尋。")}
        {field(
          "embeddingModel",
          "Embedding 模型",
          "設定後加上語意檢索；留空只用關鍵字。",
          "只用關鍵字",
        )}
        {field("chunkSize", "切片長度（字）", "每個片段的目標字數，至少 100。")}
        {field(
          "reranker",
          "Reranker",
          "重新排序用的模型，選填。",
          "不重新排序",
        )}
        {field("rerankCandidates", "交給 reranker 的候選數", "預設 20。")}
        {field(
          "minSimilarity",
          "相似度門檻",
          "-1 到 1，低於門檻的片段不回傳；需要 embedding 模型。",
          "不限制",
        )}
        {field(
          "minRerankScore",
          "Rerank 分數門檻",
          "低於門檻的片段不回傳；需要 reranker。",
          "不限制",
        )}
      </div>

      {maintainers && (
        <fieldset className="model-choices access-field">
          <legend>維護者</legend>
          <div className="help">
            維護者可以在管理平台上傳、更新、刪除這個知識庫的文件並試搜尋，
            不能改設定。管理者不必設定就能管理。
          </div>
          <ul className="model-choices-list">
            {members
              .filter((member) => member.role !== "admin")
              .map((member) => (
                <li key={member.id}>
                  <label className="model-choice">
                    <input
                      type="checkbox"
                      checked={draft.maintainers.includes(member.id)}
                      onChange={() => toggleMaintainer(member.id)}
                    />
                    <span className="model-choice-label">
                      {member.displayName}
                    </span>
                    <span className="model-choice-name mono">
                      {member.username}
                      {member.disabled ? " · 停用" : ""}
                    </span>
                  </label>
                </li>
              ))}
          </ul>
        </fieldset>
      )}

      {dirty && (
        <section className="save-bar" aria-label="未儲存的變更">
          <span>{error ?? "知識庫設定有未儲存的變更"}</span>
          <div style={{ display: "flex", gap: 6 }}>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => {
                setDraft(initial);
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
    </section>
  );
}
