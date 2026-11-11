# Skills <span class="badge-py">Python 版</span>

把一段任務指示（加上專屬的工具與參考檔案）打包成 skill。agent 平常只
看得到每個 skill 的名稱與一句說明，判斷用得到時才呼叫 `load_skill`
讀進完整指示，skill 的專屬工具也在這時才開放。agent 可以掛很多 skill，
又不會讓 system prompt 塞滿用不到的說明。

```
skills/
└── refund/
    ├── SKILL.md
    └── policy.md       參考檔案，agent 用 read_skill_file 讀取
```

`SKILL.md`：

```markdown
---
name: refund                           # 省略時用資料夾名稱
description: 處理退款申請，依退款政策判斷能不能退
tools: [find_order, issue_refund]      # 選用，tools/ 裡的工具或 toolset
---
1. 先用 find_order 查訂單，確認付款日期。
2. 讀 policy.md 判斷是否在可退款期間內。
3. 符合條件才呼叫 issue_refund。
```

- **`description` 必填**，agent 靠它決定什麼時候載入，寫清楚「什麼情況
  要用」。
- agent 設定沒寫 `skills` 欄位時可以使用全部 skill；用 `skills: [refund]`
  指定，`skills: []` 表示不用。
- skill 的專屬工具在 skill 載入前不會出現在 agent 的工具裡（即使 agent
  沒寫 `tools` 欄位）。想讓 agent 隨時都能用，就在 agent 的 `tools`
  明確列出。
- 載入狀態是從對話紀錄推算的，接續對話時已載入的 skill 仍然有效。

## 在程式裡使用

```python
refund = harness.Skill(
    name="refund",
    description="處理退款申請",
    instructions="1. 先用 find_order 查訂單……",
    tools=[issue_refund],
    root=pathlib.Path("skills/refund"),  # 有參考檔案時才需要
)
agent = harness.Agent(name="cs", instructions="...", skills=[refund])
```

## 內建 skills

框架附帶幾個常用的 skill，寫法參考 Claude 與 Codex 的 skills。在 agent
的 `skills` 欄位列出名稱就能使用（沒寫 `skills` 欄位時只會拿到專案自己
的 skill，不含內建的）：

| skill | 用途 | 載入後開放的工具 |
|---|---|---|
| `skill-creator` | 建立或改寫 skill，附 SKILL.md 範本 | `files` |
| `code-review` | 審查 git 變更，依嚴重程度回報問題，附檢查清單 | `files`、`shell` |
| `debugging` | 重現 → 縮小範圍 → 驗證假設 → 修正 → 確認 | `files`、`shell`、`plan` |
| `planning` | 把多步驟工作拆成計畫並持續更新進度 | `plan` |
| `web-research` | 查證網路資料，整理成附來源的回答 | `web` |
| `git-commit` | 檢查變更、撰寫 commit 訊息，經確認後 commit | `shell` |

```yaml
skills: [code-review, git-commit]
```

- 專案 `skills/` 裡有同名的 skill 時以專案的為準，所以想調整內建 skill
  的做法，可以把 `harness/builtin/skills/<名稱>/` 複製到專案的 `skills/`
  再修改（或請 agent 用 `skill-creator` 幫你改）。
- 內建 skill 的專屬工具一律來自內建 toolsets，而且照樣受權限設定管制
  （例如 `run_command` 仍會先問使用者）。
- 不使用專案資料夾時：`harness.builtin_skills()["code-review"]`。
