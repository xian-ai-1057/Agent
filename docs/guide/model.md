# 設定模型

## 連 OpenAI 相容的端點（兩個版本）

用三個環境變數決定連到哪裡，兩個版本共用：

| 環境變數 | 用途 |
|---|---|
| `HARNESS_BASE_URL` | 端點網址 |
| `HARNESS_API_KEY` | 端點的金鑰（本機端點可以不設） |
| `HARNESS_MODEL` | 模型名稱，寫法由端點決定，會原樣送給端點 |

| 端點 | `HARNESS_BASE_URL` | `HARNESS_MODEL` 範例 |
|---|---|---|
| Anthropic | `https://api.anthropic.com/v1` | `claude-sonnet-5-5` |
| OpenAI | 不用設（設 `HARNESS_API_KEY` 即可） | `gpt-5` |
| OpenRouter | `https://openrouter.ai/api/v1` | `nousresearch/hermes-4-405b` |
| Ollama（本機） | `http://localhost:11434/v1` | `hermes3` |

模型名稱是範例，實際可用的名稱以各端點為準。

### 用 LiteLLM Proxy 接任何模型

LiteLLM Proxy 是一個獨立的小服務，把各家模型轉成同一種介面：

```bash
pip install 'litellm[proxy]'
```

```yaml
# litellm_config.yaml
model_list:
  - model_name: claude            # 你在 harness 裡用的名稱
    litellm_params:
      model: anthropic/claude-opus-5-5
      api_key: os.environ/ANTHROPIC_API_KEY
```

```bash
export ANTHROPIC_API_KEY=...
export LITELLM_MASTER_KEY="sk-$(openssl rand -hex 32)"   # Proxy 要求一定要設
litellm --config litellm_config.yaml --port 4000
```

另一個終端機：

```bash
export HARNESS_BASE_URL=http://localhost:4000   # 結尾有沒有 /v1 都可以
export HARNESS_API_KEY=...                      # 上面那個 LITELLM_MASTER_KEY 的值
export HARNESS_MODEL=claude
```

## 直接連模型供應商 <span class="badge-py">Python 版</span> {#直接連模型供應商}

Python 版不設 `HARNESS_BASE_URL` 時，用 litellm 直接連供應商。模型名稱用 `供應商/模型` 的格式，金鑰用該供應商的環境變數：

| 模型 | 名稱範例 | 環境變數 |
|---|---|---|
| Claude | `anthropic/claude-opus-5-5` | `ANTHROPIC_API_KEY` |
| OpenAI | `openai/gpt-5` | `OPENAI_API_KEY` |
| Gemini | `gemini/gemini-2.5-pro` | `GEMINI_API_KEY` |
| OpenRouter（含 Hermes） | `openrouter/nousresearch/hermes-4-405b` | `OPENROUTER_API_KEY` |
| Ollama 本地模型 | `ollama_chat/hermes3` | 不需要 |

實際可用的模型名稱以各供應商與 [litellm 的文件](https://docs.litellm.ai/docs/providers)為準。

**只設定一把金鑰就能用**：沒有指定模型時，使用第一家已經設定金鑰的供應商的預設模型（依上表由上到下：Anthropic、OpenAI、Gemini、OpenRouter）。

Python 版的網頁也可以直接選供應商並貼上金鑰，不需要設定環境變數，見[在網頁上連接模型](/guide/web#在網頁上連接模型)。

## 決定用哪個模型的順序

1. 命令列的 `-m`
2. agent 檔案的 `model`
3. `harness.yaml` 的 `model`
4. 環境變數 `HARNESS_MODEL`
5. （Python 版）依已設定的金鑰挑選，最後是 `anthropic/claude-opus-5-5`

## 其他參數

要傳給模型的其他參數寫在 `modelParams`（agent 檔案或 `harness.yaml` 皆可），會原樣放進請求：

```yaml
modelParams:
  max_tokens: 8000
  reasoning_effort: high
```
