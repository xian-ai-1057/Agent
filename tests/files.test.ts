import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  editFileTool,
  listDirTool,
  MAX_READ_CHARS,
  readFileTool,
  resolvePath,
  writeFileTool,
} from "../src/builtin/files.ts";
import {
  findFilesTool,
  globMatcher,
  searchFilesTool,
} from "../src/builtin/search.ts";
import type { Tool } from "../src/tool.ts";
import type { RunContext } from "../src/types.ts";
import { contextIn, tempDir } from "./helpers.ts";

/** 先驗證參數（補上預設值）再執行工具，和主迴圈的做法相同。 */
async function call(
  target: Tool,
  args: Record<string, unknown>,
  context: RunContext,
) {
  return target.execute(target.parse(args), context);
}

/** 在臨時資料夾裡建立檔案，回傳對應的 context。 */
function project(files: Record<string, string>): RunContext {
  const context = contextIn(tempDir());
  for (const [path, content] of Object.entries(files)) {
    const target = join(context.workdir, path);
    mkdirSync(join(target, ".."), { recursive: true });
    writeFileSync(target, content);
  }
  return context;
}

describe("內建檔案工具", () => {
  it("寫入後可以讀回相同內容", async () => {
    const context = contextIn(tempDir());

    await writeFileTool.execute({ path: "out/a.md", content: "報告" }, context);

    expect(await readFileTool.execute({ path: "out/a.md" }, context)).toBe(
      "報告",
    );
  });

  it("list_dir 會標出資料夾", async () => {
    const context = contextIn(tempDir());
    mkdirSync(join(context.workdir, "sub"));
    writeFileSync(join(context.workdir, "a.txt"), "x");

    const args = listDirTool.parse({});

    expect(await listDirTool.execute(args, context)).toEqual(["a.txt", "sub/"]);
  });

  it("檔案過長時截斷並註明還有多少沒顯示", async () => {
    const context = contextIn(tempDir());
    writeFileSync(
      join(context.workdir, "big.txt"),
      "x".repeat(MAX_READ_CHARS + 7),
    );

    const output = await readFileTool.execute({ path: "big.txt" }, context);

    expect(output).toContain("後面還有 7 個字元沒有顯示");
  });

  it.each(["../secret.txt", "/etc/passwd", "a/../../b"])(
    "拒絕專案資料夾以外的路徑 %s",
    (path) => {
      expect(() => resolvePath(contextIn(tempDir()), path)).toThrow(
        /超出專案資料夾/,
      );
    },
  );

  it.each([
    ".harness/secrets/env.json",
    ".harness/secrets",
    "a/../.harness/secrets/x",
  ])("拒絕存放金鑰的資料夾 %s", (path) => {
    expect(() => resolvePath(contextIn(tempDir()), path)).toThrow(/金鑰/);
  });

  it("其他 .harness 底下的檔案仍然可以存取", () => {
    const context = contextIn(tempDir());

    expect(resolvePath(context, ".harness/memory.json")).toContain(
      "memory.json",
    );
  });

  it("只有會改檔案的工具需要使用者同意", () => {
    expect(writeFileTool.needsApproval).toBe(true);
    expect(editFileTool.needsApproval).toBe(true);
    expect(readFileTool.needsApproval).toBe(false);
    expect(findFilesTool.needsApproval).toBe(false);
    expect(searchFilesTool.needsApproval).toBe(false);
  });
});

describe("read_file 分段讀取", () => {
  const context = () => project({ "a.txt": "一\n二\n三\n四\n" });

  it("用 offset 與 limit 讀其中幾行並註明範圍", async () => {
    const output = await call(
      readFileTool,
      { path: "a.txt", offset: 2, limit: 2 },
      context(),
    );

    expect(output).toBe("二\n三\n[第 2–3 行，共 4 行]");
  });

  it("範圍涵蓋整個檔案時不加註明", async () => {
    const output = await call(
      readFileTool,
      { path: "a.txt", offset: 1, limit: 10 },
      context(),
    );

    expect(output).toBe("一\n二\n三\n四");
  });

  it("offset 超出檔案行數時回報錯誤", async () => {
    await expect(
      call(readFileTool, { path: "a.txt", offset: 9 }, context()),
    ).rejects.toThrow("檔案只有 4 行");
  });
});

