/**
 * 連接模型：選供應商、貼上金鑰、確認模型名稱。
 *
 * 伺服器會先用新的設定實際連一次模型，連得上才儲存，所以儲存成功就
 * 表示可以開始使用。金鑰只送到伺服器，之後不會再顯示。
 */

import { type FormEvent, useState } from "react";
import {
  type ConnectionInfo,
  ENDPOINT_PROVIDER,
  type ProjectInfo,
} from "../../../src/web/protocol.ts";
import { api } from "../api.ts";
import { refreshProject } from "../store.ts";
import { CheckIcon } from "./Icons.tsx";

export function ConnectionForm({
  project,
  connection,
}: {
  project: ProjectInfo;
  connection: ConnectionInfo;
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

  const choose = (id: string) => {
    setProviderId(id);
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
        <label htmlFor="connection-model">模型名稱</label>
        <input
          id="connection-model"
          className="field mono"
          value={modelValue}
          readOnly={agentModel !== null}
          required
          onChange={(event) => setModel(event.target.value)}
        />
        <div className="help">
          {agentModel !== null
            ? `${project.defaultAgent} 的模型寫在 agent 檔案裡，要改請編輯那個檔案。`
            : endpoint
              ? "照端點上的名稱填寫，例如 LiteLLM Proxy 設定檔裡的 model_name。"
              : "已經填好常用的模型，通常不用改。"}
        </div>
      </div>

      <div className="connection-actions">
        <button
          type="submit"
          className="btn btn-accent"
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
