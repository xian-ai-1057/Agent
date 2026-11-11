/** Markdown 裡的程式碼區塊：上方顯示語言與「複製」按鈕。 */

import type { ReactNode } from "react";
import { CheckIcon } from "../Icons.tsx";
import { COPY_LABELS, useCopy } from "./copy.ts";
import { CopyIcon } from "./icons.tsx";

/** react-markdown 傳進來的 hast 節點（只用到需要的欄位）。 */
interface HastNode {
  type: string;
  value?: string;
  tagName?: string;
  properties?: { className?: unknown };
  children?: HastNode[];
}

const LANGUAGE_NAMES: Record<string, string> = {
  js: "JavaScript",
  javascript: "JavaScript",
  ts: "TypeScript",
  typescript: "TypeScript",
  py: "Python",
  python: "Python",
  sh: "Shell",
  bash: "Bash",
  shell: "Shell",
  json: "JSON",
  yaml: "YAML",
  yml: "YAML",
  html: "HTML",
  xml: "XML",
  css: "CSS",
  sql: "SQL",
  md: "Markdown",
  markdown: "Markdown",
};

export function CodeBlock({
  node,
  children,
}: {
  node?: HastNode;
  children?: ReactNode;
}) {
  const [state, copy] = useCopy();
  const code = node?.children?.find((child) => child.tagName === "code");
  const language = languageOf(code);
  const label = language ? (LANGUAGE_NAMES[language] ?? language) : "文字";
  return (
    <div className="ch-code">
      <div className="ch-code-head">
        <span className="ch-code-lang">{label}</span>
        <button
          type="button"
          className="ch-code-copy"
          aria-label={state === "idle" ? "複製程式碼" : COPY_LABELS[state]}
          onClick={() => copy(textOf(code).replace(/\n$/, ""))}
        >
          <span key={state} className="ch-swap">
            {state === "copied" ? (
              <CheckIcon size={14} />
            ) : (
              <CopyIcon size={14} />
            )}
            <span aria-live="polite">{COPY_LABELS[state]}</span>
          </span>
        </button>
      </div>
      <pre>{children}</pre>
    </div>
  );
}

function languageOf(node: HastNode | undefined): string {
  const names = node?.properties?.className;
  if (!Array.isArray(names)) return "";
  for (const name of names) {
    if (typeof name === "string" && name.startsWith("language-")) {
      return name.slice("language-".length);
    }
  }
  return "";
}

/** 節點底下所有文字接起來（高亮後的程式碼被拆成很多 span）。 */
export function textOf(node: HastNode | undefined): string {
  if (!node) return "";
  if (node.type === "text") return node.value ?? "";
  return (node.children ?? []).map(textOf).join("");
}
