/**
 * 稽核紀錄頁的純函式：查詢字串、日期範圍與每一筆的說明文字。
 * 不碰 window，測試可以直接引用。
 */

import type { AuditEntry, AuditFilters } from "../../../../src/web/protocol.ts";
import { ROLE_LABELS } from "../../auth.ts";
import { MODE_LABELS } from "../../format.ts";

const DAY = 24 * 60 * 60 * 1000;

/** 工具核准的決定。 */
export const DECISION_LABELS: Record<string, string> = {
  once: "允許一次",
  session: "這段對話都允許",
  always: "以後都允許",
  deny: "拒絕",
};

const FAILURE_LABELS: Record<string, string> = {
  wrong_password_or_disabled: "密碼不對，或帳號已停用",
  unknown_username: "沒有這個帳號",
  throttled: "失敗太多次，暫時不能登入",
};

const FIELD_LABELS: Record<string, string> = {
  name: "名稱",
  description: "說明",
  members: "成員",
  agents: "Agent",
  toolsets: "工具組",
  knowledge: "知識庫標籤",
  displayName: "顯示名稱",
  role: "角色",
  disabled: "狀態",
};

/** 頁面上的篩選欄位（日期是 <input type="date"> 的 YYYY-MM-DD）。 */
export interface AuditForm {
  category: string;
  actor: string;
  from: string;
  to: string;
}

export const EMPTY_FORM: AuditForm = {
  category: "",
  actor: "",
  from: "",
  to: "",
};

/**
 * 表單轉成 API 的條件。日期以瀏覽器所在時區計算，「到」包含當天整天。
 * 日期格式不對時略過那一個條件。
 */
export function toFilters(form: AuditForm): AuditFilters {
  const filters: AuditFilters = {};
  if (form.category) filters.category = form.category;
  if (form.actor.trim()) filters.actor = form.actor.trim();
  const since = localDay(form.from);
  const until = localDay(form.to);
  if (since !== undefined) filters.since = since;
  if (until !== undefined) filters.until = until + DAY;
  return filters;
}

/** 查詢字串（不含開頭的 ?），沒有值的條件不放。 */
export function auditQuery(
  filters: AuditFilters,
  extra: Record<string, string | number | undefined> = {},
): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries({ ...filters, ...extra })) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  return params.toString();
}

/** YYYY-MM-DD → 當地那天 0 點的毫秒；格式不對時 undefined。 */
function localDay(value: string): number | undefined {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return undefined;
  const [, year, month, day] = match;
  const time = new Date(Number(year), Number(month) - 1, Number(day)).getTime();
  return Number.isNaN(time) ? undefined : time;
}

/** 紀錄的時間，例如「2026/11/14 13:05:09」。 */
export function formatTime(ms: number): string {
  const date = new Date(ms);
  const pad = (value: number) => String(value).padStart(2, "0");
  return (
    `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  );
}

/** 一筆紀錄的補充說明（改了什麼、核准的決定…），沒有時是空字串。 */
export function describeDetail(entry: AuditEntry): string {
  const detail = entry.detail as Record<string, unknown>;
  switch (entry.action) {
    case "auth.login_failed":
      return FAILURE_LABELS[String(detail.reason)] ?? "";
    case "member.create":
      return `角色：${roleLabel(detail.role)}`;
    case "member.update":
      return describeChanges(detail.changes);
    case "group.update":
      return `修改了：${fieldList(detail.changes)}`;
    case "approval.decide": {
      const decision =
        DECISION_LABELS[String(detail.decision)] ?? String(detail.decision);
      const args = typeof detail.arguments === "string" ? detail.arguments : "";
      return args ? `${decision} · ${args}` : decision;
    }
    case "settings.permissions": {
      const mode = detail.mode as keyof typeof MODE_LABELS;
      return `模式：${MODE_LABELS[mode] ?? String(detail.mode)}`;
    }
    case "settings.model": {
      const parts = [`預設模型：${detail.model ?? "（沿用預設值）"}`];
      if (detail.maxTurns != null) {
        parts.push(`每次最多回合：${detail.maxTurns}`);
      }
      return parts.join(" · ");
    }
    case "settings.connection": {
      const parts = [String(detail.provider ?? ""), String(detail.model ?? "")];
      if (detail.apiKeyChanged) parts.push("換了金鑰");
      return parts.filter(Boolean).join(" · ");
    }
    case "settings.models":
      return Array.isArray(detail.models) ? detail.models.join("、") : "";
    case "audit.export":
      return `格式：${String(detail.format ?? "").toUpperCase()}`;
    default:
      return "";
  }
}

function roleLabel(role: unknown): string {
  return ROLE_LABELS[role as keyof typeof ROLE_LABELS] ?? String(role);
}

function fieldList(changes: unknown): string {
  if (!changes || typeof changes !== "object") return "";
  return Object.keys(changes)
    .filter((key) => key in FIELD_LABELS)
    .map((key) => FIELD_LABELS[key])
    .join("、");
}

/** {欄位: [改之前, 改之後]} → 「角色：成員 → 管理者；狀態：啟用 → 停用」。 */
function describeChanges(changes: unknown): string {
  if (!changes || typeof changes !== "object") return "";
  return Object.entries(changes as Record<string, [unknown, unknown]>)
    .map(([key, [before, after]]) => {
      const show = (value: unknown) => {
        if (key === "role") return roleLabel(value);
        if (key === "disabled") return value ? "停用" : "啟用";
        return String(value);
      };
      return `${FIELD_LABELS[key] ?? key}：${show(before)} → ${show(after)}`;
    })
    .join("；");
}
