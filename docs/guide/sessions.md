# 對話紀錄

每段對話存成專案 `.harness/sessions/` 底下的一個 JSONL 檔，**只附加、不改寫**。命令列、網頁版和自己的程式都讀寫同一份紀錄。

## 接續對話

```bash
harness chat -c              # 接續最新的對話
harness chat -r <ID>         # 接續指定的對話
harness run -c "再補一張圖"   # run 也可以接續
```

網頁版左側的列表就是同一份紀錄，點開任何一段都可以接著問。

## 兩個版本互通

同一段對話可以先用 `harness` 開始，再用 `pyharness` 接著問，反過來也可以：

```bash
harness run --bypass "產生 2026-09 報告並存到 reports/sep.md"
pyharness run -c "上一個步驟你存了哪個檔案？"
# → 我存了 reports/sep.md。
```

Python 版[壓縮](/advanced/compaction)過的對話，TypeScript 版也讀得懂：壓縮與修剪都以額外的紀錄（`role: harness`）附加在檔案裡，原始訊息完整保留，模型看到的歷史是從紀錄推算出來的。

## 在程式裡使用

把同一個 session 傳給每次的 `run`，就會延續同一段對話。詳見[嵌進自己的程式](/guide/embed)。

::: code-group

```ts [TypeScript]
import { createSession, loadSession, run } from "agent-harness";

const session = createSession("data/sessions"); // 會存成 JSONL 檔
await run(agent, "給我十月的報告", { session });
await run(agent, "跟九月比呢？", { session });

// 之後（例如下一個 HTTP 請求）再接回來
const restored = loadSession("data/sessions", session.id);
```

```python [Python]
from harness import sessions

session = sessions.create("data/sessions")  # 會存成 JSONL 檔
await harness.run(agent, "給我十月的報告", session=session)
await harness.run(agent, "跟九月比呢？", session=session)

# 之後（例如下一個 HTTP 請求）再接回來
session = sessions.load("data/sessions", session.id)
```

:::

`.harness/` 是自動產生的資料夾，不用放進版控。
