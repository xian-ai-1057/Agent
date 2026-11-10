import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  listDirTool,
  MAX_READ_CHARS,
  readFileTool,
  resolvePath,
  writeFileTool,
} from "../src/builtin/files.ts";
import { contextIn, tempDir } from "./helpers.ts";

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

  it("只有 write_file 需要使用者同意", () => {
    expect(writeFileTool.needsApproval).toBe(true);
    expect(readFileTool.needsApproval).toBe(false);
  });
});