describe("edit_file", () => {
  it("取代唯一的一處", async () => {
    const context = project({ "a.py": "x = 1\ny = 2\n" });

    const output = await call(
      editFileTool,
      { path: "a.py", old_text: "y = 2", new_text: "y = 3" },
      context,
    );

    expect(output).toBe("已修改 a.py（取代 1 處）");
    expect(readFileSync(join(context.workdir, "a.py"), "utf8")).toBe(
      "x = 1\ny = 3\n",
    );
  });

  it("出現多次時要求更多前後文", async () => {
    const context = project({ "a.py": "a\na\n" });

    await expect(
      call(
        editFileTool,
        { path: "a.py", old_text: "a", new_text: "b" },
        context,
      ),
    ).rejects.toThrow("出現 2 次");
  });

  it("replace_all 取代全部", async () => {
    const context = project({ "a.py": "a\na\n" });

    const output = await call(
      editFileTool,
      { path: "a.py", old_text: "a", new_text: "$&b", replace_all: true },
      context,
    );

    expect(output).toBe("已修改 a.py（取代 2 處）");
    expect(readFileSync(join(context.workdir, "a.py"), "utf8")).toBe(
      "$&b\n$&b\n",
    );
  });

  it("找不到原文時提示先讀檔", async () => {
    const context = project({ "a.py": "a\n" });

    await expect(
      call(
        editFileTool,
        { path: "a.py", old_text: "z", new_text: "b" },
        context,
      ),
    ).rejects.toThrow("找不到 old_text");
  });
});

describe("glob 樣式", () => {
  it.each([
    ["*.py", "src/deep/a.py", true],
    ["*.py", "a.pyc", false],
    ["src/*.ts", "src/a.ts", true],
    ["src/*.ts", "src/x/a.ts", false],
    ["src/**/*.ts", "src/a.ts", true],
    ["src/**/*.ts", "src/x/y/a.ts", true],
    ["test_?.{py,md}", "t/test_1.md", true],
    ["test_?.{py,md}", "test_12.py", false],
  ])("%s 比對 %s 的結果是 %s", (pattern, path, expected) => {
    expect(globMatcher(pattern)(path)).toBe(expected);
  });
});

describe("find_files", () => {
  it("回傳相對於專案根目錄的路徑，並略過 node_modules", async () => {
    const context = project({
      "src/a.ts": "",
      "src/b/c.ts": "",
      "src/d.py": "",
      "node_modules/x/index.ts": "",
    });

    const output = await call(findFilesTool, { pattern: "*.ts" }, context);

    expect(output).toBe("src/a.ts\nsrc/b/c.ts");
  });

  it("可以從子資料夾開始找", async () => {
    const context = project({ "src/b/c.ts": "", "other/c.ts": "" });

    const output = await call(
      findFilesTool,
      { pattern: "*.ts", path: "src" },
      context,
    );

    expect(output).toBe("src/b/c.ts");
  });

  it("沒有結果時明確說明", async () => {
    const output = await call(findFilesTool, { pattern: "*.go" }, project({}));

    expect(output).toBe("沒有符合 *.go 的檔案");
  });
});

describe("search_files", () => {
  it("回傳路徑、行號與內容", async () => {
    const context = project({
      "a.py": "def load():\n    pass\n",
      "b.md": "def load in docs\n",
    });

    const output = await call(
      searchFilesTool,
      { pattern: "def\\s+load", glob: "*.py" },
      context,
    );

    expect(output).toBe("a.py:1: def load():");
  });

  it("可以忽略大小寫，並略過二進位檔", async () => {
    const context = project({ "a.txt": "Hello\n", "b.bin": "hello\u0000" });

    const output = await call(
      searchFilesTool,
      { pattern: "hello", ignore_case: true },
      context,
    );

    expect(output).toBe("a.txt:1: Hello");
  });

  it("正規表示式有誤時回報給模型", async () => {
    await expect(
      call(searchFilesTool, { pattern: "(" }, project({})),
    ).rejects.toThrow("正規表示式有誤");
  });
});
