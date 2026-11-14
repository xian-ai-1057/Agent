/** 知識庫的純函式：對話裡勾選知識庫、管理平台的設定表單。 */

import { describe, expect, it } from "vitest";
import type {
  KnowledgeAdminBase,
  KnowledgeBaseInfo,
  SessionOptions,
} from "../../src/web/protocol.ts";
import {
  draftDirty,
  settingsChanges,
  toSettingsDraft,
} from "../../web/src/components/admin/knowledgeForm.ts";
import {
  agentKnowledge,
  chosenKnowledge,
  toggleKnowledge,
} from "../../web/src/components/chat/options.ts";

const BASES: KnowledgeBaseInfo[] = [
  { id: "default", name: "預設知識庫", description: "" },
  { id: "hr", name: "人資規章", description: "" },
  { id: "it", name: "資訊手冊", description: "" },
];

const options = (knowledge?: string[] | null): SessionOptions => ({
  model: null,
  tools: null,
  skills: [],
  knowledge,
});

describe("對話裡勾選知識庫", () => {
  it("沒有另外勾選時跟著 agent 的預設", () => {
    expect(chosenKnowledge(options(), ["default"])).toEqual(["default"]);
    expect(chosenKnowledge(options(["it"]), ["default"])).toEqual(["it"]);
  });

  it("勾回 agent 的預設時存成 null，其他照知識庫的順序", () => {
    expect(toggleKnowledge(options(), ["default"], BASES, "it")).toEqual([
      "default",
      "it",
    ]);
    expect(
      toggleKnowledge(options(["it", "default"]), ["default"], BASES, "it"),
    ).toBeNull();
    expect(toggleKnowledge(options(), ["default"], BASES, "default")).toEqual(
      [],
    );
  });

  it("agent 的預設只留這個人可以用的", () => {
    const project = { knowledgeBases: BASES.slice(0, 2) };
    expect(
      agentKnowledge(project as never, { knowledge: ["hr", "it"] } as never),
    ).toEqual(["hr"]);
    expect(agentKnowledge(project as never, undefined)).toEqual([]);
  });
});

const BASE: KnowledgeAdminBase = {
  id: "hr",
  name: "人資規章",
  description: "",
  builtin: false,
  path: "knowledge/hr",
  documents: 2,
  settings: { name: "人資規章", chunkSize: 500 },
  retrieval: {
    embeddingModel: null,
    chunkSize: 500,
    reranker: null,
    rerankCandidates: 20,
    minSimilarity: null,
    minRerankScore: null,
  },
  agents: [],
  maintainers: ["u1"],
};

describe("知識庫設定表單", () => {
  it("沒改時不算改過，也沒有要送出的欄位", () => {
    const draft = toSettingsDraft(BASE);
    expect(draftDirty(BASE, draft)).toBe(false);
    expect(settingsChanges(BASE, draft)).toEqual({ changes: {} });
  });

  it("清空的設定送 null，數字轉成數字", () => {
    const draft = {
      ...toSettingsDraft(BASE),
      chunkSize: "",
      minSimilarity: "0.3",
      reranker: " cohere/rerank-v3.5 ",
      maintainers: ["u2"],
    };
    expect(settingsChanges(BASE, draft)).toEqual({
      changes: {
        chunkSize: null,
        minSimilarity: 0.3,
        reranker: "cohere/rerank-v3.5",
        maintainers: ["u2"],
      },
    });
  });

  it("名稱不能空白，整數欄位不能有小數", () => {
    expect(
      settingsChanges(BASE, { ...toSettingsDraft(BASE), name: " " }),
    ).toEqual({ error: "請輸入名稱" });
    expect(
      settingsChanges(BASE, { ...toSettingsDraft(BASE), chunkSize: "1.5" }),
    ).toEqual({ error: "切片長度必須是整數" });
  });

  it("維護者順序不同不算改過", () => {
    const base = { ...BASE, maintainers: ["u1", "u2"] };
    expect(
      draftDirty(base, { ...toSettingsDraft(base), maintainers: ["u2", "u1"] }),
    ).toBe(false);
  });
});
