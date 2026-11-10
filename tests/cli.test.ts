import { existsSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { Readable } from "node:stream";
import { describe, expect, it } from "vitest";
import { main } from "../src/cli.ts";
import type { ModelFn } from "../src/model.ts";
import { listSessionIds } from "../src/session.ts";
import type { ModelResponse } from "../src/types.ts";
import { fakeModel, tempDir, textResponse, toolResponse } from "./helpers.ts";

/** 執行 CLI 並收集輸出。 */
async function cli(
  argv: string[],
  options: { script?: ModelResponse[]; model?: ModelFn; input?: string[] } = {},
) {
  let out = "";
  let err = "";
  const fake = fakeModel(options.script ?? []);
  const code = await main(argv, {
    write: (text) => {
      out += text;
    },
    writeError: (text) => {
      err += text;
    },
    input: Readable.from((options.input ?? []).map((line) => `${line}\n`)),
    interactive: false,
    model: options.model ?? fake.model,
  });
  return { code, out, err, requests: fake.requests };
}

/** 用 `harness new --lang ts` 建立專案，回傳專案路徑。 */
async function newProject(): Promise<string> {
  const root = join(tempDir(), "demo");
  await cli(["new", root, "--lang", "ts"]);
  return root;
}

const sessionsOf = (root: string) =>
  listSessionIds(join(root, ".harness", "sessions"));

describe("harness new", () => {
  it("建立專案檔案並提示下一步", async () => {
    const root = join(tempDir(), "fresh");

    const { code, out } = await cli(["new", root, "--lang", "ts"]);

    expect(code).toBe(0);
    expect(existsSync(join(root, "harness.yaml"))).toBe(true);
    expect(existsSync(join(root, "tools", "example.ts"))).toBe(true);
    expect(out).toContain("harness chat");
  });

  it("預設建立 Python 範本並提醒安裝 MCP 套件", async () => {
    const root = join(tempDir(), "py");

    const { out } = await cli(["new", root]);

    expect(existsSync(join(root, "tools", "server.py"))).toBe(true);
    expect(out).toContain("pip install");
  });

  it("不認得的 --lang 回報錯誤", async () => {
    const { code, err } = await cli(["new", tempDir(), "--lang", "ruby"]);

    expect(code).toBe(1);
    expect(err).toContain("--lang 必須是 python 或 ts");
  });
});

describe("harness run", () => {
  it("印出工具呼叫與最後的回答", async () => {
    const root = await newProject();

    const { code, out } = await cli(["run", "-p", root, "幾行？"], {
      script: [
        toolResponse(["word_count", { text: "一\n二" }]),
        textResponse("共 2 行"),
      ],
    });

    expect(code).toBe(0);
    expect(out).toContain('⚙ word_count({"text":"一\\n二"})');
    expect(out).toContain("共 2 行");
  });

  it("-c 接續最新的 session", async () => {
    const root = await newProject();
    await cli(["run", "-p", root, "一"], { script: [textResponse("第一次")] });

    const { requests } = await cli(["run", "-p", root, "-c", "二"], {
      script: [textResponse("第二次")],
    });

    expect(requests[0]?.messages.map((m) => m.role)).toEqual([
      "system",
      "user",
      "assistant",
      "user",
    ]);
    expect(sessionsOf(root)).toHaveLength(1);
  });

  it("-r 接續指定的 session", async () => {
    const root = await newProject();
    await cli(["run", "-p", root, "一"], { script: [textResponse("第一次")] });
    const [id = ""] = sessionsOf(root);

    const { requests } = await cli(["run", "-p", root, "-r", id, "二"], {
      script: [textResponse("第二次")],
    });

    expect(requests[0]?.messages).toHaveLength(4);
  });

  it("-c 但沒有任何 session 時回報錯誤", async () => {
    const root = await newProject();

    const { code, err } = await cli(["run", "-p", root, "-c", "嗨"]);

    expect(code).toBe(1);
    expect(err).toContain("沒有任何 session");
  });

  it("-m 覆寫專案設定的模型", async () => {
    const root = await newProject();

    const { requests } = await cli(["run", "-p", root, "-m", "other", "嗨"], {
      script: [textResponse("好")],
    });

    expect(requests[0]?.model).toBe("other");
  });

  it("找不到專案時回報錯誤", async () => {
    const { code, err } = await cli(["run", "-p", tempDir(), "嗨"]);

    expect(code).toBe(1);
    expect(err).toContain("harness.yaml");
  });

  it("找不到 agent 時回報錯誤", async () => {
    const root = await newProject();

    const { code, err } = await cli(["run", "-p", root, "-a", "ghost", "嗨"]);

    expect(code).toBe(1);
    expect(err).toContain("沒有 ghost 這個 agent");
  });

  it("達到輪數上限時提醒使用者", async () => {
    const root = await newProject();
    writeFileSync(join(root, "harness.yaml"), "maxTurns: 1\n");

    const { out } = await cli(["run", "-p", root, "嗨"], {
      script: [toolResponse(["word_count", { text: "x" }])],
    });

    expect(out).toContain("輪數上限");
  });

  it("模型出錯時顯示原因而不是堆疊", async () => {
    const root = await newProject();
    // biome-ignore lint/correctness/useYield: 模擬連線失敗
    const broken: ModelFn = async function* () {
      throw new TypeError("連不上模型");
    };

    const { code, out } = await cli(["run", "-p", root, "嗨"], {
      model: broken,
    });

    expect(code).toBe(1);
    expect(out).toContain("✗ TypeError: 連不上模型");
  });

  it("無法詢問使用者時，需要同意的工具被拒絕", async () => {
    const root = await newProject();
    writeFileSync(
      join(root, "agents", "main.md"),
      "---\ntools: [files]\n---\n你是助手\n",
    );

    const { out } = await cli(["run", "-p", root, "存檔"], {
      script: [
        toolResponse(["write_file", { path: "a.md", content: "x" }]),
        textResponse("無法存檔"),
      ],
    });

    expect(out).toContain("✗ 使用者沒有同意這次呼叫");
    expect(existsSync(join(root, "a.md"))).toBe(false);
  });

  it("--bypass 讓需要同意的工具直接執行", async () => {
    const root = await newProject();
    writeFileSync(
      join(root, "agents", "main.md"),
      "---\ntools: [files]\n---\n你是助手\n",
    );

    await cli(["run", "-p", root, "--bypass", "存檔"], {
      script: [
        toolResponse(["write_file", { path: "a.md", content: "x" }]),
        textResponse("已存檔"),
      ],
    });

    expect(existsSync(join(root, "a.md"))).toBe(true);
  });
});

describe("harness chat", () => {
  it("逐行執行輸入直到 /exit，並延續同一段對話", async () => {
    const root = await newProject();

    const { code, out, requests } = await cli(["chat", "-p", root], {
      script: [textResponse("第一次"), textResponse("第二次")],
      input: ["一", "", "二", "/exit", "不會執行"],
    });

    expect(code).toBe(0);
    expect(out).toContain("第一次");
    expect(out).toContain("第二次");
    expect(requests).toHaveLength(2);
    expect(requests[1]?.messages).toHaveLength(4);
  });

  it("輸入結束時正常離開", async () => {
    const root = await newProject();

    const { code } = await cli(["chat", "-p", root], { input: [] });

    expect(code).toBe(0);
  });
});

describe("參數錯誤", () => {
  it("沒有指令時顯示用法", async () => {
    const { code, err } = await cli([]);

    expect(code).toBe(2);
    expect(err).toContain("harness chat");
  });

  it("run 沒有給訊息時顯示用法", async () => {
    const { code, err } = await cli(["run"]);

    expect(code).toBe(2);
    expect(err).toContain("用法");
  });

  it("不認得的選項顯示錯誤與用法", async () => {
    const { code, err } = await cli(["chat", "--nope"]);

    expect(code).toBe(2);
    expect(err).toContain("錯誤");
  });

  it("--help 顯示用法", async () => {
    const { code, out } = await cli(["--help"]);

    expect(code).toBe(0);
    expect(out).toContain("用法");
  });
});
