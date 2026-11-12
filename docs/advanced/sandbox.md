# 在沙盒裡執行程式 <span class="badge-py">Python 版</span>

解析文件、計算與分析資料時，固定的做法可以寫成工具（見[寫工具](/guide/tools)），
但事先無法預期的分析需要 agent 自己寫程式。這些程式由模型產生，所以
不會直接在主機上執行，而是每次放進一個拋棄式的 Docker 容器。

需要先安裝 [Docker](https://docs.docker.com/engine/install/)（或 Podman）。
執行 pyharness 的使用者要能使用 docker 指令。

`harness.yaml`：

```yaml
sandbox:
  image: harness-sandbox        # 容器映像檔，預設 python:3.12-slim
  workspace: workspace          # 掛載到容器 /workspace 的資料夾，預設 workspace
  timeoutSeconds: 60            # 單次執行的時間上限，預設 60
  memory: 1g                    # 記憶體上限，預設 1g
  cpus: 1                       # 可用的 CPU 數，預設 1
  network: false                # 是否允許連網，預設 false
  command: docker               # 或 podman
  notes: 已安裝 pandas、numpy、openpyxl、pdfplumber、python-docx、python-pptx、matplotlib。
```

全部用預設值的話寫 `sandbox: true`。和內建的 `files` 一樣，agent 要在
`tools` 明確列出才能使用：

```markdown
---
name: analyst
description: 解析文件並分析資料
tools: [sandbox, files]
---
你是資料分析師。需要計算或解析檔案時，寫 Python 程式用 run_python 執行，
不要自己心算。圖表存成 PNG 放在工作目錄。
```

agent 會得到 `run_python` 工具：傳入一段 Python 程式，回傳它印出的內容；
程式出錯時，模型會看到錯誤訊息並自己修正。`notes` 會附在工具說明後面，
讓模型知道有哪些套件可以用。

## 產生的檔案 {#產生的檔案}

每次執行前後會比對工作目錄，新增或修改的檔案以 `[產生的檔案] ` 開頭的行
接在工具結果後面，路徑相對於專案根目錄（網頁版是相對於這段對話的
[工作資料夾](/guide/web#工作資料夾)，每一輪只掛載那個資料夾；程式出錯或
逾時也一樣，部分結果常常還用得上）：

```text
平均營收 1,234,567

[產生的檔案] workspace/charts/revenue.png
[產生的檔案] workspace/summary.csv
```

最新的排前面，最多列 20 個；以點開頭的檔案與資料夾不列，工作目錄很大時
只比對前 5,000 個項目。網頁版用這幾行顯示檔案卡片與縮圖，模型也看得到
它們，可以直接用這個路徑告訴使用者。

## 隔離方式

- 每次執行都是全新的容器，執行完就刪除，變數不會保留到下一次（要沿用
  的結果寫成檔案）。
- 預設**沒有網路**：文件內容不會被送出去，也無法下載東西或安裝套件。
- 限制執行時間、記憶體、CPU 與行程數。超過時間會停止容器，使用者在
  網頁上按停止時也一樣。
- 容器的系統目錄唯讀，拿掉所有 Linux capabilities，並以執行 pyharness
  的使用者身分（不是 root）執行，產生的檔案也屬於這個使用者。
- **只掛載 `workspace` 一個資料夾**，可讀寫。要分析的文件放在這裡，
  程式產生的圖表、CSV 也存在這裡。`workspace` 不能設成專案根目錄，也
  不能放在 `tools/`、`agents/`、`skills/`、`.harness/` 裡，否則容器裡的
  程式可以改寫這些檔案，進而在主機上執行程式。
- stdout、stderr 各自最多保留 20,000 字，太長時只留開頭與結尾。

## 準備分析用的映像檔

預設的 `python:3.12-slim` 只有標準函式庫，通常要另外做一個裝好套件的
映像檔。例如 `sandbox.Dockerfile`：

```dockerfile
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-noto-cjk \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir \
      pandas numpy openpyxl pdfplumber python-docx python-pptx matplotlib
# 圖表預設用中文字型，否則中文會變成方框
RUN printf 'font.family: Noto Sans CJK JP\naxes.unicode_minus: False\n' \
      > /etc/matplotlibrc
ENV MATPLOTLIBRC=/etc/matplotlibrc
```

```bash
docker build -t harness-sandbox -f sandbox.Dockerfile .
pyharness sandbox     # 檢查 Docker、映像檔與限制是否正常
```

`pyharness sandbox` 會在沙盒裡跑一段檢查程式，列出 Python 版本、裝了
哪些常用套件、網路是否關閉、工作目錄能不能寫入。

用 [Docker Compose 部署](/guide/deploy)時這些都已經做好：映像檔來自
`docker/sandbox.Dockerfile`，並用三個環境變數提供預設值（`harness.yaml`
有寫的話以它為準）：

| 環境變數 | 用途 |
|---|---|
| `HARNESS_SANDBOX_IMAGE` | 沒寫 `sandbox.image` 時使用的映像檔 |
| `HARNESS_SANDBOX_NOTES` | 沒寫 `sandbox.notes` 時附在工具說明後面的文字 |
| `HARNESS_SANDBOX_VOLUME` | pyharness 自己也跑在容器裡時，專案所在的 Docker volume 名稱。沙盒改成只掛載這個 volume 裡的工作目錄（需要 Docker 26 以上） |

## 在程式裡使用

```python
box = harness.Sandbox(
    workspace=pathlib.Path("data/analysis"),
    image="harness-sandbox",
    notes="已安裝 pandas。",
)
agent = harness.Agent(name="analyst", instructions="...", tools=box.tools())
```
