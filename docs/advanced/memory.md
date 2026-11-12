# 長期記憶 <span class="badge-py">Python 版</span>

使用者說「請記住……」時，agent 用 `remember` 工具存下來；之後每段新的
對話都會把記憶放進 system prompt。使用者要求忘記時用 `forget` 刪除。

在 `harness.yaml` 開啟：

```yaml
memory: true
```

記憶存在 `.harness/memory.json`，可以用命令列查看與管理（網頁版每個人的記憶分開，存在 `.harness/users/<識別碼>/memory.json`，見[帳號與成員](/guide/web#帳號與成員)）：

```bash
pyharness memory                 # 列出所有記憶
pyharness memory forget <識別碼>  # 刪除一則
pyharness memory clear           # 全部刪除
```

記憶屬於使用者，所以嵌進網頁服務時在呼叫 `run` 時傳入，每個使用者用
自己的檔案：

```python
store = harness.MemoryStore(f"data/memory/{user_id}.json")
await harness.run(agent, text, session=session, memory=store)
```

- 只有直接和使用者對話的 agent 會看到記憶，subagent 不會。
- 一則最多 500 字、最多 100 則；滿了 agent 會收到錯誤，請它先刪除舊的。
- 不傳 `memory` 就完全不使用記憶。
