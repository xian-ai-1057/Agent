# 文件網站

Agent Harness 的說明文件，用 [VitePress](https://vitepress.dev/) 建置成多頁網站。

```bash
cd docs
npm install
npm run dev        # 本機預覽 http://localhost:5173
npm run build      # 輸出到 .vitepress/dist
npm run preview    # 預覽建置結果
```

## 結構

| 位置 | 內容 |
|---|---|
| `index.md` | 首頁 |
| `guide/` | 開始與使用（兩個版本共通） |
| `advanced/` | 只有 Python 版才有的進階功能 |
| `reference/` | 版本比較、閱讀原始碼、目前的限制 |
| `public/` | 圖片與 logo，網址從 `/` 開始，例如 `/images/web-chat.jpg` |
| `.vitepress/config.mts` | 網站設定、上方選單與左側目錄 |
| `.vitepress/theme/` | 品牌色與自訂樣式 |

新增頁面後，記得在 `.vitepress/config.mts` 的 `sidebar` 加上連結。只有 Python 版才有的功能，在標題後面加上 `<span class="badge-py">Python 版</span>`；有這個標記的標題要另外指定錨點，例如 `## 上傳檔案 <span class="badge-py">Python 版</span> {#上傳檔案}`，其他頁面才連得到。

建置時會檢查站內連結，連到不存在的頁面會讓建置失敗。

## 部署到 Vercel

Vercel 專案的 **Root Directory 設成 `docs`**，只建置這個資料夾，不會碰到 repo 其他部分。其他設定寫在 `docs/vercel.json`（VitePress、`npm run build`、輸出到 `.vitepress/dist`、不帶 `.html` 的網址）。

- 推送到 main 會自動更新正式網址，每個 PR 也會有自己的預覽網址。
- 只有 `docs/` 有變動時才會重新建置；其他的 commit 會被略過。

網址改變時，記得一併更新根目錄 `README.md`、`python/README.md`、`Dockerfile` 與 `docker-compose.yml` 裡的文件網址。
