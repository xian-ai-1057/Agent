"""建立新專案的範本檔案。

兩種範本：
    create_project    `pyharness new`：給開發者的起點，附一個範例工具與
        各種設定的註解。
    create_assistant  `pyharness web` 指到空資料夾時自動建立：不用寫任何
        程式就能直接使用的通用助手。
"""

import pathlib

_CONFIG = """\
# 專案設定
name: {name}

# litellm 的模型名稱，例如 anthropic/claude-opus-5-5、openai/gpt-5、
# ollama/hermes3。不寫的話使用環境變數 HARNESS_MODEL。
# model: anthropic/claude-opus-5-5

defaultAgent: main

permissions:
  # ask: 標記 needs_approval 的工具要先問使用者
  # bypass: 全部直接執行
  # dontAsk: 不詢問，需要同意的工具一律拒絕
  mode: ask
  allow: []
  deny: []
  # 執行前要先詢問的工具名稱樣式，例如 ["send_*", "delete_*"]
  ask: []

# 連接 MCP 伺服器，伺服器名稱就是 agent 設定裡引用的 toolset 名稱。
# 需要 MCP 套件：pip install "mcp>=2"
# mcpServers:
#   github:        # 本機程式（stdio），會繼承目前所有的環境變數
#     command: npx
#     args: ["-y", "@modelcontextprotocol/server-github"]
#   search:        # 遠端伺服器（Streamable HTTP）
#     url: https://example.com/mcp

# 長期記憶：agent 可以跨對話記住使用者的偏好（用 pyharness memory 管理）
# memory: true

# 知識庫：把文件放進 knowledge/ 資料夾，再在 agent 設定 knowledge: true。
# 不設定 embeddingModel 時只用關鍵字檢索，不需要任何模型。
# knowledge:
#   path: knowledge
#   embeddingModel: openai/text-embedding-3-small
#   minSimilarity: 0.3            # 相似度低於這個值的片段不回傳
#   reranker: cohere/rerank-v3.5  # 用 rerank 模型重新排序前幾名
#   minRerankScore: 0.2           # rerank 分數低於這個值的片段不回傳

# 對話壓縮（預設開啟）：對話接近模型上限時，把較舊的歷史濃縮成檢查點。
# 用 litellm 查不到上限的模型（例如自架端點）要寫 contextWindow。
# compaction:
#   contextWindow: 128000
#   thresholdRatio: 0.8    # 超過上限的 80% 時開始壓縮
#   retainRatio: 0.15      # 原文保留最近約 15% 的對話

# 沙盒：讓 agent 寫 Python 程式解析文件、分析資料，在 Docker 容器裡執行。
# agent 要在 tools 列出 sandbox 才能使用；用 pyharness sandbox 檢查是否正常。
# sandbox:
#   image: python:3.12-slim   # 建議換成裝好 pandas 等套件的映像檔
#   workspace: workspace      # 掛載到容器 /workspace 的資料夾
#   timeoutSeconds: 60
#   network: false

# 訊息防護：在訊息交給模型前、回覆送出前檢查
# guardrails:
#   maskPii: true          # 遮蔽 Email、手機、身分證字號、信用卡號
#   blockInjection: true   # 擋下「忽略之前的指示」這類提示注入
#   blockedWords: []
"""

_AGENTS_MD = """\
# {name}

這裡寫整個專案共用的背景知識與規則，會接在每個 agent 的指示後面。
"""

_MAIN_AGENT = """\
---
name: main
description: 專案的主要助手
# tools 可以寫 toolset 名稱（tools/ 底下的檔名、mcpServers 的伺服器名稱）
# 或單一工具名稱；整個欄位不寫的話，可以使用 tools/ 與 mcpServers 的所有
# 工具。內建的 toolset 要明確列出才會啟用：files（讀取、搜尋、修改檔案）、
# shell（執行指令）、plan（列出計畫）、web（讀取網頁）、sandbox（在
# Docker 容器裡執行 Python，需要 harness.yaml 的 sandbox 設定）。
tools:
  - example
# 多個 agent 協作時（名稱是 agents/ 底下其他 agent 的 name）：
# subagents: [researcher]   # 把工作交給它，它做完只回傳結果
# handoffs: [billing]       # 把整段對話轉交給它，由它直接回覆使用者
# skills 是 skills/ 底下的資料夾名稱；整個欄位不寫的話可以使用全部 skill。
# 也可以列出內建的 skill：skill-creator、code-review、debugging、
# planning、web-research、git-commit。
# skills: [refund, code-review]
# 搜尋知識庫（knowledge/ 資料夾）；用 {labels: [hr]} 限定可搜尋的範圍。
# knowledge: true
---
你是 {name} 的助手。用使用者的語言回答，需要資料時就呼叫工具，不要
自己編造工具才能取得的內容。
"""

_EXAMPLE_TOOL = '''\
"""範例工具：把函式包成工具。

在這裡定義新的函式，或 import 現有的模組，再用 @harness.tool 包一層。
"""

import harness


@harness.tool
def word_count(text: str) -> dict[str, int]:
    """計算一段文字的字元數與行數。

    Args:
        text: 要計算的文字。
    """
    return {"characters": len(text), "lines": len(text.splitlines())}
'''

_GITIGNORE = """\
.harness/
__pycache__/
workspace/
"""

