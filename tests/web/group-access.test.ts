/** 管理平台 → 群組的純函式：摘要文字與草稿比較。 */

import { describe, expect, it } from "vitest";
import type { AccessGroup, GroupCatalog } from "../../src/web/protocol.ts";
import {
  accessSummary,
  groupDirty,
  kindSummary,
} from "../../web/src/components/admin/groupAccess.ts";

const CATALOG: GroupCatalog = {
  agents: [
    { name: "main", description: "" },
    { name: "writer", description: "" },
  ],
  toolsets: [
    { name: "files", tools: ["read_file"] },
    { name: "shell", tools: ["run_command"] },
  ],
  knowledgeBases: [
    { id: "default", name: "預設知識庫", description: "" },
    { id: "hr", name: "人資規章", description: "" },
  ],
  knowledge: ["hr"],
  hasKnowledge: true,
};

const GROUP: AccessGroup = {
  id: "g1",
  name: "資訊處",
  description: "",
  builtin: false,
  members: ["u1", "u2"],
  agents: ["main"],
  toolsets: [],
  knowledgeBases: ["hr"],
  knowledge: ["hr"],
};

describe("kindSummary", () => {
  it("全部、無、或勾選數 / 總數", () => {
    expect(kindSummary(null, 3)).toBe("全部");
    expect(kindSummary([], 3)).toBe("無");
    expect(kindSummary(["x"], 3)).toBe("1 / 3");
  });
});

describe("accessSummary", () => {
  it("列出每一類資源（模型由模型中台管，不在群組裡）", () => {
    expect(accessSummary(GROUP, CATALOG)).toBe(
      "Agent 1 / 2 · 工具組 無 · 知識庫 1 / 2 · 知識庫標籤 1 / 1",
    );
  });

  it("沒有知識庫時不列知識庫", () => {
    expect(
      accessSummary(GROUP, { ...CATALOG, hasKnowledge: false }),
    ).not.toContain("知識庫");
  });
});

describe("groupDirty", () => {
  it("清單順序不同不算改過", () => {
    expect(groupDirty(GROUP, { ...GROUP, members: ["u2", "u1"] })).toBe(false);
  });

  it("全部與空清單不一樣", () => {
    expect(groupDirty(GROUP, { ...GROUP, toolsets: null })).toBe(true);
    expect(groupDirty(GROUP, { ...GROUP, agents: null })).toBe(true);
  });

  it("名稱、說明、成員改了都算", () => {
    expect(groupDirty(GROUP, { ...GROUP, name: "x" })).toBe(true);
    expect(groupDirty(GROUP, { ...GROUP, description: "x" })).toBe(true);
    expect(groupDirty(GROUP, { ...GROUP, members: ["u1"] })).toBe(true);
  });
});
