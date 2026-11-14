# 知識庫（RAG） <span class="badge-py">Python 版</span>

讓 agent 從文件裡找答案並附上出處。把文件放進 `knowledge/` 資料夾，
再在 agent 設定 `knowledge: true`：

```
knowledge/
├── hr/
│   └── leave.md
├── product.md
└── manual.pdf        需要 pypdf（安裝時加上 [pdf]，見下方）
```

```markdown
---
name: faq
knowledge: true       # 這個 agent 可以用 search_knowledge 搜尋知識庫
---
你是公司的 FAQ 助手。回答前先搜尋知識庫，並註明出處。
```

agent 會得到 `search_knowledge` 工具，搜尋結果附上來源檔案與所在標題
（PDF 則是頁碼），例如 `[1] 來源：hr/leave.md › 請假規定 › 特休`。

- **檢索方式。** 預設用關鍵字比對（BM25），不需要任何模型，中文也能
  用（以單字與相鄰兩字比對，不用另外斷詞）。設定 `embeddingModel` 後
  再加上語意檢索，兩者的排名合併（hybrid search），同義詞、換句話說
  也找得到。
- **檔案改了會自動更新。** 每次搜尋前會檢查檔案有沒有變動，有就重建
  索引。embedding 存在內建的向量資料庫，沒變的片段不會重新計算（見
  [向量資料庫](#向量資料庫)）。
- **過濾不相關的結果與重新排序。** 可以設定相似度門檻，把與問題不夠
  相似的片段丟掉；也可以用 reranker 重新評估前幾名的相關度（見
  [相似度門檻與 reranker](#相似度門檻與-reranker)）。
- **切片。** 依段落切成約 `chunkSize` 字的片段，不會從段落中間切開。
- 支援 `.md`、`.txt`、`.pdf`；其他格式會略過，用 `pyharness knowledge`
  可以看到哪些檔案被略過。讀取 PDF 需要 pypdf：

  ```bash
  pip install "agent-harness[pdf] @ git+https://github.com/xian-ai-1057/AgentHarness.git#subdirectory=python"
  ```

`harness.yaml`（整個區塊可省略，有 `knowledge/` 資料夾就會啟用）：

```yaml
knowledge:
  path: knowledge                              # 預設
  embeddingModel: openai/text-embedding-3-small  # 省略時只用關鍵字
  chunkSize: 800                               # 預設
  minSimilarity: 0.3            # 選用，語意相似度門檻（需要 embeddingModel）
  reranker: cohere/rerank-v3.5  # 選用，重新排序用的模型
  rerankCandidates: 20          # 交給 reranker 的候選數，預設 20
  minRerankScore: 0.2           # 選用，rerank 分數門檻（需要 reranker）
```

`embeddingModel` 與 `reranker` 是 litellm 的模型名稱（上面的例子是直接
連供應商的寫法）。有設定 `HARNESS_BASE_URL` 時會改連那個端點，和聊天
模型一樣，名稱要寫端點上的名稱；用 LiteLLM Proxy 時就是它設定檔裡的
`model_name`（見[模型設定](/guide/model)）。reranker 走 Proxy 的
`/v1/rerank`。

## 相似度門檻與 reranker

只靠排名的話，知識庫裡就算沒有答案，也一定會回傳「最接近」的幾段，
agent 可能拿不相關的內容硬湊答案。兩種設定可以擋掉這些結果：

- **`minSimilarity`**：語意相似度（餘弦，-1 到 1）低於門檻的片段不回傳，
  關鍵字找到的片段也一樣要過門檻。需要設定 `embeddingModel`。
- **`reranker`**：先用關鍵字與語意檢索取出前 `rerankCandidates` 名，
  再交給 rerank 模型逐段判斷與問題的相關度、重新排序，排名比單純的
  向量相似度準確。可以搭配 **`minRerankScore`** 丟掉分數太低的片段。
  litellm 支援的 rerank 服務都能用（Cohere、Voyage、Jina、Together、
  自架的 Infinity / vLLM 等），例如 `cohere/rerank-v3.5`
  （環境變數 `COHERE_API_KEY`）。

全部被擋下時，agent 收到「知識庫裡沒有找到相關的內容」，會照指示回答
找不到資料，而不是自己編造。

**門檻的合適值依模型而定**（不同 embedding 模型的相似度分布差很多），
用 `pyharness knowledge "問題"` 試搜尋，結果會列出每筆的相似度與 rerank
分數，拿相關與不相關的問題各試幾次再決定：

```
$ pyharness knowledge "特休怎麼算"
3 個檔案、12 個片段，檢索方式：關鍵字 + 語意（embedding），再用 reranker 重新排序
門檻：相似度 ≥ 0.3、rerank ≥ 0.2

[1] 來源：hr/leave.md › 請假規定 › 特休（相似度 0.712、rerank 0.954）
到職滿六個月給三天特休……
```

## 向量資料庫

embedding 存在內建的向量資料庫裡，不需要另外安裝或架設服務：

- 專案的向量存在 `.harness/knowledge/vectors.sqlite3`（一個 SQLite 檔案），
  以「模型名稱 + 片段內容」識別。重新啟動、檔案部分改動時，只有新的
  或改過的片段需要重新計算 embedding；刪掉的檔案，它的向量也會一併
  清掉。舊版的 `embeddings.json` 會自動搬進來。
- 搜尋在記憶體中計算，有安裝 numpy 時用它加速（一萬個片段約 0.02 秒），
  沒有的話用純 Python（幾千個片段以內也很快）：

  ```bash
  pip install numpy
  ```

要改用外部的向量資料庫（pgvector、Qdrant 等），實作
`harness.VectorStore` 的四個方法（`missing`、`add`、`retain`、`search`），
在程式裡交給知識庫：

```python
project = harness.load_project(".")
project.knowledge.vector_store = MyPgVectorStore(...)
```

一個向量資料庫只給一個知識庫使用：`retain` 會刪除這個知識庫用不到的
向量。

## 依部門限制可以搜尋的文件

文件可以標上標籤。**有標籤的文件只有被允許該標籤的 agent 搜尋得到，
沒有標籤的文件所有人都搜尋得到。** 標籤有兩種寫法：

```markdown
---
labels: [hr]
---
# 請假規定
...
```

或在 `knowledge/labels.yaml` 用路徑樣式指定（PDF 只能用這種寫法）：

```yaml
"hr/*": [hr]
"finance/*.pdf": [finance]
```

限制某個 agent 只能搜尋特定標籤：

```markdown
---
name: hr_bot
knowledge:
  labels: [hr]        # 只搜尋得到 hr 與沒有標籤的文件
  topK: 5             # 每次最多回傳幾筆，預設 5
---
```

要依**登入的使用者**決定範圍（例如不同部門看到不同文件），在程式裡
設定 `label_filter`，它收到 `RunContext`，從 `deps` 取得使用者資訊：

```python
project = harness.load_project(".")
project.knowledge.label_filter = lambda context: context.deps["departments"]

await harness.run(agent, text, deps={"departments": ["hr"]})
```

agent 設定的 `labels` 與 `label_filter` 同時存在時，兩者都允許的標籤
才搜尋得到。

用網頁版（`pyharness web`）讓多人登入時不必寫程式：管理者在「管理平台 →
群組」為每個群組勾選可以搜尋的標籤，成員搜尋得到的是所屬群組開放的標籤，
再和 agent 自己的 `labels` 取交集（見[存取群組](/guide/web#存取群組)）。

## 在程式裡使用

```python
kb = harness.Knowledge(
    "knowledge",
    embedding_model="openai/text-embedding-3-small",  # 可省略
    cache_dir="data/kb-cache",  # 向量資料庫的位置，省略時只存在記憶體
    min_similarity=0.3,  # 可省略
    reranker="cohere/rerank-v3.5",  # 可省略
    min_rerank_score=0.2,  # 可省略
)
kb.add_text("退貨請在七天內申請", source="FAQ#3")  # 也可以直接加文字

agent = harness.Agent(name="faq", instructions="...", tools=[kb.as_tool()])

hits = await kb.search("退貨期限")  # 也可以直接搜尋
hits[0].similarity, hits[0].rerank_score  # 沒有使用時是 None
```

用自己的 reranker（例如本機的 cross-encoder）時，傳入
`rerank_fn=async (query, documents) -> list[float]`，分數越大越相關。
