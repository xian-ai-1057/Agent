/**
 * 顯示 agent 的回覆。react-markdown 預設不會執行內容裡的 HTML，
 * 模型或工具輸出的文字不會變成可以執行的程式。
 *
 * 程式碼區塊會上色並加上「複製」按鈕；只有 $$…$$ 用 KaTeX 排版。
 * 單一個 $ 不算數學式：回覆裡常有 NT$1,000 到 NT$2,000 這種金額，
 * 兩個 $ 之間的文字不能被當成數學式。
 *
 * KaTeX 與上色的程式比較大，內容裡真的有 $$ 或程式碼區塊時才載入。
 * 還在串流的回覆每來一段字就重畫一次，所以先不排版、不上色，等回覆
 * 完成後再做一次。
 *
 * localFiles 為 true 時（伺服器支援 /api/files），圖片的 src 是相對路徑
 * （或沙盒裡的 /workspace/...）就從工作資料夾讀取。
 */

import { memo, useEffect, useMemo, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { fileUrl } from "../api.ts";
import "./chat/chat.css";
import { CodeBlock } from "./chat/CodeBlock.tsx";
import { projectPath } from "./chat/filetype.ts";
import { displayMath } from "./chat/math.ts";

type Plugins = NonNullable<
  Parameters<typeof ReactMarkdown>[0]["rehypePlugins"]
>;

const remarkPlugins = [
  remarkGfm,
  [remarkMath, { singleDollarTextMath: false }],
] as NonNullable<Parameters<typeof ReactMarkdown>[0]["remarkPlugins"]>;

const MATH = /\$\$/;
// 縮排在清單或引用（>）底下的程式碼區塊也要算進去。
const FENCE = /^[ \t>]*(`{3,}|~{3,})/m;

// 載入過就留著，之後的訊息直接用。
let katexPlugin: Plugins[number] | undefined;
let highlightPlugin: Plugins[number] | undefined;
let mathLoading: Promise<void> | undefined;
let highlightLoading: Promise<void> | undefined;

function loadMath(): Promise<void> {
  mathLoading ??= Promise.all([
    import("rehype-katex"),
    import("katex/dist/katex.min.css"),
  ]).then(([module]) => {
    // 打錯的數學式用警告色顯示原文，不用紅色。
    katexPlugin = [
      module.default,
      { throwOnError: false, errorColor: "var(--warn-ink)" },
    ];
  });
  return mathLoading;
}

function loadHighlight(): Promise<void> {
  highlightLoading ??= import("rehype-highlight").then((module) => {
    highlightPlugin = module.default;
  });
  return highlightLoading;
}

/** 這段文字需要的 rehype 外掛；還沒載入的先載入，好了再重畫。 */
function useRehypePlugins(text: string, streaming: boolean): Plugins {
  const wantMath = !streaming && MATH.test(text);
  const wantCode = !streaming && FENCE.test(text);
  const [, setLoaded] = useState(0);

  useEffect(() => {
    const loads: Promise<void>[] = [];
    if (wantMath && !katexPlugin) loads.push(loadMath());
    if (wantCode && !highlightPlugin) loads.push(loadHighlight());
    if (loads.length === 0) return;
    let alive = true;
    Promise.all(loads).then(
      () => alive && setLoaded((count) => count + 1),
      () => undefined,
    );
    return () => {
      alive = false;
    };
  }, [wantMath, wantCode]);

  const math = wantMath ? katexPlugin : undefined;
  const code = wantCode ? highlightPlugin : undefined;
  return useMemo(() => {
    // KaTeX 要排在上色前面，數學式才不會被當成程式碼。
    const plugins: Plugins = [];
    if (math) plugins.push(math);
    if (code) plugins.push(code);
    return plugins;
  }, [math, code]);
}

const components: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  ),
  pre: ({ node, children }) => (
    <CodeBlock node={node as Parameters<typeof CodeBlock>[0]["node"]}>
      {children}
    </CodeBlock>
  ),
};

/** 圖片的 src 指向專案裡的檔案時，改從 /api/files 讀取。 */
const localComponents: Components = {
  ...components,
  img: ({ src, alt, title }) => {
    const path = typeof src === "string" ? projectPath(src) : undefined;
    return (
      <img
        src={path ? fileUrl(path) : src}
        alt={alt ?? ""}
        title={title}
        loading="lazy"
      />
    );
  },
};

export const Markdown = memo(function Markdown({
  text,
  streaming = false,
  localFiles = false,
}: {
  text: string;
  streaming?: boolean;
  /** 伺服器可以讀取工作資料夾的檔案（features.files）。 */
  localFiles?: boolean;
}) {
  const rehypePlugins = useRehypePlugins(text, streaming);
  const source = useMemo(
    () => (streaming ? text : displayMath(text)),
    [text, streaming],
  );
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={localFiles ? localComponents : components}
      >
        {source}
      </ReactMarkdown>
      {streaming && (
        <span className="caret" aria-hidden="true">
          ▍
        </span>
      )}
    </div>
  );
});

/**
 * 把一段文字包成 Markdown 的程式碼區塊。圍欄比內容裡最長的反引號
 * 還長一個，內容才不會提早結束區塊。
 */
export function fenced(text: string, language: string): string {
  const longest = Math.max(
    2,
    ...[...text.matchAll(/`+/g)].map((match) => match[0].length),
  );
  const fence = "`".repeat(longest + 1);
  return `${fence}${language}\n${text.replace(/\n$/, "")}\n${fence}`;
}
