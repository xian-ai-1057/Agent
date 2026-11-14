/**
 * 管理平台 → 稽核紀錄：誰在什麼時候登入、改了成員、群組與設定，以及
 * 核准或拒絕了哪些工具呼叫。
 *
 * 紀錄只能新增，不能修改或刪除；每一筆串著前一筆的雜湊，「檢查完整性」
 * 從第一筆檢查到最後一筆，有人繞過系統直接改資料庫時會找出從哪一筆開始
 * 對不上。可以依分類、帳號與日期篩選，匯出 CSV（Excel 打得開）或 JSON
 * Lines（送進 SIEM），匯出這件事本身也會記一筆。
 */

import { type FormEvent, useCallback, useEffect, useState } from "react";
import type {
  AuditEntry,
  AuditFilters,
  AuditVerification,
} from "../../../../src/web/protocol.ts";
import { api, auditExportUrl } from "../../api.ts";
import { CheckIcon, SearchIcon, ShieldIcon, WarnIcon } from "../Icons.tsx";
import {
  type AuditForm,
  describeDetail,
  EMPTY_FORM,
  formatTime,
  toFilters,
} from "./auditFormat.ts";

export function Audit() {
  const [form, setForm] = useState<AuditForm>(EMPTY_FORM);
  const [filters, setFilters] = useState<AuditFilters>({});
  const [entries, setEntries] = useState<AuditEntry[]>();
  const [total, setTotal] = useState(0);
  const [categories, setCategories] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string>();
  const [verification, setVerification] = useState<AuditVerification>();
  const [verifying, setVerifying] = useState(false);
  const [verifyError, setVerifyError] = useState<string>();

  const load = useCallback(async (next: AuditFilters, before?: number) => {
    setLoading(true);
    setLoadError(undefined);
    try {
      const page = await api.audit(next, before);
      setEntries((list) =>
        before === undefined
          ? page.entries
          : [...(list ?? []), ...page.entries],
      );
      setTotal(page.total);
      setCategories(page.categories);
    } catch (error) {
      setLoadError((error as Error).message);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void load({});
  }, [load]);

  const search = (event: FormEvent) => {
    event.preventDefault();
    const next = toFilters(form);
    setFilters(next);
    void load(next);
  };

  const reset = () => {
    setForm(EMPTY_FORM);
    setFilters({});
    void load({});
  };

  const verify = async () => {
    setVerifying(true);
    setVerifyError(undefined);
    try {
      setVerification(await api.verifyAudit());
    } catch (error) {
      setVerifyError((error as Error).message);
    }
    setVerifying(false);
  };

  const last = entries?.at(-1);
  const more = entries !== undefined && entries.length < total;
  const filtered = Object.keys(filters).length > 0;

  return (
    <section aria-labelledby="h-audit" className="settings-body">
      <div className="members-head">
        <div>
          <h2 id="h-audit">稽核紀錄</h2>
          <p className="lead">
            登入、成員與群組的變更、權限與模型設定，以及工具核准都會記在這裡。紀錄只能新增，不能修改或刪除。模型的用量與花費由模型中台（LiteLLM）記錄。
          </p>
        </div>
        <div className="audit-exports">
          <a
            className="btn btn-outline"
            href={auditExportUrl(filters, "csv")}
            download
          >
            匯出 CSV
          </a>
          <a
            className="btn btn-ghost"
            href={auditExportUrl(filters, "jsonl")}
            download
          >
            JSON Lines
          </a>
        </div>
      </div>

      <div className="box box-row audit-verify">
        <span className="audit-verify-text">
          {verification ? (
            verification.ok ? (
              <span className="audit-ok">
                <CheckIcon size={15} className="audit-icon audit-icon-ok" />
                {verification.checked} 筆紀錄都完整，沒有被修改或刪除
              </span>
            ) : (
              <span className="inline-error" role="alert">
                <WarnIcon size={15} className="audit-icon" />
                從第 {verification.brokenAt}{" "}
                筆開始對不上，紀錄可能被直接修改過資料庫，請保留現場並通知資安人員
              </span>
            )
          ) : (
            <span className="subtle">
              <ShieldIcon size={15} className="audit-icon" />
              每一筆都串著前一筆的雜湊，可以檢查紀錄有沒有被竄改。
            </span>
          )}
          {verifyError && (
            <span className="inline-error" role="alert">
              {verifyError}
            </span>
          )}
        </span>
        <button
          type="button"
          className="btn btn-outline"
          disabled={verifying}
          onClick={verify}
        >
          {verifying ? "檢查中…" : "檢查完整性"}
        </button>
      </div>

      <form className="audit-filters" onSubmit={search} aria-label="篩選">
        <label className="form-row">
          <span className="label">分類</span>
          <select
            className="field"
            value={form.category}
            onChange={(event) =>
              setForm({ ...form, category: event.target.value })
            }
          >
            <option value="">全部</option>
            {Object.entries(categories).map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="form-row">
          <span className="label">帳號</span>
          <input
            className="field"
            placeholder="帳號名稱"
            autoComplete="off"
            value={form.actor}
            onChange={(event) =>
              setForm({ ...form, actor: event.target.value })
            }
          />
        </label>
        <label className="form-row">
          <span className="label">從</span>
          <input
            className="field"
            type="date"
            value={form.from}
            onChange={(event) => setForm({ ...form, from: event.target.value })}
          />
        </label>
        <label className="form-row">
          <span className="label">到</span>
          <input
            className="field"
            type="date"
            value={form.to}
            onChange={(event) => setForm({ ...form, to: event.target.value })}
          />
        </label>
        <div className="audit-filter-actions">
          <button type="submit" className="btn btn-accent" disabled={loading}>
            <SearchIcon size={15} />
            查詢
          </button>
          {filtered && (
            <button type="button" className="btn btn-ghost" onClick={reset}>
              清除
            </button>
          )}
        </div>
      </form>

      {loadError && (
        <div className="box box-row" role="alert">
          <span className="inline-error">讀不到稽核紀錄：{loadError}</span>
          <button
            type="button"
            className="btn btn-outline"
            onClick={() => load(filters)}
          >
            重試
          </button>
        </div>
      )}
      {entries === undefined && !loadError && (
        <p className="subtle" aria-busy="true">
          讀取中…
        </p>
      )}
      {entries !== undefined && (
        <div className="audit-results">
          <p className="subtle" aria-live="polite">
            {filtered ? "符合條件的" : "共"} {total} 筆
          </p>
          {entries.length === 0 ? (
            <p className="subtle">沒有符合條件的紀錄。</p>
          ) : (
            <ul className="member-list audit-list" aria-label="稽核紀錄">
              {entries.map((entry) => (
                <AuditRow key={entry.seq} entry={entry} />
              ))}
            </ul>
          )}
          {more && last && (
            <button
              type="button"
              className="btn btn-outline audit-more"
              disabled={loading}
              onClick={() => load(filters, last.seq)}
            >
              {loading ? "讀取中…" : "載入更多"}
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function AuditRow({ entry }: { entry: AuditEntry }) {
  const detail = describeDetail(entry);
  const failed = entry.action === "auth.login_failed";
  return (
    <li className="member-row audit-row">
      <span className="member-main">
        <span className="member-name">
          <span className="member-display">{entry.label}</span>
          {entry.target && (
            <span className="badge badge-muted audit-target">
              {entry.target}
            </span>
          )}
          {failed && <span className="badge badge-danger">失敗</span>}
        </span>
        {detail && <span className="audit-detail">{detail}</span>}
        <span className="member-meta">
          <time dateTime={new Date(entry.at).toISOString()}>
            {formatTime(entry.at)}
          </time>
          {" · "}
          {entry.actor.username || "（未登入）"}
          {entry.address && ` · ${entry.address}`}
          {` · #${entry.seq}`}
        </span>
      </span>
    </li>
  );
}
