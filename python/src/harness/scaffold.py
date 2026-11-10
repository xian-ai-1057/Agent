"""建立新專案的範本檔案（`pyharness new`）。"""

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

# 對話壓縮（預設開啟）：對話接近模型上限時，把較舊的歷史濃縮成檢查點。
# 用 litellm 查不到上限的模型（例如自架端點）要寫 contextWindow。
# compaction:
#   contextWindow: 128000
#   thresholdRatio: 0.8    # 超過上限的 80% 時開始壓縮
#   retainRatio: 0.15      # 原文保留最近約 15% 的對話

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
# tools 可以寫 toolset 名稱（tools/ 底下的檔名、mcpServers 的伺服器名稱、
# 內建的 files）或單一工具名稱；整個欄位不寫的話，可以使用專案裡所有的
# 工具。
tools:
  - example
# 多個 agent 協作時（名稱是 agents/ 底下其他 agent 的 name）：
# subagents: [researcher]   # 把工作交給它，它做完只回傳結果
# handoffs: [billing]       # 把整段對話轉交給它，由它直接回覆使用者
# skills 是 skills/ 底下的資料夾名稱；整個欄位不寫的話可以使用全部 skill。
# skills: [refund]
# 搜尋知識庫（knowledge/ 資料夾）；用 {labels: [hr]} 限定可搜尋的範圍。
# knowledge: true
---
你是 {name} 的助手。用使用者的語言回答，需要資料時就呼叫工具，不要
自己編造工具才能取得的內容。
"""

_EXAMPLE_TOOL = '''\
"""範例工具：把既有專案的函式包成工具。

實際移轉時，在這裡 import 既有專案的模組，再用 @harness.tool 包一層。
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
"""

_FILES = {
    "harness.yaml": _CONFIG,
    "AGENTS.md": _AGENTS_MD,
    "agents/main.md": _MAIN_AGENT,
    "tools/example.py": _EXAMPLE_TOOL,
    ".gitignore": _GITIGNORE,
}


def create_project(path: str | pathlib.Path) -> list[pathlib.Path]:
    """在指定資料夾建立專案範本，不會覆寫已存在的檔案。

    Args:
        path: 專案資料夾，不存在時會建立。

    Returns:
        這次實際建立的檔案路徑。
    """
    root = pathlib.Path(path).resolve()
    created = []
    for relative, template in _FILES.items():
        target = root / relative
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        # 範本裡有程式碼的大括號，所以不用 str.format。
        content = template.replace("{name}", root.name)
        target.write_text(content, encoding="utf-8")
        created.append(target)
    return created
