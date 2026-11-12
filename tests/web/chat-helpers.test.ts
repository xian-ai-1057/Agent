/** 對話畫面用到的純函式：步驟句子、計畫、CSV、附件、檔案預覽方式。 */

import { describe, expect, it } from "vitest";
import type { Message } from "../../src/types.ts";
import { parseCsv } from "../../web/src/components/chat/csv.ts";
import {
  extensionOf,
  languageOf,
  previewKind,
} from "../../web/src/components/chat/filetype.ts";
import { displayMath } from "../../web/src/components/chat/math.ts";
import {
  latestPlan,
  parsePlan,
  planFromArguments,
  planProgress,
} from "../../web/src/components/chat/plan.ts";
import {
  describeStep,
  sentenceText,
} from "../../web/src/components/chat/steps.ts";
import { splitAttachments } from "../../web/src/transcript.ts";

const step = (name: string, args: Record<string, unknown> = {}) =>
  sentenceText(describeStep({ name, args }));

describe("describeStep", () => {
  it("讀寫檔案的工具寫成動詞加路徑", () => {
    expect(
      describeStep({ name: "read_file", args: { path: "src/a.ts" } }),
    ).toEqual({ verb: "讀取", target: "src/a.ts", style: "code" });
    expect(step("write_file", { path: "out.md" })).toBe("寫入 out.md");
    expect(step("edit_file", { path: "b.py" })).toBe("修改 b.py");
  });

  it("list_dir 沒給路徑時是目前的資料夾", () => {
    expect(step("list_dir")).toBe("列出 .");
  });

  it("搜尋的字放進「」", () => {
    expect(step("search_files", { pattern: "TODO" })).toBe("搜尋「TODO」");
  });

  it("多行指令只顯示第一行", () => {
    expect(step("run_command", { command: "npm test\nnpm run lint" })).toBe(
      "執行 npm test …",
    );
  });

  it("網頁只顯示主機名稱", () => {
    expect(step("web_fetch", { url: "https://example.com/a/b?c=1" })).toBe(
      "讀取網頁 example.com",
    );
  });

  it("ask_ 開頭的工具是交給 subagent", () => {
    expect(step("ask_researcher", { question: "x" })).toBe("交給 researcher");
  });

  it("沒有參數的工具只有動詞", () => {
    expect(step("update_plan", { plan: [] })).toBe("更新計畫");
    expect(step("run_python", { code: "print(1)" })).toBe("執行 Python 程式");
  });

  it("不認識的工具把參數縮成一小段", () => {
    expect(step("lookup", { query: "天氣", limit: 3 })).toBe(
      'lookup query="天氣", limit=3',
    );
    expect(step("ping")).toBe("ping");
  });

  it("太長的對象截斷在 80 個字", () => {
    const sentence = describeStep({
      name: "read_file",
      args: { path: "a".repeat(100) },
    });
    expect(sentence.target).toBe(`${"a".repeat(80)}…`);
  });
});

describe("plan", () => {
  it("略過格式不對的項目，不認識的狀態當成 pending", () => {
    expect(
      parsePlan([
        { step: " 讀資料 ", status: "completed" },
        { step: "", status: "pending" },
        { status: "pending" },
        "nope",
        { step: "畫圖", status: "weird" },
      ]),
    ).toEqual([
      { step: "讀資料", status: "completed" },
      { step: "畫圖", status: "pending" },
    ]);
  });

  it("沒有項目時是 undefined", () => {
    expect(parsePlan([])).toBeUndefined();
    expect(parsePlan("x")).toBeUndefined();
  });

  it("參數可以是 JSON 字串或物件，壞掉的 JSON 是 undefined", () => {
    const plan = [{ step: "a", status: "in_progress" }];
    expect(planFromArguments(JSON.stringify({ plan }))).toEqual(plan);
    expect(planFromArguments({ plan })).toEqual(plan);
    expect(planFromArguments("{oops")).toBeUndefined();
  });

  it("取最後一次 update_plan", () => {
    const call = (id: string, name: string, args: unknown) => ({
      id,
      type: "function" as const,
      function: { name, arguments: JSON.stringify(args) },
    });
    const messages: Message[] = [
      {
        role: "assistant",
        content: null,
        tool_calls: [call("1", "update_plan", { plan: [{ step: "舊的" }] })],
      },
      { role: "tool", content: "ok", tool_call_id: "1" },
      {
        role: "assistant",
        content: null,
        tool_calls: [
          call("2", "update_plan", {
            plan: [
              { step: "新的", status: "completed" },
              { step: "下一步", status: "pending" },
            ],
          }),
          call("3", "read_file", { path: "a" }),
        ],
      },
    ];
    const plan = latestPlan(messages);
    expect(plan?.map((item) => item.step)).toEqual(["新的", "下一步"]);
    expect(plan && planProgress(plan)).toEqual({ done: 1, total: 2 });
    expect(latestPlan([])).toBeUndefined();
  });
});

