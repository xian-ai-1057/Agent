# 部署到伺服器 <span class="badge-py">Python 版</span>

把 pyharness 架在一台伺服器上，使用者用瀏覽器連線，自己的電腦不需要
安裝 Python。agent 寫的程式在伺服器的[沙盒](/advanced/sandbox)裡執行。

最省事的做法是用 Docker Compose，見[快速部署](/guide/deploy)。以下是
不用 Docker 執行 pyharness、自己安裝與設定的步驟，以及加上 HTTPS 的
方式（兩種做法都適用）。

## 1. 安裝

伺服器需要 Python 3.11 以上與 Docker。

```bash
sudo useradd --create-home harness      # 專用的帳號，不要用 root 執行
sudo usermod -aG docker harness         # 讓它能使用 docker（見下方的安全說明）
sudo -iu harness
python3 -m venv ~/venv
~/venv/bin/pip install "git+https://github.com/xian-ai-1057/AgentHarness.git#subdirectory=python"
```

接著把專案放到伺服器上，依[在沙盒裡執行程式](/advanced/sandbox#準備分析用的映像檔)建立沙盒的映像檔，並執行
`pyharness sandbox` 確認可以使用。

## 2. 設定登入密碼並啟動

```bash
export HARNESS_WEB_PASSWORD='一組夠長的隨機密碼'
export HARNESS_BASE_URL=http://litellm:4000      # 模型設定，見「模型設定」
export HARNESS_API_KEY=sk-...
export HARNESS_MODEL=claude
~/venv/bin/pyharness web -p /srv/my_bot --allowed-host ai.example.com
```

- **讓其他電腦連線時一定要有登入密碼。** 沒有設定
  `HARNESS_WEB_PASSWORD` 時，第一次啟動會自動產生一組並印在啟動訊息裡，
  存在專案的 `.harness/secrets/web-password`，之後每次啟動都用同一組。
  瀏覽器會跳出登入視窗：使用者名稱不限，密碼要正確。
- 模型的三個環境變數也可以不設定，啟動後
  [在網頁上連接模型](/guide/web#在網頁上連接模型)。
- `--allowed-host` 是使用者在瀏覽器網址列輸入的主機名稱（可以重複）。
  沒有指定時，只要密碼正確，用任何網址連線都接受。
- 密碼會以 HTTP Basic Auth 傳送，**一定要放在 HTTPS 後面**，不要讓瀏覽器
  直接用 http 連到伺服器。

## 3. 用反向代理加上 HTTPS

pyharness 只聽本機，由 nginx（或 Caddy 等）負責 HTTPS。以 nginx 為例：

```nginx
server {
    listen 443 ssl;
    server_name ai.example.com;
    # ssl_certificate ... 依你的憑證設定

    location / {
        proxy_pass http://127.0.0.1:4321;
        proxy_set_header Host $http_host;  # 一定要傳瀏覽器送來的 Host
        proxy_http_version 1.1;
        proxy_buffering off;               # 即時串流回覆（SSE）需要
        proxy_read_timeout 1h;
        client_max_body_size 1m;
    }
}
```

## 4. 用 systemd 讓它常駐

`/etc/systemd/system/harness.service`：

```ini
[Unit]
Description=Agent Harness
After=network-online.target docker.service

[Service]
User=harness
WorkingDirectory=/srv/my_bot
EnvironmentFile=/etc/harness.env
ExecStart=/home/harness/venv/bin/pyharness web --allowed-host ai.example.com
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

`/etc/harness.env` 放 `HARNESS_WEB_PASSWORD`、模型設定等環境變數，權限設成
只有 root 與 harness 能讀（`chmod 640`）。

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now harness
```

## 安全上要知道的事

- **能使用 docker 指令，等於擁有主機的 root 權限。** 請用專用帳號執行
  pyharness，不要在這個帳號放其他服務。要更嚴格的話可以改用 rootless
  Docker 或 Podman（`sandbox.command: podman`）。
- **所有登入的人共用同一組密碼、看得到同樣的對話**，也都可以在網頁上
  同意工具執行、修改權限與模型設定。目前沒有分帳號，適合給同一個團隊
  使用；要依使用者區分資料，請把 agent 嵌進自己的網頁服務（見
  [嵌進既有的網頁服務](/guide/embed#嵌進既有的網頁服務)）。
- **上傳的檔案所有登入的人都拿得到**（agent 可以讀 `uploads/` 裡的任何
  檔案），也不會自動刪除。敏感文件分析完請自行清理。
