# SKILL.md 範本

```markdown
---
name: monthly-report
description: 產生每月營運報告並寄給主管。使用者提到月報、營運報告、每月數字時使用
tools: [query_sales, send_mail]    # 選用：載入 skill 後才開放的工具或 toolset
---
# 每月營運報告

## 步驟

1. 問清楚要哪個月份；沒說的話用上個月。
2. 用 query_sales 取得當月與前一個月的數字，不要自己估算。
3. 依 report-format.md 的格式整理成報告。
4. 寄出前把報告內容與收件人給使用者確認。

## 注意

- 金額一律用新台幣、千分位。
- 數字與上個月差異超過 30% 時，在報告最上方標註並說明可能原因。
```

## frontmatter 欄位

| 欄位 | 必填 | 說明 |
|---|---|---|
| `name` | 否 | skill 名稱，預設用資料夾名稱 |
| `description` | 是 | 做什麼、什麼時候用；agent 靠它決定要不要載入 |
| `tools` | 否 | 載入後才開放的工具，可寫專案 `tools/` 裡的工具、toolset 名稱，或內建的 `files`、`shell`、`plan`、`web` |

## 資料夾結構

```
skills/
└── monthly-report/
    ├── SKILL.md            必要
    └── report-format.md    參考檔案（選用），agent 用 read_skill_file 讀取
```
