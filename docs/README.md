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

1. 在 Vercel 匯入這個 GitHub repo。
2. **Root Directory 設成 `docs`**。其他設定由 `docs/vercel.json` 提供（VitePress、`npm run build`、輸出到 `.vitepress/dist`、不帶 `.html` 的網址）。
3. 按 Deploy。之後推送到 main 會自動更新正式網址，每個 PR 也會有自己的預覽網址。

Vercel 預設在 Root Directory 以外的檔案變動時也會重新建置；只想在 `docs/` 有變動時才建置，可以在專案設定的 **Ignored Build Step** 填 `git diff --quiet HEAD^ HEAD -- .`。
