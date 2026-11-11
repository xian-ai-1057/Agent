# 安裝

兩個版本可以同時安裝：TypeScript 版的指令是 `harness`，Python 版是 `pyharness`。不知道選哪個，見[兩個版本](/guide/introduction#兩個版本)。

只想架起網頁版直接使用，不需要安裝任何東西，見[快速部署](/guide/deploy)。

## TypeScript 版

需要 Node.js 22.18 以上。

```bash
git clone https://github.com/xian-ai-1057/AgentHarness.git
cd AgentHarness
npm install          # 安裝相依套件並編譯
npm install -g .     # 讓 harness 指令在任何資料夾都能用
```

之後要更新：在這個資料夾執行 `git pull && npm install`。

只想在某個 Node.js 專案裡當函式庫用的話，在該專案執行：

```bash
npm install github:xian-ai-1057/AgentHarness
```

## Python 版

需要 Python 3.11 以上。

```bash
pip install "git+https://github.com/xian-ai-1057/AgentHarness.git#subdirectory=python"
```

也可以用 `python -m harness` 執行。選用的功能：

| 功能 | 另外安裝 |
|---|---|
| 連接 MCP 伺服器 | `pip install "mcp>=2"` |
| 知識庫讀取 PDF | 安裝時加上 `[pdf]` |
| 加速語意檢索 | `pip install numpy` |
| 在沙盒裡執行程式 | [Docker](https://docs.docker.com/engine/install/) 或 Podman |

```bash
pip install "agent-harness[pdf] @ git+https://github.com/xian-ai-1057/AgentHarness.git#subdirectory=python"
```

## 開發這個框架本身

```bash
# TypeScript 版
npm test             # 測試
npm run lint         # 檢查格式
npm run typecheck    # 檢查型別

# Python 版
cd python
pip install -e ".[dev]"
pytest
```

這份文件網站的原始檔在 `docs/`：

```bash
cd docs
npm install
npm run dev          # http://localhost:5173
```
