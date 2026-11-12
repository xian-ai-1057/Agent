# 快速開始

先[安裝](/guide/install)並[設定模型](/guide/model)。

## Python 版

```bash
pyharness new my_bot        # 建立專案範本
cd my_bot

export ANTHROPIC_API_KEY=...                      # 你所用模型的金鑰
export HARNESS_MODEL=anthropic/claude-opus-5-5    # litellm 的模型名稱

pyharness chat              # 命令列聊天
pyharness web               # 或開網頁版（打開終端機顯示的網址）
```

## TypeScript 版

```bash
harness new my_bot              # Python 工具（預設）
harness new my_bot --lang ts    # 或 TypeScript 工具
cd my_bot
pip install "mcp>=2"            # 用 Python 寫工具時，工具伺服器需要這個套件
harness chat                    # 命令列聊天
harness web                     # 或開網頁版 http://127.0.0.1:4321
```

## 看一個完整的例子

`examples/report_bot` 把一個 Python 銷售報告程式包成三個工具，示範怎麼為現有的程式碼加上 agent。它同時提供兩種工具寫法，兩個版本都能直接執行：

```bash
cd examples/report_bot
pip install -r requirements.txt      # TypeScript 版需要（MCP 套件）
harness run "資料裡有哪些月份？最新那個月的總營收是多少？"
```

```text
  ⚙ list_months({})
  ⚙ get_sales_summary({"month":"2026-10"})
根據查詢結果：
- 筆電：150,000 元
- 螢幕：30,000 元
- 鍵盤：12,000 元
- 合計：192,000 元
```

用 Python 版的話，把 `harness` 換成 `pyharness`（不需要安裝 MCP 套件）。

## 接下來

- [專案格式](/guide/project)：`harness.yaml` 與 agent 檔案的寫法。
- [撰寫工具](/guide/tools)：把自己的函式交給 agent。
- [為既有專案加上 agent](/guide/migration)：已經有一套程式碼時的做法。
