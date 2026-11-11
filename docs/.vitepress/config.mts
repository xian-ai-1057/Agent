import { defineConfig } from "vitepress";

const repo = "https://github.com/xian-ai-1057/AgentHarness";

/**
 * 把中文切成相鄰兩字（bigram），英數字照空白與標點切開。
 *
 * 內建搜尋（MiniSearch）只用空白與標點分詞，一整句中文會變成一個詞，
 * 搜尋「沙盒」找不到「在沙盒裡執行程式」。切成兩字一組後，建立索引與
 * 搜尋時用同一種切法，就能找到句子中間的詞。
 */
function tokenize(text: string): string[] {
  const tokens: string[] = [];
  for (const part of text.split(/[\s\p{P}\p{S}]+/u)) {
    if (!part) continue;
    for (const run of part.match(/\p{Script=Han}+|[^\p{Script=Han}]+/gu) ??
      []) {
      if (!/\p{Script=Han}/u.test(run)) {
        tokens.push(run);
      } else if (run.length === 1) {
        tokens.push(run);
      } else {
        for (let i = 0; i < run.length - 1; i++)
          tokens.push(run.slice(i, i + 2));
      }
    }
  }
  return tokens;
}

export default defineConfig({
  lang: "zh-Hant",
  title: "Agent Harness",
  description:
    "完全開源、不綁定模型供應商的 agent harness。用幾個檔案定義 agent，其餘交給框架。",
  cleanUrls: true,
  srcExclude: ["README.md"],
  lastUpdated: true,
  head: [["link", { rel: "icon", href: "/logo.svg", type: "image/svg+xml" }]],

  themeConfig: {
    logo: "/logo.svg",
    nav: [
      { text: "指南", link: "/guide/introduction", activeMatch: "/guide/" },
      {
        text: "進階功能",
        link: "/advanced/multi-agent",
        activeMatch: "/advanced/",
      },
      { text: "參考", link: "/reference/versions", activeMatch: "/reference/" },
    ],

    sidebar: [
      {
        text: "開始",
        items: [
          { text: "簡介", link: "/guide/introduction" },
          { text: "快速部署（Docker）", link: "/guide/deploy" },
          { text: "安裝", link: "/guide/install" },
          { text: "快速開始", link: "/guide/quickstart" },
          { text: "設定模型", link: "/guide/model" },
        ],
      },
      {
        text: "使用",
        items: [
          { text: "專案格式", link: "/guide/project" },
          { text: "撰寫工具", link: "/guide/tools" },
          { text: "連接 MCP 伺服器", link: "/guide/mcp" },
          { text: "內建工具", link: "/guide/builtin-tools" },
          { text: "權限", link: "/guide/permissions" },
          { text: "Hooks", link: "/guide/hooks" },
          { text: "對話紀錄", link: "/guide/sessions" },
          { text: "命令列", link: "/guide/cli" },
          { text: "網頁版", link: "/guide/web" },
          { text: "嵌進自己的程式", link: "/guide/embed" },
          { text: "為既有專案加上 agent", link: "/guide/migration" },
        ],
      },
      {
        text: "進階功能（Python 版）",
        items: [
          { text: "多個 agent 協作", link: "/advanced/multi-agent" },
          { text: "知識庫（RAG）", link: "/advanced/knowledge" },
          { text: "Skills", link: "/advanced/skills" },
          { text: "長期記憶", link: "/advanced/memory" },
          { text: "訊息防護", link: "/advanced/guardrails" },
          { text: "對話壓縮", link: "/advanced/compaction" },
          { text: "在沙盒裡執行程式", link: "/advanced/sandbox" },
          { text: "部署到伺服器", link: "/advanced/server" },
        ],
      },
      {
        text: "參考",
        items: [
          { text: "兩個版本的比較", link: "/reference/versions" },
          { text: "閱讀原始碼", link: "/reference/source" },
          { text: "目前的限制", link: "/reference/limits" },
        ],
      },
    ],

    socialLinks: [{ icon: "github", link: repo }],

    editLink: {
      pattern: `${repo}/edit/main/docs/:path`,
      text: "在 GitHub 上修改這一頁",
    },

    search: {
      provider: "local",
      options: {
        miniSearch: {
          options: { tokenize },
          searchOptions: { tokenize, combineWith: "AND", fuzzy: false },
        },
        translations: {
          button: { buttonText: "搜尋", buttonAriaLabel: "搜尋文件" },
          modal: {
            displayDetails: "顯示詳細清單",
            resetButtonTitle: "清除",
            backButtonTitle: "關閉搜尋",
            noResultsText: "找不到相關的結果",
            footer: {
              selectText: "選擇",
              navigateText: "切換",
              closeText: "關閉",
            },
          },
        },
      },
    },

    outline: { level: [2, 3], label: "本頁內容" },
    docFooter: { prev: "上一頁", next: "下一頁" },
    lastUpdated: { text: "最後更新" },
    returnToTopLabel: "回到頂端",
    sidebarMenuLabel: "目錄",
    darkModeSwitchLabel: "深淺色",
    lightModeSwitchTitle: "切換到淺色",
    darkModeSwitchTitle: "切換到深色",
    langMenuLabel: "語言",
    notFound: {
      title: "找不到這一頁",
      quote: "網址可能打錯了，或這一頁已經搬到別的地方。",
      linkText: "回到首頁",
    },

    footer: {
      message: "完全開源 · 不綁定模型供應商",
      copyright: `<a href="${repo}">GitHub</a>`,
    },
  },
});
