# 命令列

TypeScript 版的指令是 `harness`，Python 版是 `pyharness`，共用的指令與選項相同。

```text
harness new <資料夾> [--lang python|ts]   建立新專案
harness chat                              開始聊天
harness run "<訊息>"                      執行一次後結束（適合排程與腳本）
harness web                               開啟網頁版

chat 與 run 共用的選項：
  -p, --project <資料夾>     專案位置（預設為目前資料夾）
  -a, --agent <名稱>         使用哪個 agent
  -m, --model <名稱>         覆寫模型
  -c, --continue             接續最新的對話
  -r, --resume <ID>          接續指定的對話
      --bypass               所有工具都不詢問直接執行

web 的選項：
  -p, --project <資料夾>     專案位置（預設為目前資料夾）
      --port <埠號>          預設 4321
      --host <位址>          預設 127.0.0.1（只有本機能連）
```

聊天時按 Ctrl-C 會中斷目前這一輪，不會結束整個聊天。

## Python 版多出的指令

```text
pyharness run -c /compact    壓縮最新的對話（聊天時直接輸入 /compact）
pyharness memory             查看長期記憶（forget <識別碼> / clear）
pyharness knowledge ["查詢"]  檢查知識庫索引，或試著搜尋
                             （-l hr,sales 模擬受限的 agent）
pyharness sandbox            檢查沙盒（Docker）能不能正常執行程式
```

`pyharness web` 另外有這些選項：

| 選項 | 用途 |
|---|---|
| `--allowed-host <主機名稱>` | 只接受用這個網址連線，可以重複（見[部署到伺服器](/advanced/server)） |
| `--max-upload-mb <MB>` | 單一上傳檔案的上限，預設 50 |

在空的資料夾執行 `pyharness web` 時，會自動建立可以直接使用的助手（見[網頁版](/guide/web#自動建立助手)）。

相關說明：[對話壓縮](/advanced/compaction)、[長期記憶](/advanced/memory)、[知識庫](/advanced/knowledge)、[在沙盒裡執行程式](/advanced/sandbox)。

## 在排程裡使用

`run` 執行一次就結束，適合放進 cron 或 CI。無人值守時沒有人可以回答權限詢問，可以把權限模式設成 `dontAsk`（需要同意的工具一律拒絕），或確定安全時加上 `--bypass`。見[權限](/guide/permissions)。
