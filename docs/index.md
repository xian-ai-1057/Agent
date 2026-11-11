---
layout: home

hero:
  name: Agent Harness
  text: 用幾個檔案定義 agent，其餘交給框架。
  tagline: 完全開源、不綁定模型供應商的 agent harness。用一份 harness.yaml 和幾個 Markdown 檔定義 agent，用普通的函式或現成的 MCP 伺服器給它工具，再從命令列、網頁或自己的程式裡和它對話。
  image:
    src: /logo.svg
    alt: Agent Harness
  actions:
    - theme: brand
      text: 快速開始
      link: /guide/quickstart
    - theme: alt
      text: 一行部署
      link: /guide/deploy
    - theme: alt
      text: GitHub
      link: https://github.com/xian-ai-1057/AgentHarness

features:
  - title: 不綁定模型
    details: TypeScript 版連接任何 OpenAI 相容端點，Python 版透過 litellm 直連各家供應商。換模型只要改設定。
    link: /guide/model
    linkText: 設定模型
  - title: 用檔案定義 agent
    details: harness.yaml 加上 agents/*.md，不寫程式就能調整行為，也方便放進版控與審查。
    link: /guide/project
    linkText: 專案格式
  - title: 工具來源不限
    details: Python 函式、TypeScript 函式、現成的 MCP 伺服器，以及參考 Claude Code 與 Codex 設計的內建工具。
    link: /guide/tools
    linkText: 撰寫工具
  - title: 安全的預設值
    details: 權限模式與 allow / deny / ask 規則；會寄信、改資料、花錢的工具，執行前先問使用者。Hooks 可以稽核、攔截或改寫。
    link: /guide/permissions
    linkText: 權限
  - title: 可接續的對話
    details: 只附加、不改寫的 JSONL 對話紀錄，TypeScript 與 Python 兩個版本互通。
    link: /guide/sessions
    linkText: 對話紀錄
  - title: 多種使用方式
    details: 命令列聊天、單次執行（排程與腳本）、網頁介面，或當成函式庫嵌進自己的服務。
    link: /guide/web
    linkText: 網頁版
  - title: 進階能力（Python 版）
    details: 多 agent 協作、知識庫（RAG）、skills、長期記憶、訊息防護、對話壓縮。
    link: /advanced/multi-agent
    linkText: 進階功能
  - title: 部署後就能用（Python 版）
    details: docker compose up -d 啟動，在網頁上填模型金鑰、上傳 Excel 或 PDF 請它分析；agent 寫的程式在隔離的 Docker 沙盒裡執行。
    link: /guide/deploy
    linkText: 快速部署
---

## 為誰而做

Agent Harness 和 [Hermes Agent](https://github.com/NousResearch/hermes-agent)、
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 屬於同一類工具，主要為三種使用者而做：

- **需要避開特定服務的組織。** 不依賴任何中國大陸的服務，也沒有內建的雲端服務。模型自己選：Claude、GPT、Gemini，或透過 Ollama 完全在本機執行。
- **想完全自己掌控的人。** 框架、網頁介面與範例都在 repo 裡。不需要註冊帳號、框架本身不收集使用資料，對話紀錄是存在專案資料夾的純文字檔，可以部署在內網。
- **想學 agent 怎麼運作的人。** TypeScript 版主迴圈約 300 行，執行時只有四個相依套件；兩種語言並排實作，註解與文件都是繁體中文。見[閱讀原始碼](/reference/source)。
