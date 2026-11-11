# 簡介

Agent Harness 是完全開源、不綁定模型供應商的 agent harness。用一份 `harness.yaml` 和幾個 Markdown 檔定義 agent，用普通的函式或現成的 MCP 伺服器給它工具，再從命令列、網頁或自己的程式裡和它對話。

框架負責其餘的事：呼叫模型、執行工具並把結果送回模型、保存與接續對話、在危險操作前取得使用者同意、在關鍵時間點插入自訂邏輯。

提供 **TypeScript** 與 **Python** 兩個實作，共用同一種專案格式，對話紀錄也互通。

## 運作方式

一個 agent 的核心就是一個迴圈：把對話送給模型 → 模型要求呼叫工具 → 框架檢查權限、執行工具、把結果送回 → 直到模型不再呼叫工具，回覆使用者。

<div class="diagram" role="img" aria-label="使用者與 harness 對話；harness 透過 OpenAI 相容端點呼叫模型，並執行工具">
<svg viewBox="0 0 760 270" xmlns="http://www.w3.org/2000/svg">
  <title>harness 運作方式</title>
  <defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path class="ah" d="M0 0L10 5L0 10z"/></marker></defs>
  <rect class="box" x="10" y="100" width="130" height="70" rx="10"/>
  <text x="75" y="131" text-anchor="middle">使用者</text>
  <text class="sub" x="75" y="152" text-anchor="middle">命令列 / 網頁 / 程式</text>
  <rect class="core" x="230" y="70" width="260" height="130" rx="12"/>
  <text x="360" y="104" text-anchor="middle" style="font-weight:650">harness</text>
  <text class="sub" x="360" y="130" text-anchor="middle">主迴圈 · 權限 · Hooks</text>
  <text class="sub" x="360" y="150" text-anchor="middle">對話紀錄（JSONL）</text>
  <text class="sub" x="360" y="170" text-anchor="middle">subagent · 知識庫 · 壓縮…</text>
  <rect class="box" x="580" y="20" width="170" height="90" rx="10"/>
  <text x="665" y="52" text-anchor="middle">模型</text>
  <text class="sub" x="665" y="74" text-anchor="middle">Claude · GPT · Gemini</text>
  <text class="sub" x="665" y="93" text-anchor="middle">Ollama · LiteLLM…</text>
  <rect class="box" x="580" y="160" width="170" height="90" rx="10"/>
  <text x="665" y="192" text-anchor="middle">工具</text>
  <text class="sub" x="665" y="214" text-anchor="middle">Python / TS 函式</text>
  <text class="sub" x="665" y="233" text-anchor="middle">MCP 伺服器 · 內建工具</text>
  <path class="arrow" d="M140 125 H226" marker-end="url(#ah)"/>
  <path class="arrow" d="M230 148 H144" marker-end="url(#ah)"/>
  <path class="arrow" d="M490 100 C530 100 540 70 576 66" marker-end="url(#ah)"/>
  <path class="arrow" d="M490 170 C530 170 540 200 576 204" marker-end="url(#ah)"/>
  <text class="sub" x="534" y="70" text-anchor="middle">請求</text>
  <text class="sub" x="534" y="214" text-anchor="middle">執行</text>
</svg>
</div>

## 兩個版本

兩個版本使用**同一種專案格式**（同一份 `harness.yaml` 與 `agents/*.md`），對話紀錄也互通。差別在於工具怎麼接、能嵌進什麼程式：

| | TypeScript 版 | Python 版 |
|---|---|---|
| 指令 | `harness` | `pyharness` |
| Python 寫的工具 | 透過 MCP 伺服器（另一個行程） | 直接 import，加 `@harness.tool` |
| TypeScript 寫的工具 | 直接載入 `tools/*.ts` | 不支援 |
| 可以嵌進 | Node.js 服務 | Python 服務（FastAPI、Streamlit…） |
| 模型 | 任何 OpenAI 相容端點 | litellm（直連供應商或連端點） |
| 進階功能 | — | 多 agent、知識庫、skills、記憶、防護、壓縮、沙盒、伺服器部署 |

完整的功能對照見[兩個版本的比較](/reference/versions)。

**怎麼選：**

- **工具是 Python**、要嵌進 Python 網頁服務、要架在伺服器上給團隊用，或需要進階功能 → [Python 版](/guide/install#python-版)。
- **工具是 TypeScript**、或要嵌進 Node.js 服務 → [TypeScript 版](/guide/install#typescript-版)。
- **只想架起來直接用，不寫程式** → [快速部署](/guide/deploy)。

## 接下來

- [快速部署](/guide/deploy)：用 Docker 架起網頁版，在瀏覽器裡設定模型就能用。
- [快速開始](/guide/quickstart)：建立第一個專案，從命令列和它對話。
- [為既有專案加上 agent](/guide/migration)：已經有一套程式碼，想讓 agent 使用它的功能。
