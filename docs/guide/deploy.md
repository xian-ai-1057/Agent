# 快速部署（Docker）<span class="badge-py">Python 版</span>

只想架起來給自己或團隊用、不寫任何設定時用這個方式。需要一台裝好 [Docker](https://docs.docker.com/engine/install/)（Docker 26、Compose 2.24 以上）的電腦。

```bash
git clone https://github.com/xian-ai-1057/AgentHarness.git
cd AgentHarness
docker compose up -d             # 第一次會建置映像檔，需要幾分鐘
docker compose logs harness      # 看網址與自動產生的登入密碼
```

接著：

1. **登入**：用瀏覽器打開 `http://<這台電腦的位址>:4321`，使用者名稱不限，密碼在上面的 log 裡。
2. **連接模型**：第一次會看到「先連接模型」，選供應商、貼上金鑰，按「測試連線並儲存」（見[在網頁上連接模型](/guide/web#在網頁上連接模型)）。
3. **開始對話**：可以直接上傳 Excel、PDF、CSV 請它分析。

## 自動完成的設定

| 項目 | 自動的做法 | 想自己指定時 |
|---|---|---|
| 專案 | 建立一個通用助手：讀寫檔案、列計畫、讀取網頁、記住交代的事、寫程式分析上傳的檔案 | 修改 volume 裡的 `harness.yaml`、`agents/main.md`、`AGENTS.md` |
| 登入密碼 | 第一次啟動時產生，之後每次啟動都用同一組 | `.env` 的 `HARNESS_WEB_PASSWORD` |
| 模型與金鑰 | 在網頁上填寫 | `.env` 的 `ANTHROPIC_API_KEY` 等 |
| 沙盒 | 一併建置裝好 pandas、matplotlib 等套件的映像檔 | 修改 `docker/sandbox.Dockerfile` |
| 埠號 | 4321 | `.env` 的 `HARNESS_PORT` |

要調整時，把 `.env.example` 複製成 `.env` 再修改，然後重新執行 `docker compose up -d`。

## 透過 proxy 連外

公司網路要透過 proxy 才能連外時，在 `.env` 寫上：

```bash
HTTP_PROXY=http://proxy.example.com:3128
HTTPS_PROXY=http://proxy.example.com:3128
NO_PROXY=localhost,127.0.0.1,.example.internal   # 不經過 proxy 的位址
```

建置映像檔（pip、apt）與執行時（呼叫模型、讀取網頁）都會使用，容器裡大小寫兩種環境變數都會設定。proxy 不會寫進映像檔。執行 `docker compose` 的終端機已經設定這些環境變數時，不寫 `.env` 也會沿用。內網的模型端點（例如自架的 LiteLLM Proxy）記得放進 `NO_PROXY`。

## 資料放在哪裡

專案、對話紀錄、上傳的檔案、記憶與金鑰都在名為 `harness-data` 的 Docker volume 裡，`docker compose down` 與重新建置都不會刪除它（`docker compose down -v` 才會）。

```bash
docker compose exec harness ls /assistant
docker compose restart harness   # 改了 harness.yaml 或 agent 之後重新載入
```

更新版本：`git pull && docker compose up -d --build`。

## 安全上要知道的事

::: warning
- **`docker-compose.yml` 把主機的 Docker socket 掛進容器**，沙盒才能啟動容器來執行程式。能使用這個 socket 等於擁有主機的 root 權限，所以請放在專用的機器或虛擬機上。不需要分析檔案時，把 `/var/run/docker.sock` 那一行刪掉再啟動（自動建立的助手就不會開啟沙盒）。
- **登入密碼會印在 log 裡**，看得到 `docker compose logs` 的人就能登入。
- **這個設定是 http，沒有加密。** 只在內網或 VPN 裡使用；要從網際網路連線，請在前面加上 HTTPS 的反向代理（見[部署到伺服器](/advanced/server)）。
- 所有登入的人共用對話與上傳的檔案，見[部署到伺服器](/advanced/server#安全上要知道的事)。
:::

## 不用 Docker

[安裝 Python 版](/guide/install#python-版)後，在一個空的資料夾執行 `pyharness web`，同樣會自動建立助手、在網頁上填金鑰（見[網頁版](/guide/web)）。
