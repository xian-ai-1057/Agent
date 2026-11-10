import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { loadProject, type Project, parseFrontmatter } from "../src/project.ts";
import { createProject } from "../src/scaffold.ts";
import { contextIn, MCP_FIXTURE, tempDir } from "./helpers.ts";

const opened: Project[] = [];
afterEach(async () => {
  await Promise.all(opened.splice(0).map((project) => project.close()));
});

/** 建立 TypeScript 範本專案（不需要啟動任何伺服器）。 */
function scaffold(): string {
  const root = join(tempDir(), "demo_project");
  createProject(root, "ts");
  return root;
}

async function load(root: string): Promise<Project> {
  const project = await loadProject(root);
  opened.push(project);
  return project;
}

function writeAgent(root: string, name: string, frontmatter: string): void {
  const text = `---\n${frontmatter}\n---\n你是助手\n`;
  writeFileSync(join(root, "agents", `${name}.md`), text);
}

function writeConfig(root: string, yaml: string): void {
  writeFileSync(join(root, "harness.yaml"), yaml);
}

describe("loadProject", () => {
  it("讀取範本專案", async () => {
    const project = await load(scaffold());

    expect(project.name).toBe("demo_project");
    expect(project.defaultAgent).toBe("main");
    expect(Object.keys(project.tools)).toEqual(["word_count"]);
  });

  it("載入 tools/ 底下 TypeScript 檔案匯出的工具並可執行", async () => {
    const project = await load(scaffold());

    const output = await project.tools.word_count?.execute(
      { text: "一\n二" },
      contextIn(project.root),
    );

    expect(output).toEqual({ characters: 3, lines: 2 });
  });

  it("把 AGENTS.md 接在 agent 的指示後面", async () => {
    const root = scaffold();
    writeFileSync(join(root, "AGENTS.md"), "共用規則");

    const agent = (await load(root)).agents.main;

    expect(agent?.instructions).toMatch(/^你是 demo_project 的助手/);
    expect(agent?.instructions).toMatch(/共用規則$/);
  });

  it("agent 沒寫 tools 欄位時可以使用專案所有的工具", async () => {
    const root = scaffold();
    writeAgent(root, "open", "name: open");

    const agent = (await load(root)).agents.open;

    expect(agent?.tools.map((item) => item.name)).toEqual(["word_count"]);
  });

  it("agent 的 tools 是空陣列時沒有任何工具", async () => {
    const root = scaffold();
    writeAgent(root, "bare", "tools: []");

    expect((await load(root)).agents.bare?.tools).toEqual([]);
  });

  it("tools 可以混用內建 toolset 與單一工具名稱", async () => {
    const root = scaffold();
    writeAgent(root, "writer", "tools: [files, word_count]");

    const agent = (await load(root)).agents.writer;

    expect(agent?.tools.map((item) => item.name)).toEqual([
      "read_file",
      "list_dir",
      "write_file",
      "word_count",
    ]);
  });

  it("agent 檔案的設定優先於 harness.yaml", async () => {
    const root = scaffold();
    writeConfig(root, "model: base\nmaxTurns: 5\n");
    writeAgent(root, "special", "model: special\nmaxTurns: 9");

    const { agents } = await load(root);

    expect(agents.main).toMatchObject({ model: "base", maxTurns: 5 });
    expect(agents.special).toMatchObject({ model: "special", maxTurns: 9 });
  });

  it("讀取權限設定", async () => {
    const root = scaffold();
    writeConfig(
      root,
      "permissions:\n  mode: dontAsk\n  deny: ['write_*']\n  ask: ['send_*']\n",
    );

    const { permissions } = await load(root);

    expect(permissions).toMatchObject({
      mode: "dontAsk",
      deny: ["write_*"],
      ask: ["send_*"],
      allow: [],
    });
  });

  it("用 tool() 定義的工具保留參數驗證", async () => {
    const root = scaffold();
    const toolModule = join(process.cwd(), "src", "tool.ts");
    writeFileSync(
      join(root, "tools", "strict.ts"),
      `import { z } from "zod";
import { tool } from ${JSON.stringify(toolModule)};
export const double = tool({
  name: "double",
  description: "加倍",
  parameters: z.object({ value: z.number() }),
  execute: ({ value }) => value * 2,
});
`,
    );

    const double = (await load(root)).tools.double;

    expect(() => double?.parse({ value: "x" })).toThrow(/參數不符合格式/);
  });
});

