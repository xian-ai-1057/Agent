/** 知識庫設定表單的純函式：草稿與要送出的修改。不碰 window，測試可以直接引用。 */

import type {
  KnowledgeAdminBase,
  KnowledgeSettings,
} from "../../../../src/web/protocol.ts";

/** 表單裡每一欄都是文字，維護者是 MemberInfo.id 的清單。 */
export interface SettingsDraft {
  name: string;
  description: string;
  embeddingModel: string;
  chunkSize: string;
  reranker: string;
  rerankCandidates: string;
  minSimilarity: string;
  minRerankScore: string;
  maintainers: string[];
}

const TEXT_KEYS = ["embeddingModel", "reranker"] as const;
const INT_KEYS = ["chunkSize", "rerankCandidates"] as const;
const FLOAT_KEYS = ["minSimilarity", "minRerankScore"] as const;

const LABELS: Record<string, string> = {
  chunkSize: "切片長度",
  rerankCandidates: "候選數",
  minSimilarity: "相似度門檻",
  minRerankScore: "Rerank 分數門檻",
};

export function toSettingsDraft(base: KnowledgeAdminBase): SettingsDraft {
  const own = base.settings;
  const text = (value: unknown) =>
    value === undefined || value === null ? "" : String(value);
  return {
    name: base.name,
    description: base.description,
    embeddingModel: text(own.embeddingModel),
    chunkSize: text(own.chunkSize),
    reranker: text(own.reranker),
    rerankCandidates: text(own.rerankCandidates),
    minSimilarity: text(own.minSimilarity),
    minRerankScore: text(own.minRerankScore),
    maintainers: base.maintainers ?? [],
  };
}

type Changes = KnowledgeSettings & { maintainers?: string[] };

/**
 * 和原本的設定比較，算出要送給 PATCH /api/knowledge/:id 的欄位。清空的
 * 檢索設定送 null（刪掉，沿用預設）；數字格式不對時回傳錯誤。
 */
export function settingsChanges(
  base: KnowledgeAdminBase,
  draft: SettingsDraft,
): { changes: Changes } | { error: string } {
  const initial = toSettingsDraft(base);
  const changes: Changes = {};
  if (draft.name.trim() !== initial.name) {
    if (!draft.name.trim()) return { error: "請輸入名稱" };
    changes.name = draft.name.trim();
  }
  if (draft.description.trim() !== initial.description) {
    changes.description = draft.description.trim();
  }
  for (const key of TEXT_KEYS) {
    const value = draft[key].trim();
    if (value !== initial[key]) changes[key] = value || null;
  }
  for (const key of [...INT_KEYS, ...FLOAT_KEYS]) {
    const value = draft[key].trim();
    if (value === initial[key]) continue;
    if (!value) {
      changes[key] = null;
      continue;
    }
    const number = Number(value);
    const integer = (INT_KEYS as readonly string[]).includes(key);
    if (!Number.isFinite(number) || (integer && !Number.isInteger(number))) {
      return {
        error: `${LABELS[key]}必須是${integer ? "整數" : "數字"}`,
      };
    }
    changes[key] = number;
  }
  if (!sameMembers(draft.maintainers, initial.maintainers)) {
    changes.maintainers = draft.maintainers;
  }
  return { changes };
}

/** 草稿和原本的不一樣嗎（維護者不管順序）。 */
export function draftDirty(
  base: KnowledgeAdminBase,
  draft: SettingsDraft,
): boolean {
  const initial = toSettingsDraft(base);
  return (
    !sameMembers(draft.maintainers, initial.maintainers) ||
    (Object.keys(initial) as (keyof SettingsDraft)[]).some(
      (key) => key !== "maintainers" && draft[key] !== initial[key],
    )
  );
}

function sameMembers(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const set = new Set(a);
  return b.every((item) => set.has(item));
}
