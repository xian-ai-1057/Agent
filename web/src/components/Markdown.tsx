/**
 * 顯示 agent 的回覆。react-markdown 預設不會執行內容裡的 HTML，
 * 模型或工具輸出的文字不會變成可以執行的程式。
 */

import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const plugins = [remarkGfm];

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
        remarkPlugins={plugins}
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer noopener">
              {children}
            </a>
          ),
        }}
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
