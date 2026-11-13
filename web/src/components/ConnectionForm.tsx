/**
 * 連接模型：選供應商、貼上金鑰、確認模型名稱。
 *
 * 伺服器會先用新的設定實際連一次模型，連得上才儲存，所以儲存成功就
 * 表示可以開始使用。金鑰只送到伺服器，之後不會再顯示。
 *
 * 伺服器支援時（connection.canDiscover，Python 版），填好端點網址或金鑰
 * 後自動列出這家供應商（或端點）的模型，從清單選；也可以勾選哪些模型
 * 在對話中可以切換（harness.yaml 的 models）。
 */

import { type FormEvent, useEffect, useState } from "react";
import {
  type ConnectionInfo,
  ENDPOINT_PROVIDER,
  type ModelChoice,
  type ProjectInfo,
} from "../../../src/web/protocol.ts";
import { api } from "../api.ts";
import { refreshProject } from "../store.ts";
import { CheckIcon } from "./Icons.tsx";

export function ConnectionForm({
  project,
  connection,
  secondary = false,
}: {
  project: ProjectInfo;
  connection: ConnectionInfo;
  /** 畫面上已經有另一個主要（紅色）按鈕時，送出按鈕改用外框樣式。 */
  secondary?: boolean;
}) {
  const { env } = project.model;
  const agent = project.agents.find((a) => a.name === project.defaultAgent);
  // 模型寫在 agent 檔案裡時，這裡改不到它，只能設定金鑰。
  const agentModel = agent?.modelSource === "agent" ? agent.model : null;
  const [providerId, setProviderId] = useState(
    connection.provider ?? connection.providers[0]?.id ?? ENDPOINT_PROVIDER,
  );
  const [model, setModel] = useState<string>();
  const [apiKey, setApiKey] = useState("");
  const [baseURL, setBaseURL] = useState<string>();
  const [state, setState] = useState<"idle" | "saving" | "saved">("idle");
  const [error, setError] = useState<string>();
  const [found, setFound] = useState<Discovery>({ state: "idle" });
  const [manual, setManual] = useState(false);
  // undefined：沒有改過，沿用 harness.yaml 的 models。
  const [choices, setChoices] = useState<string[]>();

  const provider = connection.providers.find((p) => p.id === providerId);
  const endpoint = providerId === ENDPOINT_PROVIDER;
  const isCurrent = providerId === connection.provider;
  const currentModel = isCurrent ? (agent?.model ?? "") : "";
  const modelValue =
    agentModel ?? model ?? (currentModel || provider?.defaultModel || "");
  const savedURL = endpoint ? env.baseURL : (provider?.baseURL ?? null);
  const showURL = endpoint || provider?.baseURL != null;
  const keySet = endpoint ? env.apiKeySet : (provider?.keySet ?? false);
  const needsKey = endpoint ? false : (provider?.needsKey ?? false);
  const showKey = endpoint || needsKey;
  const savedChoices = connection.choices ?? [];
  const chosen = choices ?? savedChoices;

  // 填好網址或金鑰後自動列出模型；打字時等一下再查，不要每個字都查。
  const urlValue = baseURL ?? savedURL ?? "";
  const canList =
    connection.canDiscover === true &&
    (endpoint ? Boolean(urlValue) : !needsKey || Boolean(apiKey) || keySet);
  useEffect(() => {
    if (!canList) {
      setFound({ state: "idle" });
      return;
    }
    let alive = true;
    const typed = apiKey !== "" || baseURL !== undefined;
    const timer = setTimeout(
      () => {
        setFound({ state: "loading" });
        api
          .discoverModels({
            provider: providerId,
            apiKey,
            baseURL: baseURL ?? "",
          })
          .then(({ models }) => {
            if (alive) setFound({ state: "done", models });
          })
          .catch((failure: Error) => {
            if (alive) setFound({ state: "error", message: failure.message });
          });
      },
      typed ? 600 : 0,
    );
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [canList, providerId, apiKey, baseURL]);

  const models = found.state === "done" ? found.models : [];
  const picking = models.length > 0 && !manual && agentModel === null;

  const choose = (id: string) => {
    setProviderId(id);
    setManual(false);
    setModel(undefined);
    setBaseURL(undefined);
    setApiKey("");
    setError(undefined);
    setState("idle");
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setState("saving");
    setError(undefined);
    try {
      await api.saveConnection({
        provider: providerId,
        apiKey,
        baseURL: baseURL ?? "",
        model: modelValue,
      });
      if (choices && !sameList(choices, savedChoices)) {
        await api.saveModelChoices(choices);
        setChoices(undefined);
      }
      await refreshProject();
      setApiKey("");
      setState("saved");
    } catch (failure) {
      setError((failure as Error).message);
      setState("idle");
    }
  };

  return (
    <form className="connection-form" onSubmit={submit}>
      <div className="form-row">
        <label htmlFor="connection-provider">模型供應商</label>
        <select
          id="connection-provider"
          className="field"
          value={providerId}
          onChange={(event) => choose(event.target.value)}
        >
          {connection.providers.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
          <option value={ENDPOINT_PROVIDER}>
            OpenAI 相容端點（LiteLLM Proxy、vLLM…）
          </option>
        </select>
      </div>

      {showURL && (
        <div className="form-row">
          <label htmlFor="connection-url">
            {endpoint ? "端點網址" : "伺服器位址"}
          </label>
          <input
            id="connection-url"
            className="field mono"
            type="url"
            value={baseURL ?? savedURL ?? ""}
            placeholder={endpoint ? "http://litellm:4000" : ""}
            required={endpoint}
            onChange={(event) => setBaseURL(event.target.value)}
          />
          {!endpoint && (
            <div className="help">
              Ollama 所在的位址。Harness 跑在 Docker 裡、Ollama
              在同一台主機上時，通常是{" "}
              <code>http://host.docker.internal:11434</code>。
            </div>
          )}
        </div>
      )}

      {showKey && (
        <div className="form-row">
          <label htmlFor="connection-key">
            金鑰（API key）
            {keySet && <span className="badge badge-muted">已設定</span>}
          </label>
          <input
            id="connection-key"
            className="field mono"
            type="password"
            autoComplete="off"
            value={apiKey}
            placeholder={
              keySet
                ? "留空表示沿用目前的金鑰"
                : endpoint
                  ? "端點不需要金鑰時可以留空"
                  : "貼上金鑰"
            }
            required={needsKey && !keySet}
            onChange={(event) => setApiKey(event.target.value)}
          />
          <div className="help">金鑰只存在這台伺服器上，儲存後不會再顯示。</div>
        </div>
      )}

      <div className="form-row">
        <label htmlFor="connection-model">模型</label>
        {picking ? (
          <select
            id="connection-model"
            className="field mono"
            value={modelValue}
            onChange={(event) => {
              if (event.target.value === MANUAL) setManual(true);
              else setModel(event.target.value);
            }}
          >
            {modelValue && !models.some((m) => m.name === modelValue) && (
              <option value={modelValue}>{modelValue}（目前設定）</option>
            )}
            {models.map((item) => (
              <option key={item.name} value={item.name}>
                {optionLabel(item)}
              </option>
            ))}
            <option value={MANUAL}>其他（自行輸入模型名稱）…</option>
          </select>
        ) : (
          <input
            id="connection-model"
            className="field mono"
            value={modelValue}
            readOnly={agentModel !== null}
            required
            onChange={(event) => setModel(event.target.value)}
          />
        )}
        <div className="help" aria-live="polite">
          {agentModel !== null
            ? `${project.defaultAgent} 的模型寫在 agent 檔案裡，要改請編輯那個檔案。`
            : discoveryNote(found, canList, endpoint, manual)}
          {manual && models.length > 0 && (
            <>
              {" "}
              <button
                type="button"
                className="link-btn"
                onClick={() => setManual(false)}
              >
                從清單選
              </button>
            </>
          )}
        </div>
      </div>

      {models.length > 0 && (
        <ModelChoices models={models} chosen={chosen} onChange={setChoices} />
      )}

      <div className="connection-actions">
        <button
          type="submit"
          className={`btn ${secondary ? "btn-outline" : "btn-accent"}`}
          disabled={state === "saving"}
        >
          {state === "saving" ? "正在測試連線…" : "測試連線並儲存"}
        </button>
        {state === "saved" && (
          <span className="ok-text anim-in" role="status">
            <CheckIcon size={14} />
            已連線並儲存
          </span>
        )}
        {error && (
          <span className="inline-error" role="alert">
            {error}
          </span>
        )}
      </div>
    </form>
  );
}

const MANUAL = "__manual__";

type Discovery =
  | { state: "idle" }
  | { state: "loading" }
  | { state: "done"; models: ModelChoice[] }
  | { state: "error"; message: string };

function optionLabel(item: ModelChoice): string {
  return item.label && item.label !== item.name
    ? `${item.label}（${item.name}）`
    : item.name;
}

function sameList(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((item, index) => item === b[index]);
}

function discoveryNote(
  found: Discovery,
  canList: boolean,
  endpoint: boolean,
  manual: boolean,
): string {
  if (found.state === "loading") return "正在列出可以用的模型…";
  if (found.state === "error") {
    return `無法自動列出模型：${found.message}。可以直接填模型名稱。`;
  }
  if (found.state === "done") {
    if (found.models.length === 0)
      return "這裡沒有列出任何模型，請直接填模型名稱。";
    return manual
      ? "照供應商或端點上的名稱填寫。"
      : `找到 ${found.models.length} 個模型，選一個當作預設模型。`;
  }
  if (!canList) {
    return endpoint
      ? "填好端點網址後會自動列出可以用的模型；也可以照端點上的名稱直接填寫。"
      : "填好金鑰後會自動列出可以用的模型。";
  }
  return endpoint
    ? "照端點上的名稱填寫，例如 LiteLLM Proxy 設定檔裡的 model_name。"
    : "已經填好常用的模型，通常不用改。";
}

/**
 * 勾選對話中可以切換的模型（harness.yaml 的 models）。預設模型一定可以
 * 選，不必勾。清單太長時可以搜尋。
 */
function ModelChoices({
  models,
  chosen,
  onChange,
}: {
  models: ModelChoice[];
  chosen: string[];
  onChange: (next: string[]) => void;
}) {
  const [query, setQuery] = useState("");
  const known = new Set(models.map((item) => item.name));
  // 已經開放、但這次沒有列出來的模型也列著，才能取消勾選。
  const extra = chosen
    .filter((name) => !known.has(name))
    .map((name) => ({ name, label: name }));
  const all = [...extra, ...models];
  const term = query.trim().toLowerCase();
  const shown = term
    ? all.filter(
        (item) =>
          item.name.toLowerCase().includes(term) ||
          item.label.toLowerCase().includes(term),
      )
    : all;
  const toggle = (name: string) =>
    onChange(
      chosen.includes(name)
        ? chosen.filter((item) => item !== name)
        : [...chosen, name],
    );

  return (
    <fieldset className="form-row model-choices">
      <legend>對話中可以切換的模型</legend>
      <div className="help">
        {chosen.length === 0
          ? "目前都沒有勾選：輸入框的模型選單會列出這個接口的所有模型。勾選後只列出勾選的模型（預設模型一定會列出）。"
          : `輸入框的模型選單只列出勾選的 ${chosen.length} 個模型和預設模型；全部取消勾選就會列出這個接口的所有模型。`}
      </div>
      {all.length > 12 && (
        <input
          className="field model-choices-search"
          type="search"
          value={query}
          placeholder="搜尋模型"
          aria-label="搜尋模型"
          onChange={(event) => setQuery(event.target.value)}
        />
      )}
      <ul className="model-choices-list">
        {shown.map((item) => (
          <li key={item.name}>
            <label className="model-choice">
              <input
                type="checkbox"
                checked={chosen.includes(item.name)}
                onChange={() => toggle(item.name)}
              />
              <span className="model-choice-label">{item.label}</span>
              {item.label !== item.name && (
                <span className="model-choice-name mono">{item.name}</span>
              )}
            </label>
          </li>
        ))}
      </ul>
    </fieldset>
  );
}
