/**
 * 對話畫面的純函式：沙盒產生的檔案、回覆裡圖片的路徑、拒絕的工具呼叫、
 * 已經自己修正的錯誤、edit_file 的檔案變更。
 */

import { describe, expect, it } from "vitest";
import type { Message } from "../../src/types.ts";
import {
  DENIED_RESULT,
  PRODUCED_MARKER,
  type SessionDetail,
  type SessionStatus,
} from "../../src/web/protocol.ts";
import {
  externalImageHost,
  isThumbnail,
  projectPath,
} from "../../web/src/components/chat/filetype.ts";
import { toolLabel } from "../../web/src/components/chat/steps.ts";
import {
  approvalPreview,
  buildTurns,
  collectProduced,
  isDeniedResult,
  type Step,
  splitProduced,
  summarizeSteps,
  type Turn,
} from "../../web/src/transcript.ts";

const call = (id: string, name: string, args: Record<string, unknown> = {}) =>
  ({
    role: "assistant",
    content: null,
    tool_calls: [
      {
        id,
        type: "function",
        function: { name, arguments: JSON.stringify(args) },
      },
    ],
  }) as Message;

const result = (id: string, content: string): Message => ({
  role: "tool",
  tool_call_id: id,
  content,
});

const user = (content: string): Message => ({ role: "user", content });
const reply = (content: string): Message => ({ role: "assistant", content });

function detail(
  messages: Message[],
  status: SessionStatus = "done",
): SessionDetail {
  return {
    summary: {
      id: "s1",
      title: "測試",
      agent: "main",
      status,
      updatedAt: 0,
      toolErrors: 0,
    },
    messages,
    partial: "",
    approvals: [],
    mode: null,
  };
}

function stepsOf(turn: Turn | undefined): Step[] {
  return (turn?.blocks ?? []).flatMap((block) =>
    block.kind === "steps" ? block.steps : [],
  );
}

describe("splitProduced", () => {
  it("拆出產生的檔案，結果裡不留標記行", () => {
    const content = [
      "畫好了",
      "",
      `${PRODUCED_MARKER}workspace/chart.png`,
      `${PRODUCED_MARKER}workspace/out/報表 (1).xlsx`,
    ].join("\n");
    expect(splitProduced(content)).toEqual({
      text: "畫好了",
      produced: ["workspace/chart.png", "workspace/out/報表 (1).xlsx"],
    });
  });

  it("沒有標記時原樣保留，重複的路徑只算一次", () => {
    expect(splitProduced("  ok\n")).toEqual({ text: "  ok\n", produced: [] });
    const twice = `x\n${PRODUCED_MARKER}a.png\n${PRODUCED_MARKER}a.png`;
    expect(splitProduced(twice).produced).toEqual(["a.png"]);
  });

  it("buildTurns 把檔案記在那一輪，步驟的結果拿掉標記行", () => {
    const turns = buildTurns(
      detail([
        user("畫圖"),
        call("c1", "run_python", { code: "plot()" }),
        result("c1", `完成\n${PRODUCED_MARKER}workspace/chart.png`),
        reply("圖在這裡"),
        user("再一張"),
        call("c2", "run_python", { code: "plot()" }),
        result(
          "c2",
          `完成\n${PRODUCED_MARKER}workspace/b.csv\n${PRODUCED_MARKER}workspace/chart.png`,
        ),
      ]),
      {},
    );
    expect(turns[0]?.produced).toEqual(["workspace/chart.png"]);
    expect(stepsOf(turns[0])[0]?.result).toBe("完成");
    expect(turns[1]?.produced).toEqual([
      "workspace/b.csv",
      "workspace/chart.png",
    ]);
    // 面板的清單：新的在前面，同一個路徑只出現一次。
    expect(collectProduced(turns)).toEqual([
      "workspace/chart.png",
      "workspace/b.csv",
    ]);
  });

  it("subagent 步驟產生的檔案也算進這一輪", () => {
    const base = detail([
      user("分析"),
      call("c1", "ask_analyst"),
      result("c1", "分析完成"),
    ]);
    base.subagentSteps = {
      c1: [
        {
          id: "x1",
          callId: "sub1",
          agent: "analyst",
          path: ["analyst"],
          name: "run_python",
          arguments: {},
          status: "done",
          result: `ok\n${PRODUCED_MARKER}workspace/sub.png`,
        },
      ],
    };
    const [turn] = buildTurns(base, {});
    expect(turn?.produced).toEqual(["workspace/sub.png"]);
    expect(stepsOf(turn)[0]?.children?.[0]?.result).toBe("ok");
  });
});

