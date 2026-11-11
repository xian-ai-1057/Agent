---
name: git-commit
description: 檢查目前的 git 變更並撰寫清楚的 commit 訊息，經使用者確認後 commit。使用者要求 commit、寫 commit message、整理變更時使用
tools: [shell]
---
# 撰寫 commit

## 步驟

1. **看變更。** 用 `run_command` 執行：
   - `git status --short`
   - `git diff --staged`（已暫存的變更）與 `git diff`（尚未暫存的變更）
   - `git log --oneline -n 10`（參考專案既有的 commit 訊息風格與語言）
2. **確認範圍。** 只 commit 和這次工作有關的檔案。看到不相關的變更、
   暫存檔、含有金鑰或密碼的檔案（例如 `.env`）時，先問使用者，不要
   一起加進去。變更包含好幾件不相關的事時，建議拆成多個 commit。
3. **寫訊息**（格式見下方）。
4. **給使用者確認。** 列出要加入的檔案與完整的 commit 訊息，等使用者
   同意後再執行 `git add <檔案>` 與 `git commit`。不要用 `git add -A`
   一次加入所有檔案。
5. **回報結果。** 顯示 `git log --oneline -n 1`。除非使用者要求，否則
   不要 push，也不要改寫已經存在的 commit（`--amend`、`rebase`）。

## commit 訊息格式

```
用一句話說明這次變更做了什麼（50 字以內，不加句號）

說明為什麼要做這個變更、解決了什麼問題，以及讀程式碼看不出來的
取捨或注意事項。每行約 72 字元換行。不需要逐一列出改了哪些檔案，
那是 diff 的工作。
```

- 標題用祈使句描述效果，例如「修正退款金額超過訂單總額的問題」，
  不要寫「修改了 refund.py」。
- 專案有固定格式（例如 Conventional Commits 的 `fix:`、`feat:`）或
  慣用語言時，沿用專案的寫法。
- 用 heredoc 傳入多行訊息，避免引號問題：

```
git commit -F - <<'MSG'
標題

內文
MSG
```

## 不要做的事

- 不要用 `--no-verify` 跳過 hooks；hook 失敗時修正問題後重新 commit。
- 不要 commit 測試沒有通過、或還有除錯用 print 的程式碼，除非使用者
  明確要求。
