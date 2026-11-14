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

## 2. 設定管理者密碼並啟動

```bash
export HARNESS_WEB_PASSWORD='一組夠長的隨機密碼'
export HARNESS_BASE_URL=http://litellm:4000      # 模型設定，見「模型設定」
export HARNESS_API_KEY=sk-...
export HARNESS_MODEL=claude
~/venv/bin/pyharness web -p /srv/my_bot --allowed-host ai.example.com \
    --trusted-proxy 127.0.0.1
```

- **讓其他電腦連線時一定要登入。** 第一次啟動時自動建立管理者帳號
  `admin`，密碼是 `HARNESS_WEB_PASSWORD`；沒有設定時自動產生一組並印在
  啟動訊息裡，存在專案的 `.harness/secrets/web-password`。之後每次啟動
  都會再顯示，直到 admin 自己改了密碼。從舊版（只有一組密碼）升級時，
  原本的密碼就是 admin 的密碼。
- 環境變數只在建立 admin 時使用一次；之後改密碼請在網頁的帳號選單，
  或用 `pyharness user passwd admin`。
- 登入後由管理者在「管理平台 → 成員」（帳號選單 → 管理平台）或用 `pyharness user add` 新增成員，
  每個人用自己的帳號登入，對話、檔案與記憶各自分開（見
  [帳號與成員](/guide/web#帳號與成員)、[工作資料夾](/guide/web#工作資料夾)）。
- 要讓不同成員用不同的 agent、工具或知識庫，在「管理平台 → 群組」
  設定（見[存取群組](/guide/web#存取群組)）。模型、預算與限流交給
  LiteLLM 這類模型中台，harness 會在每個呼叫帶上使用者（見
  [模型中台](/guide/web#模型中台)）。
- 登入、成員與群組變更、設定與工具核准都記在只能新增的稽核紀錄
  （見[稽核紀錄](/guide/web#稽核紀錄)），請定期匯出保存。
- 模型的三個環境變數也可以不設定，啟動後
  [在網頁上連接模型](/guide/web#在網頁上連接模型)（只有管理者能在[管理平台](/guide/web#管理平台)設定）。
- `--allowed-host` 是使用者在瀏覽器網址列輸入的主機名稱（可以重複）。
  沒有指定時，用任何網址連線都接受（還是要登入）。
- `--trusted-proxy` 是反向代理連到 pyharness 的位址（IP 或網段，例如
  `127.0.0.1`、`10.0.0.0/8`，可以重複；也可以用環境變數
  `HARNESS_TRUSTED_PROXIES`，逗號分隔）。只有從這些位址連進來的請求，
  才採信 `X-Forwarded-For`（取最右邊不是反向代理的那一站當成使用者的
  IP，登入限制依它計算）與 `X-Forwarded-Proto`。沒有指定時這兩個標頭
  一律不採信：所有人共用反向代理的 IP，cookie 也不會加上 Secure。
- 登入的 cookie 是 HttpOnly、SameSite=Strict；經過 HTTPS（或信任的反向
  代理送來 `X-Forwarded-Proto: https`）時另外加上 Secure。**一定要放在
  HTTPS 後面**，不要讓瀏覽器直接用 http 連到伺服器傳送密碼。
- 沒有設定密碼、也沒有任何帳號時是「本機模式」（不必登入）。這時伺服器
  拒絕所有帶反向代理標頭（`X-Forwarded-For`、`Forwarded`、
  `X-Forwarded-Host`、`Via`）或不是從本機連進來的請求，所以忘了設定
  密碼就接上 nginx 時，只會看到「請設定 HARNESS_WEB_PASSWORD」的錯誤，
  不會把管理者權限開放出去。另外每次啟動產生一把網址鑰匙（終端機顯示
  的 `/?key=…`），沒有鑰匙的請求一律拒絕，連不加任何標頭的轉送
  （`proxy_pass` 預設設定、`ssh -L`、`kubectl port-forward`）也用不了。

## 3. 用反向代理加上 HTTPS

pyharness 只聽本機，由 nginx（或 Caddy 等）負責 HTTPS；啟動時用
`--trusted-proxy 127.0.0.1` 讓 pyharness 採信 nginx 送來的
`X-Forwarded-For` 與 `X-Forwarded-Proto`。以 nginx 為例：

```nginx
server {
    listen 443 ssl;
    server_name ai.example.com;
    # ssl_certificate ... 依你的憑證設定

    location / {
        proxy_pass http://127.0.0.1:4321;
        proxy_set_header Host $http_host;  # 一定要傳瀏覽器送來的 Host
        proxy_set_header X-Forwarded-Proto $scheme;  # 讓登入的 cookie 加上 Secure
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;  # 使用者的 IP
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
ExecStart=/home/harness/venv/bin/pyharness web --allowed-host ai.example.com --trusted-proxy 127.0.0.1
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

`/etc/harness.env` 放 `HARNESS_WEB_PASSWORD`（第一次啟動時建立 admin 用）、
模型設定等環境變數，權限設成只有 root 與 harness 能讀（`chmod 640`）。

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now harness
```

## 安全上要知道的事

- **能使用 docker 指令，等於擁有主機的 root 權限。** 請用專用帳號執行
  pyharness，不要在這個帳號放其他服務。要更嚴格的話可以改用 rootless
  Docker 或 Podman（`sandbox.command: podman`）。
- **這是單機模式**：帳號存在專案的 `.harness/harness.db`（SQLite，密碼用
  scrypt 加上各自的 salt，登入 token 只存雜湊），資料放在本機的檔案系統。
  每個動作都記得是誰做的、每段對話都有擁有者，存取檢查集中在伺服器的
  一層裡；身分（`AccountStore`）與資料夾權限（`Folders`）各自包成一個小
  介面，之後的企業版可以換成 OIDC 單一登入與 PostgreSQL。
- **成員看不到別人的對話與「我的檔案」**，也不能修改模型連線、全站權限
  與成員；他們「以後都允許」的工具只適用於自己。成員的 `run_command`
  一律在沙盒裡執行（沒有網路、只掛載工作資料夾），沒有沙盒時不提供，
  指令不會在伺服器上替成員執行。
- **管理者的 `run_command` 在自己的「我的檔案」裡仍然在伺服器上執行**
  （`cwd` 限制在工作資料夾，但指令本身可以做任何事，讀得到金鑰與其他人
  的資料）。在共用資料夾裡改在沙盒執行，因為成員可以在那裡放檔案，
  誘使看似無害的指令執行成員的程式。不需要時別讓 agent 使用 `shell`；
  企業版會把管理者也全部移進沙盒。
- **共用資料夾裡的檔案，所有能使用它的人都拿得到**（agent 可以讀裡面的
  任何檔案），上傳的檔案也不會自動刪除。敏感文件請放在自己的「我的
  檔案」，分析完自行清理。預設的「共用」資料夾是 `shared/`；從單人版
  升級時，舊的 `workspace/` 只有第一位管理者能在「舊資料（專案資料夾）」
  裡看到（見[從舊版升級](/guide/web#帳號與成員)）。
- **沙盒裡的程式可以在工作資料夾裡建立捷徑（symlink）**，所以伺服器存取
  工作資料夾時一律不跟著捷徑走：檔案工具、檔案面板與上傳都從資料夾
  開始一層一層用 `O_NOFOLLOW` 打開，路徑上有捷徑就拒絕。
- **`web_fetch` 不能讀內部網路**（本機、私有網路、link-local 與雲端主機的
  中繼資料服務 `169.254.169.254`、IPv6 unique-local、CGNAT），每次轉址
  都重新檢查。管理者確實需要時，在 `harness.yaml` 寫
  `web: {allowPrivateNetworks: true}`，只對管理者自己的對話生效。
- **停用成員或改變角色會立刻生效**：那個人正在執行的對話停下來，開著
  的網頁不再收到更新（SSE 每次心跳都重新確認登入）；工具的權限判斷
  每次都讀最新的角色。
- 登入失敗的限制只記在記憶體裡，重新啟動後歸零：同一個帳號名稱連續錯
  5 次後（只算同一個 IP），每次再錯都要等 2 秒起跳、加倍、最多 60 秒；
  同一個 IP 10 分鐘內最多錯 50 次；同一個帳號名稱 10 分鐘內所有 IP 加起來
  最多錯 200 次；同時送出的登入也算數，同時最多 4 個登入在驗證密碼。
  別人從其他地方亂試你的帳號擋不到你。放在網際網路上時，請另外在反向
  代理加上流量限制，並設定 `--trusted-proxy`。
- **web_fetch 的內網防護**檢查每一次連線實際解析到的位址（包括中文網域
  轉成的 `xn--` 名稱與 NAT64 這類內含 IPv4 的 IPv6 位址）。`harness.yaml`
  設定 `sandbox: {network: true}` 時，沙盒裡的程式可以連到任何地方，不受
  這個防護限制。
