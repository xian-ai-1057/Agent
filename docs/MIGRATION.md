# 把既有專案移轉到 Harness

目標：原本的程式碼幾乎不動，只在外面加一層，讓 agent 可以透過工具
使用它的功能。

完整的例子在 [`examples/report_bot`](../examples/report_bot)：
`legacy/` 是「原本就存在」的程式碼，`tools/report.py` 是新加的那一層。

## 移轉後的樣子

把 Harness 的檔案直接放在既有專案的根目錄：

```
my_project/                 ← 既有專案
├── my_project/             既有的程式碼（不用改）
│   ├── reports.py
│   └── db.py
├── harness.yaml            ← 新增
├── AGENTS.md               ← 新增
├── agents/main.md          ← 新增
└── tools/reports.py        ← 新增：把既有函式包成工具
```

專案根目錄會自動加入 Python 的搜尋路徑，所以 `tools/` 裡的檔案可以
直接 `import my_project`。

## 步驟

### 1. 安裝並建立範本

```bash
cd my_project
pip install git+https://github.com/xian-ai-1057/Agent.git
harness new .
```

`harness new .` 只會建立不存在的檔案，不會覆寫任何既有檔案。建立後
可以刪掉 `tools/example.py`。

### 2. 列出專案「能做的事」

把專案的功能列成清單，每一項就是一個工具的候選。以報告產生器為例：

| 使用者會說的話 | 需要的功能 | 既有的函式 |
|---|---|---|
| 「有哪些月份的資料？」 | 列出月份 | （要新寫幾行） |
| 「十月賣了多少？」 | 取得加總 | `load_sales` + `summarize` |
| 「幫我出十月的報告」 | 產生報告 | `render_markdown` |

從使用者的問題出發，而不是從既有函式出發。一個工具應該對應一件使用者
在意的事，內部可以串接好幾個既有函式。

### 3. 把功能包成工具

```python
# tools/reports.py
import harness
from my_project import reports


@harness.tool
def get_sales_summary(month: str) -> dict[str, int]:
    """取得某個月份各產品的銷售總額。

    Args:
        month: 月份，格式為 YYYY-MM，例如 2026-10。
    """
    records = reports.load_sales(month)
    if not records:
        raise ValueError(f"{month} 沒有銷售資料，請先用 list_months 確認")
    return reports.summarize(records)
```

既有函式的簽名和 docstring 已經夠清楚時，可以不寫包裝函式，直接包：

```python
build_report = harness.tool(reports.build_report)
```

### 4. 寫 agent 的指示

把原本寫死在程式裡的 prompt 搬到 `agents/main.md`。原本用程式控制的
流程（先查資料、再判斷、再產生報告）改用文字描述，讓模型自己決定
呼叫順序：

```markdown
---
name: main
tools: [reports]
---
你是銷售報告助手。

- 需要數字時一定要呼叫工具取得，不要自己估算。
- 使用者沒說月份時，先用 list_months 確認有哪些月份再詢問。
```

整個專案共用的背景知識（名詞定義、單位、格式慣例）寫在 `AGENTS.md`。

### 5. 測試

```bash
harness chat
```

工具本身是普通的 Python 函式，可以直接用 pytest 測試。要測試「agent
加工具」的整體行為而不花錢呼叫模型，可以用假的模型函式，寫法見
[`tests/test_example_report_bot.py`](../tests/test_example_report_bot.py)。

## 設計工具的原則

**說明要具體。** 模型只看得到工具名稱、docstring 和參數說明。格式
（`YYYY-MM`）、單位、範例值都寫進去。

**錯誤訊息要告訴模型下一步。** `raise ValueError("2026-13 沒有資料，
請先用 list_months 確認")` 比 `raise ValueError("invalid")` 有用得多，
模型會照著做。

**回傳模型需要的資料就好。** 回傳內容會佔用模型的 context。查詢類工具
回傳整理過的摘要或前幾筆，不要回傳整張資料表。

**有副作用的工具要標記。** 會寄信、寫檔、改資料庫、花錢的工具加上
`needs_approval=True`。

**使用者身分不要讓模型填。** 目前的使用者、權限、資料庫連線，從
`RunContext.deps` 取得，不要做成工具的參數，否則模型可以填別人的 ID。

## 嵌進既有的網頁服務

既有專案已經有網頁介面（FastAPI、Streamlit、Flask）時，不需要用 CLI，
直接在請求處理函式裡呼叫：

```python
import harness
from harness import sessions

project = harness.load_project(".")
agent = project.agents["main"]


async def handle_chat(user_id: str, session_id: str | None, text: str):
    if session_id:
        session = sessions.load(project.sessions_dir, session_id)
    else:
        session = sessions.create(project.sessions_dir)
    async for event in harness.run_stream(
        agent,
        text,
        session=session,
        deps={"user_id": user_id, "db": get_db()},
        permissions=harness.Permissions(mode="dont_ask"),
    ):
        if event.type == "text_delta":
            yield event.data["text"]
```

網頁服務沒辦法在終端機詢問使用者，所以這裡用 `dont_ask`：需要同意的
工具會被拒絕。要讓網頁使用者可以按「同意」，就提供自己的
`Permissions(approver=...)`，在裡面等待前端的回應。

## 檢查清單

- [ ] `harness.yaml`、`agents/main.md` 已建立
- [ ] 每個使用者會用到的功能都有對應的工具
- [ ] 每個工具都有具體的 docstring 與參數說明
- [ ] 有副作用的工具都標了 `needs_approval=True`
- [ ] 使用者身分從 `RunContext.deps` 取得，不是工具參數
- [ ] 原本寫死的 prompt 已搬到 `agents/*.md`
- [ ] `.harness/` 已加入 `.gitignore`
- [ ] `harness chat` 實際問過幾個常見問題