describe("loadProject：MCP 伺服器", () => {
  function scaffoldWithMcp(extraArgs = ""): string {
    const root = scaffold();
    rmSync(join(root, "tools"), { recursive: true });
    writeConfig(
      root,
      `mcpServers:
  backend:
    command: node
    args: [${JSON.stringify(MCP_FIXTURE)}${extraArgs}]
`,
    );
    writeAgent(root, "main", "tools: [backend]");
    return root;
  }

  it("伺服器名稱就是 toolset 名稱", async () => {
    const project = await load(scaffoldWithMcp());

    const names = project.agents.main?.tools.map((item) => item.name);

    expect(names).toContain("greet");
    expect(Object.keys(project.tools)).toContain("greet");
  });

  it("可以透過專案呼叫伺服器的工具", async () => {
    const project = await load(scaffoldWithMcp());

    const output = await project.tools.greet?.execute(
      { name: "小明" },
      contextIn(project.root),
    );

    expect(output).toBe("哈囉 小明");
  });

  it("伺服器啟動失敗時回報原因", async () => {
    const root = scaffoldWithMcp(', "--crash"');

    await expect(loadProject(root)).rejects.toThrow(
      /無法啟動 MCP 伺服器 backend/,
    );
  });

  it("不同來源的工具名稱重複時丟出錯誤", async () => {
    const root = scaffoldWithMcp();
    mkdirSync(join(root, "tools"));
    writeFileSync(
      join(root, "tools", "local.ts"),
      'export const greet = { name: "greet", description: "x", execute: () => "" };\n',
    );

    await expect(loadProject(root)).rejects.toThrow(
      /工具名稱重複：greet（backend 與 local）/,
    );
  });
});

describe("loadProject：設定錯誤", () => {
  it("沒有 harness.yaml 時提示用 harness new", async () => {
    await expect(loadProject(tempDir())).rejects.toThrow(/harness new/);
  });

  it("agent 引用不存在的工具", async () => {
    const root = scaffold();
    writeAgent(root, "broken", "tools: [nope]");

    await expect(loadProject(root)).rejects.toThrow(
      /找不到工具或 toolset：nope/,
    );
  });

  it("defaultAgent 指向不存在的 agent", async () => {
    const root = scaffold();
    writeConfig(root, "defaultAgent: ghost\n");

    await expect(loadProject(root)).rejects.toThrow(/ghost/);
  });

  it("沒有任何 agent", async () => {
    const root = scaffold();
    rmSync(join(root, "agents", "main.md"));

    await expect(loadProject(root)).rejects.toThrow(/沒有任何 agent/);
  });

  it("工具與內建工具同名", async () => {
    const root = scaffold();
    writeFileSync(
      join(root, "tools", "clash.ts"),
      'export const x = { name: "read_file", description: "x", execute: () => "" };\n',
    );

    await expect(loadProject(root)).rejects.toThrow(/與內建工具同名/);
  });
});

describe("parseFrontmatter", () => {
  it("拆出設定與內文，內文裡的分隔線不受影響", () => {
    const parsed = parseFrontmatter("---\nname: x\n---\n內文\n---\n後面\n");

    expect(parsed).toEqual({ meta: { name: "x" }, body: "內文\n---\n後面" });
  });

  it("沒有 frontmatter 時整份都是內文", () => {
    expect(parseFrontmatter("只有內文\n")).toEqual({
      meta: {},
      body: "只有內文",
    });
  });
});

describe("createProject", () => {
  it("不覆寫已存在的檔案", () => {
    const root = scaffold();
    writeFileSync(join(root, "AGENTS.md"), "我改過了");

    expect(createProject(root, "ts")).toEqual([]);
  });

  it("python 範本產生 MCP 伺服器與對應設定", () => {
    const root = join(tempDir(), "py_project");

    const created = createProject(root, "python");

    expect(created.map((file) => file.slice(root.length + 1)).sort()).toEqual([
      ".gitignore",
      "AGENTS.md",
      "agents/main.md",
      "harness.yaml",
      "tools/server.py",
    ]);
  });
});