_FILES = {
    "harness.yaml": _CONFIG,
    "AGENTS.md": _AGENTS_MD,
    "agents/main.md": _MAIN_AGENT,
    "tools/example.py": _EXAMPLE_TOOL,
    ".gitignore": _GITIGNORE,
}


_ASSISTANT_CONFIG = """\
# 自動建立的通用助手。直接用就可以，不需要修改任何設定。
#
# 模型與金鑰在網頁的「設定 → 模型」填寫，也可以用環境變數提供
# （例如 ANTHROPIC_API_KEY、OPENAI_API_KEY）。
name: {name}

defaultAgent: main

permissions:
  # 寫檔、讀取網頁這類動作，執行前先在網頁上問使用者。
  mode: ask

# 長期記憶：使用者說「請記住……」時，之後的對話都會記得。
memory: true
{sandbox}"""

_SANDBOX_ON = """
# 沙盒：讓助手寫 Python 程式解析上傳的檔案、分析資料，在 Docker 容器裡
# 執行，沒有網路。上傳的檔案與產生的結果都在 workspace/。
sandbox: true
"""

_SANDBOX_OFF = """
# 沙盒：讓助手寫 Python 程式解析上傳的檔案、分析資料。建立這個專案時
# 這台電腦不能使用 Docker，所以沒有開啟。裝好 Docker 之後，把下面這行
# 與 agents/main.md 裡 tools 的 sandbox 取消註解，再重新啟動。
# sandbox: true
"""

_ASSISTANT_AGENTS_MD = """\
# {name}

這裡寫所有對話都要知道的背景知識與規則，例如公司的名稱、常用的術語、
回答時要遵守的規定。內容會接在助手的指示後面，改完重新啟動就生效。
"""

_ASSISTANT_AGENT = """\
---
name: main
description: 通用助手：回答問題、查資料、整理與分析檔案
tools:
  - files
  - plan
  - web
  {sandbox_tool}
skills:
  - planning
  - web-research
---
你是一位通用的工作助手。

- 用使用者的語言回答，先講結論，再補充必要的說明。
- 不確定的事情照實說，不要編造資料、數字或出處。
- 使用者上傳的檔案在 workspace/uploads/。
{file_hint}
- 需要網路上的資料時用 web_fetch 讀取網頁，並附上來源網址。
- 工作有好幾個步驟時，先用 update_plan 列出計畫，再一步一步完成。
- 產生的檔案（報告、圖表、整理好的資料）存在 workspace/ 裡，並告訴
  使用者檔名。
"""

_FILE_HINT_SANDBOX = """\
- 要解析 Excel、PDF、Word 或計算數字時，寫 Python 程式用 run_python
  執行，不要自己心算或猜測檔案內容。"""
_FILE_HINT_PLAIN = "- 文字檔用 read_file 讀取。"


def create_project(path: str | pathlib.Path) -> list[pathlib.Path]:
    """在指定資料夾建立專案範本，不會覆寫已存在的檔案。

    Args:
        path: 專案資料夾，不存在時會建立。

    Returns:
        這次實際建立的檔案路徑。
    """
    return _write_files(pathlib.Path(path).resolve(), _FILES)


def create_assistant(
    path: str | pathlib.Path, *, sandbox: bool = False
) -> list[pathlib.Path]:
    """建立不用寫程式就能直接使用的通用助手，不會覆寫已存在的檔案。

    助手可以讀寫專案裡的檔案、列計畫、讀取網頁，並記住使用者交代的事。

    Args:
        path: 專案資料夾，不存在時會建立。
        sandbox: 是否開啟沙盒，讓助手寫 Python 程式分析檔案（需要
            Docker）。

    Returns:
        這次實際建立的檔案路徑。
    """
    root = pathlib.Path(path).resolve()
    config = _ASSISTANT_CONFIG.replace(
        "{sandbox}", _SANDBOX_ON if sandbox else _SANDBOX_OFF
    )
    agent = _ASSISTANT_AGENT.replace(
        "{sandbox_tool}", "- sandbox" if sandbox else "# - sandbox"
    ).replace(
        "{file_hint}", _FILE_HINT_SANDBOX if sandbox else _FILE_HINT_PLAIN
    )
    created = _write_files(
        root,
        {
            "harness.yaml": config,
            "AGENTS.md": _ASSISTANT_AGENTS_MD,
            "agents/main.md": agent,
            ".gitignore": _GITIGNORE,
        },
    )
    (root / "workspace").mkdir(exist_ok=True)
    return created


def is_empty(path: str | pathlib.Path) -> bool:
    """資料夾不存在，或裡面沒有任何東西（隱藏檔不算）。

    Args:
        path: 要檢查的資料夾。

    Returns:
        可以放心在裡面建立新專案時為 True。
    """
    root = pathlib.Path(path)
    if not root.exists():
        return True
    if not root.is_dir():
        return False
    return all(item.name.startswith(".") for item in root.iterdir())


def _write_files(
    root: pathlib.Path, files: dict[str, str]
) -> list[pathlib.Path]:
    """寫入範本檔案，已存在的檔案保持不動。"""
    created = []
    for relative, template in files.items():
        target = root / relative
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        # 範本裡有程式碼的大括號，所以不用 str.format。
        content = template.replace("{name}", root.name)
        target.write_text(content, encoding="utf-8")
        created.append(target)
    return created