describe("isThumbnail", () => {
  it("只有常見的點陣圖顯示成縮圖", () => {
    expect(isThumbnail("a/b.PNG")).toBe(true);
    expect(isThumbnail("c.jpeg")).toBe(true);
    expect(isThumbnail("d.webp")).toBe(true);
    expect(isThumbnail("e.svg")).toBe(false);
    expect(isThumbnail("f.pdf")).toBe(false);
  });
});

describe("projectPath", () => {
  it("相對路徑去掉開頭的 ./", () => {
    expect(projectPath("chart.png")).toBe("chart.png");
    expect(projectPath("./workspace/chart.png")).toBe("workspace/chart.png");
    expect(projectPath("././a.png")).toBe("a.png");
  });

  it("容器裡的 /workspace 對應到專案的 workspace/", () => {
    expect(projectPath("/workspace/out/chart.png")).toBe(
      "workspace/out/chart.png",
    );
    expect(projectPath("/workspace/out/chart.png", false)).toBe(
      "workspace/out/chart.png",
    );
  });

  it("有工作資料夾時，/workspace 就是這段對話的資料夾", () => {
    expect(projectPath("/workspace/out/chart.png", true)).toBe("out/chart.png");
    expect(projectPath("/workspace/%E5%9C%96.png", true)).toBe("圖.png");
    expect(projectPath("chart.png", true)).toBe("chart.png");
    expect(projectPath("./out/a.png", true)).toBe("out/a.png");
    expect(projectPath("/workspace", true)).toBeUndefined();
    expect(projectPath("/workspace/", true)).toBeUndefined();
    expect(projectPath("/etc/passwd", true)).toBeUndefined();
    expect(projectPath("https://example.com/a.png", true)).toBeUndefined();
  });

  it("百分比編碼還原成原本的檔名，去掉 ? 與 # 後面", () => {
    expect(projectPath("workspace/%E5%9C%96%201.png?v=2")).toBe(
      "workspace/圖 1.png",
    );
    expect(projectPath("a.png#top")).toBe("a.png");
  });

  it("外部網址、data:、其他絕對路徑照原樣（undefined）", () => {
    expect(projectPath("https://example.com/a.png")).toBeUndefined();
    expect(projectPath("http://example.com/a.png")).toBeUndefined();
    expect(projectPath("data:image/png;base64,AAAA")).toBeUndefined();
    expect(projectPath("//cdn.example.com/a.png")).toBeUndefined();
    expect(projectPath("/etc/passwd")).toBeUndefined();
    expect(projectPath("/workspace")).toBeUndefined();
    expect(projectPath("")).toBeUndefined();
  });
});

describe("externalImageHost", () => {
  it("外部網站的圖片回傳主機名稱", () => {
    expect(externalImageHost("https://example.com/a.png?q=1")).toBe(
      "example.com",
    );
    expect(externalImageHost("HTTP://Evil.example:8080/x")).toBe(
      "evil.example:8080",
    );
    expect(externalImageHost(" //cdn.example.com/a.png")).toBe(
      "cdn.example.com",
    );
  });

  it("資料夾裡的檔案、data: 與其他網址不算", () => {
    expect(externalImageHost("chart.png")).toBeUndefined();
    expect(externalImageHost("/workspace/chart.png")).toBeUndefined();
    expect(externalImageHost("data:image/png;base64,AAAA")).toBeUndefined();
    expect(externalImageHost("blob:https://x/1")).toBeUndefined();
    expect(externalImageHost("")).toBeUndefined();
  });
});

describe("拒絕的工具呼叫", () => {
  it("認得拒絕的結果，有沒有錯誤標記都算", () => {
    expect(isDeniedResult(DENIED_RESULT)).toBe(true);
    expect(isDeniedResult(`[錯誤] ${DENIED_RESULT}`)).toBe(true);
    expect(isDeniedResult("找不到檔案")).toBe(false);
    expect(isDeniedResult(undefined)).toBe(false);
  });

  it("步驟狀態是 denied，不算工具錯誤；寫檔的變更是 denied", () => {
    const [turn] = buildTurns(
      detail([
        user("寫檔"),
        call("c1", "write_file", { path: "a.md", content: "x" }),
        result("c1", `[錯誤] ${DENIED_RESULT}`),
        reply("好，不寫了"),
      ]),
      {},
    );
    const steps = stepsOf(turn);
    expect(steps[0]?.status).toBe("denied");
    expect(summarizeSteps(steps).errors).toBe(0);
    expect(turn?.files[0]?.status).toBe("denied");
  });
});

