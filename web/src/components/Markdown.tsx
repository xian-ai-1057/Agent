/**
 * 顯示 agent 的回覆。react-markdown 預設不會執行內容裡的 HTML，
 * 模型或工具輸出的文字不會變成可以執行的程式。
 *
 * 程式碼區塊會上色並加上「複製」按鈕；$…$ 與 $$…$$ 用 KaTeX 排版。
 */

import "katex/dist/katex.min.css";
import { memo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "./chat/chat.css";
import { CodeBlock } from "./chat/CodeBlock.tsx";

const remarkPlugins = [remarkGfm, remarkMath];
// KaTeX 要排在上色前面，數學式才不會被當成程式碼。
const rehypePlugins = [
  [rehypeKatex, { throwOnError: false, errorColor: "var(--danger)" }],
  rehypeHighlight,
] as NonNullable<Parameters<typeof ReactMarkdown>[0]["rehypePlugins"]>;

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

export const Markdown = memo(function Markdown({
  text,
  streaming = false,
}: {
  text: string;
  streaming?: boolean;
}) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={components}
      >
        {text}
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
