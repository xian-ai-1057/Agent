/** 存取群組的純函式：顯示文字與草稿比較。不碰 window，測試可以直接引用。 */

import type {
  AccessGroup,
  AccessKind,
  GroupCatalog,
} from "../../../../src/web/protocol.ts";

export const ACCESS_KINDS: AccessKind[] = ["agents", "toolsets", "knowledge"];

export const KIND_LABELS: Record<AccessKind, string> = {
  agents: "Agent",
  toolsets: "工具組",
  knowledge: "知識庫標籤",
};

/** 一類資源的簡短說明：「全部」、「無」或「2 / 5」。 */
export function kindSummary(value: string[] | null, total: number): string {
  if (value === null) return "全部";
  if (value.length === 0) return "無";
  return `${value.length} / ${total}`;
}

/** 群組列表上的一行摘要，例如「Agent 1 / 2 · 工具組 無」。 */
export function accessSummary(
  group: AccessGroup,
  catalog: GroupCatalog,
): string {
  const totals: Record<AccessKind, number> = {
    agents: catalog.agents.length,
    toolsets: catalog.toolsets.length,
    knowledge: catalog.knowledge.length,
  };
  return ACCESS_KINDS.filter(
    (kind) => kind !== "knowledge" || catalog.hasKnowledge,
  )
    .map(
      (kind) =>
        `${KIND_LABELS[kind]} ${kindSummary(group[kind], totals[kind])}`,
    )
    .join(" · ");
}

function sameList(a: string[] | null, b: string[] | null): boolean {
  if (a === null || b === null) return a === b;
  if (a.length !== b.length) return false;
  const set = new Set(a);
  return b.every((item) => set.has(item));
}

/** 草稿和儲存的群組不一樣嗎（清單不管順序）。 */
export function groupDirty(saved: AccessGroup, draft: AccessGroup): boolean {
  return (
    saved.name !== draft.name ||
    saved.description !== draft.description ||
    !sameList(saved.members, draft.members) ||
    ACCESS_KINDS.some((kind) => !sameList(saved[kind], draft[kind]))
  );
}