describe("已經自己修正的錯誤", () => {
  it("後面同一個工具成功了，錯誤算已修正", () => {
    const [turn] = buildTurns(
      detail(
        [
          user("跑程式"),
          call("c1", "run_python", { code: "bad" }),
          result("c1", "[錯誤] Traceback: NameError"),
          call("c2", "run_python", { code: "good" }),
          result("c2", "ok"),
        ],
        "running",
      ),
      {},
    );
    const steps = stepsOf(turn);
    expect(steps[0]?.recovered).toBe(true);
    expect(summarizeSteps(steps)).toMatchObject({ errors: 0, fixed: 1 });
  });

  it("這一輪正常做完、錯誤之後有回覆，也算已修正", () => {
    const [turn] = buildTurns(
      detail([
        user("讀檔"),
        call("c1", "read_file", { path: "nope.txt" }),
        result("c1", "[錯誤] 找不到檔案"),
        reply("檔案不存在，改用預設值。"),
      ]),
      {},
    );
    expect(stepsOf(turn)[0]?.recovered).toBe(true);
  });

  it("最後還在失敗的錯誤要顯示", () => {
    const failing = buildTurns(
      detail(
        [user("跑程式"), call("c1", "run_python"), result("c1", "[錯誤] boom")],
        "running",
      ),
      {},
    );
    expect(stepsOf(failing[0])[0]?.recovered).toBe(false);
    expect(summarizeSteps(stepsOf(failing[0]))).toMatchObject({
      errors: 1,
      fixed: 0,
    });

    // 有回覆但這一輪出錯停下來：不算修正。
    const errored = buildTurns(
      detail(
        [
          user("讀檔"),
          call("c1", "read_file", { path: "x" }),
          result("c1", "[錯誤] 找不到"),
          reply("我再試一次"),
        ],
        "error",
      ),
      {},
    );
    expect(stepsOf(errored[0])[0]?.recovered).toBe(false);
  });

  it("別的工具成功不算修正", () => {
    const [turn] = buildTurns(
      detail(
        [
          user("x"),
          call("c1", "run_python"),
          result("c1", "[錯誤] boom"),
          call("c2", "read_file", { path: "a" }),
          result("c2", "ok"),
        ],
        "running",
      ),
      {},
    );
    expect(stepsOf(turn)[0]?.recovered).toBe(false);
  });
});

describe("edit_file", () => {
  const args = { path: "src/a.py", old_text: "x = 1", new_text: "x = 2" };

  it("有確認時的內容就用整個檔案比較", () => {
    const [turn] = buildTurns(
      detail([user("改"), call("c1", "edit_file", args), result("c1", "ok")]),
      { c1: { path: "src/a.py", before: "x = 1\ny\n", after: "x = 2\ny\n" } },
    );
    expect(turn?.files[0]).toMatchObject({
      path: "src/a.py",
      before: "x = 1\ny\n",
      after: "x = 2\ny\n",
      status: "written",
      edited: true,
    });
    expect(turn?.files[0]?.fragment).toBeUndefined();
  });

  it("沒有確認時的內容，只比較被取代的那一段", () => {
    const [turn] = buildTurns(
      detail([user("改"), call("c1", "edit_file", args), result("c1", "ok")]),
      {},
    );
    expect(turn?.files[0]).toMatchObject({
      before: "x = 1",
      after: "x = 2",
      status: "written",
      edited: true,
      fragment: true,
    });
  });

  it("確認卡片：伺服器沒附 preview 時用參數裡的那一段", () => {
    const approval = {
      id: "a1",
      sessionId: "s1",
      callId: "c1",
      tool: "edit_file",
      arguments: args,
    };
    expect(approvalPreview(approval)).toEqual({
      path: "src/a.py",
      before: "x = 1",
      after: "x = 2",
    });
    const preview = { path: "src/a.py", before: "a", after: "b" };
    expect(approvalPreview({ ...approval, preview })).toBe(preview);
    expect(
      approvalPreview({ ...approval, tool: "run_command", arguments: {} }),
    ).toBeUndefined();
  });
});

describe("toolLabel", () => {
  it("認識的工具有白話名稱，其他是 undefined", () => {
    expect(toolLabel("edit_file")).toBe("修改檔案");
    expect(toolLabel("run_python")).toBe("執行 Python 程式");
    expect(toolLabel("ask_analyst")).toBe("交給 analyst");
    expect(toolLabel("my_tool")).toBeUndefined();
  });
});