describe("parseCsv", () => {
  it("引號裡可以有逗號、換行與跳脫的引號", () => {
    const text = 'name,note\r\n"Lin, A","說 ""好""\n第二行"\nB,x';
    expect(parseCsv(text).rows).toEqual([
      ["name", "note"],
      ["Lin, A", '說 "好"\n第二行'],
      ["B", "x"],
    ]);
  });

  it("去掉開頭的 BOM，最後沒有換行也算一列", () => {
    expect(parseCsv("﻿a,b\n1,2").rows).toEqual([
      ["a", "b"],
      ["1", "2"],
    ]);
  });

  it("超過 maxRows 時截斷並標記", () => {
    const text = ["h", "1", "2", "3", "4"].join("\n");
    const { rows, truncated } = parseCsv(text, 3);
    expect(rows).toEqual([["h"], ["1"], ["2"]]);
    expect(truncated).toBe(true);
    expect(parseCsv("a\nb", 5).truncated).toBe(false);
  });
});

describe("splitAttachments", () => {
  it("把附件從訊息最後拆出來", () => {
    expect(
      splitAttachments(
        "看看這兩個\n[附件] workspace/uploads/a.xlsx\n[附件] workspace/uploads/b.pdf",
      ),
    ).toEqual({
      text: "看看這兩個",
      attachments: ["workspace/uploads/a.xlsx", "workspace/uploads/b.pdf"],
    });
  });

  it("去掉 run_python 的路徑說明", () => {
    const line =
      "[附件] workspace/uploads/a.xlsx（run_python 裡的路徑：/workspace/uploads/a.xlsx）";
    expect(splitAttachments(`嗨\n${line}`).attachments).toEqual([
      "workspace/uploads/a.xlsx",
    ]);
  });

  it("檔名裡的括號（半形與全形）不會被切掉", () => {
    const content = [
      "請看",
      "[附件] workspace/uploads/report_(1).pdf",
      "[附件] workspace/uploads/報表（二月）.xlsx（run_python 裡的路徑：/workspace/uploads/報表（二月）.xlsx）",
    ].join("\n");
    expect(splitAttachments(content).attachments).toEqual([
      "workspace/uploads/report_(1).pdf",
      "workspace/uploads/報表（二月）.xlsx",
    ]);
  });

  it("只有訊息最後連續的附件行才算", () => {
    const content = "[附件] 不是附件\n內容";
    expect(splitAttachments(content)).toEqual({
      text: content,
      attachments: [],
    });
  });
});

describe("previewKind", () => {
  it("依副檔名決定怎麼預覽", () => {
    expect(previewKind("a/photo.PNG")).toBe("image");
    expect(previewKind("report.pdf")).toBe("pdf");
    expect(previewKind("README.md")).toBe("markdown");
    expect(previewKind("data.csv")).toBe("csv");
    expect(previewKind("page.html")).toBe("html");
    expect(previewKind("main.py")).toBe("text");
    expect(previewKind("Makefile")).toBe("text");
    expect(previewKind("Dockerfile")).toBe("text");
    expect(previewKind("book.xlsx")).toBe("binary");
  });

  it("SVG 顯示原始碼，不當成圖片", () => {
    expect(previewKind("chart.svg")).toBe("text");
    expect(languageOf("chart.svg")).toBe("xml");
  });

  it("程式碼的語言", () => {
    expect(languageOf("page.htm")).toBe("xml");
    expect(languageOf("a.ts")).toBe("typescript");
    expect(languageOf("notes.txt")).toBe("");
    expect(extensionOf("dir.v2/archive")).toBe("");
    expect(extensionOf(".env")).toBe("");
  });
});

describe("displayMath", () => {
  it("turns a whole-line $$…$$ into a display block", () => {
    expect(displayMath("前文\n\n$$r = \\frac{b-a}{a}$$\n\n後文")).toBe(
      "前文\n\n$$\nr = \\frac{b-a}{a}\n$$\n\n後文",
    );
  });

  it("keeps inline $$ and currency untouched", () => {
    const text = "費用 NT$1,000 到 NT$2,000，公式 $$x$$ 在句子裡";
    expect(displayMath(text)).toBe(text);
  });

  it("leaves code fences alone", () => {
    const text = "```\n$$x$$\n```";
    expect(displayMath(text)).toBe(text);
  });
});
