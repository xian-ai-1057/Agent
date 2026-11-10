/**
 * 設定：模型、權限、工具、agent。
 *
 * 改動先放在草稿裡，按「儲存」才寫回 harness.yaml。端點網址與金鑰來自
 * 環境變數，這裡只顯示狀態。
 */

import { type FormEvent, useEffect, useState } from "react";
import type {
  ModelTestResult,
  PermissionMode,
  PermissionSettings,
  ProjectInfo,
  SettingSource,
} from "../../../src/web/protocol.ts";
import { api } from "../api.ts";
import { MODE_LABELS } from "../format.ts";
import type { SettingsSection } from "../router.ts";
import { refreshProject, useStore } from "../store.ts";
import { CheckIcon, CloseIcon } from "./Icons.tsx";
import { Link } from "./Sidebar.tsx";

interface Draft {
  model: string;
  maxTurns: string;
  permissions: PermissionSettings;
}

type RuleKind = "allow" | "ask" | "deny";

const SECTIONS: [SettingsSection, string][] = [
  ["model", "模型"],
  ["permissions", "權限"],
  ["tools", "工具"],
  ["agents", "Agent"],
];

const MODE_DESCRIPTIONS: Record<PermissionMode, string> = {
  ask: "需要同意的工具，執行前先問你。預設。",
  bypass: "不詢問，所有工具直接執行。只在信任的環境用。",
  dontAsk: "需要同意的工具一律拒絕，適合無人值守的排程。",
};

const RULES: { kind: RuleKind; label: string }[] = [
  { kind: "allow", label: "允許" },
  { kind: "ask", label: "先問我" },
  { kind: "deny", label: "拒絕" },
];

export function Settings({ section }: { section: SettingsSection }) {
  const project = useStore((s) => s.project);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    document.title = "設定 · Harness";
  }, []);

  useEffect(() => {
    if (!saved) return;
    const timer = setTimeout(() => setSaved(false), 2400);
    return () => clearTimeout(timer);
  }, [saved]);

  if (!project) return <main className="main" aria-busy="true" />;

  const base = toDraft(project);
  const current = draft ?? base;
  const modelDirty =
    current.model !== base.model || current.maxTurns !== base.maxTurns;
  const permissionsDirty =
    JSON.stringify(current.permissions) !== JSON.stringify(base.permissions);
  const dirty = modelDirty || permissionsDirty;

  const edit = (change: (value: Draft) => Draft) => {
    setDraft(change(structuredClone(current)));
    setError(undefined);
  };

  const save = async () => {
    const maxTurns = current.maxTurns.trim() ? Number(current.maxTurns) : null;
    if (maxTurns !== null && !(Number.isInteger(maxTurns) && maxTurns > 0)) {
      setError("每次最多回合必須是正整數");
      return;
    }
    setSaving(true);
    try {
      if (modelDirty) {
        await api.saveModel(current.model.trim() || null, maxTurns);
      }
      if (permissionsDirty) await api.savePermissions(current.permissions);
      await refreshProject();
      setDraft(null);
      setSaved(true);
    } catch (failure) {
      setError((failure as Error).message);
    }
    setSaving(false);
  };

  return (
    <main className="main">
      <header className="main-header">
        <div className="main-title">
          <h1>設定</h1>
          <span className="subtle mono">{project.name} / harness.yaml</span>
        </div>
      </header>
      <div className="scroll">
        <div className="settings">
          <nav className="settings-nav" aria-label="設定分類">
            {SECTIONS.map(([id, label]) => (
              <Link key={id} to={`/settings/${id}`} current={id === section}>
                <span className="bar" />
                {label}
              </Link>
            ))}
          </nav>
          <div className="settings-body anim-in" key={section}>
            {section === "model" && (
              <ModelSection project={project} draft={current} edit={edit} />
            )}
            {section === "permissions" && (
              <PermissionSection
                project={project}
                draft={current}
                edit={edit}
              />
            )}
            {section === "tools" && <ToolSection project={project} />}
            {section === "agents" && <AgentSection project={project} />}
          </div>
        </div>
      </div>

      {dirty && (
        <section className="save-bar" aria-label="未儲存的變更">
          <span>{error ?? "harness.yaml 有未儲存的變更"}</span>
          <div style={{ display: "flex", gap: 6 }}>
            <button
              type="button"
              className="btn btn-ghost"
              style={{ color: "#fff" }}
              onClick={() => {
                setDraft(null);
                setError(undefined);
              }}
            >
              捨棄
            </button>
            <button
              type="button"
              className="btn btn-light"
              disabled={saving}
              onClick={save}
            >
              儲存
            </button>
          </div>
        </section>
      )}
      {saved && !dirty && (
        <div className="toast" role="status">
          <CheckIcon size={14} />
          已儲存到 harness.yaml
        </div>
      )}
    </main>
  );
}

function toDraft(project: ProjectInfo): Draft {
  const { config } = project.model;
  return {
    model: config.model ?? "",
    maxTurns: config.maxTurns === null ? "" : String(config.maxTurns),
    permissions: structuredClone(project.permissions),
  };
}

type EditProps = {
  project: ProjectInfo;
  draft: Draft;
  edit: (change: (value: Draft) => Draft) => void;
};

function ModelSection({ project, draft, edit }: EditProps) {
  const [test, setTest] = useState<ModelTestResult | "testing">();
  const agent = project.agents.find((a) => a.name === project.defaultAgent);
  const { env } = project.model;
  const source = draft.model.trim()
    ? "config"
    : agent?.modelSource === "agent"
      ? "agent"
      : env.model
        ? "env"
        : "default";

  const runTest = async () => {
    setTest("testing");
    setTest(
      await api
        .testModel()
        .catch((error: Error) => ({ ok: false, message: error.message })),
    );
  };

  return (
    <section aria-labelledby="h-model" className="settings-body">
      <div>
        <h2 id="h-model">模型</h2>
        <p className="lead">
          連接任何 OpenAI 相容的端點，例如 LiteLLM Proxy、OpenRouter、Ollama。
          換模型只要改這裡。
        </p>
      </div>

      <div className="form-row">
        <label htmlFor="model-name">模型名稱</label>
        <input
          id="model-name"
          className="field mono"
          value={draft.model}
          placeholder={env.model ?? "端點認得的模型名稱"}
          onChange={(event) =>
            edit((d) => ({ ...d, model: event.target.value }))
          }
        />
        <div className="chain">
          <span>讀取順序：</span>
          <span className={`link ${source === "agent" ? "on" : ""}`}>
            agent 的 model
          </span>
          <span aria-hidden="true">→</span>
          <span className={`link ${source === "config" ? "on" : ""}`}>
            harness.yaml
          </span>
          <span aria-hidden="true">→</span>
          <span className={`link ${source === "env" ? "on" : ""}`}>
            HARNESS_MODEL
          </span>
        </div>
        <div className="help">
          {source === "default"
            ? "還沒有設定模型：在這裡填寫，或設定環境變數 HARNESS_MODEL。"
            : `${project.defaultAgent} 目前使用：${
                source === "config" ? draft.model.trim() : (agent?.model ?? "")
              }（${SOURCE_LABELS[source]}）`}
        </div>
      </div>

      <div className="form-row">
        <label htmlFor="base-url">端點網址</label>
        <input
          id="base-url"
          className="field mono"
          readOnly
          value={env.baseURL ?? "未設定（使用 OpenAI 官方端點）"}
        />
        <div className="help">
          來自環境變數 <code>HARNESS_BASE_URL</code>
          ，要改的話請設定後重新啟動 harness web。
        </div>
      </div>

      <div className="box box-row">
        <div>
          <div style={{ fontWeight: 600 }}>金鑰</div>
          <div className="help" style={{ marginTop: 4 }}>
            {env.apiKeySet
              ? "已設定 · 從 HARNESS_API_KEY 讀取，這裡不會顯示內容"
              : "沒有設定 HARNESS_API_KEY（本機端點通常不需要）"}
          </div>
        </div>
        <span
          className={`badge ${env.apiKeySet || env.baseURL ? "badge-muted" : "badge-danger"}`}
        >
          {env.apiKeySet ? "已設定" : "未設定"}
        </span>
      </div>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 12,
          alignItems: "center",
        }}
      >
        <button
          type="button"
          className="btn btn-dark"
          disabled={test === "testing"}
          onClick={runTest}
        >
          {test === "testing" && (
            <span
              className="spin"
              style={{
                width: 12,
                height: 12,
                border: "2px solid rgba(255,255,255,.35)",
                borderTopColor: "#fff",
                borderRadius: "50%",
              }}
            />
          )}
          測試連線
        </button>
        {test && test !== "testing" && (
          <span
            className={`anim-in ${test.ok ? "ok-text" : "inline-error"}`}
            role="status"
          >
            {test.ok ? (
              <>
                <CheckIcon size={14} />
                已連線，{test.model} 有回應
              </>
            ) : (
              test.message
            )}
          </span>
        )}
      </div>
      <div className="help">測試使用已儲存的設定。</div>

      <div
        className="form-row"
        style={{ paddingTop: 24, borderTop: "1px solid var(--line)" }}
      >
        <label htmlFor="max-turns">每次最多回合</label>
        <input
          id="max-turns"
          className="field mono"
          style={{ width: 140 }}
          type="number"
          min={1}
          value={draft.maxTurns}
          placeholder="20"
          onChange={(event) =>
            edit((d) => ({ ...d, maxTurns: event.target.value }))
          }
        />
        <div className="help">
          agent 一輪裡最多呼叫模型幾次（<code>maxTurns</code>），避免停不下來。
          留空使用預設值 20。agent 檔案自己有寫的話以 agent 檔案為準。
        </div>
      </div>
    </section>
  );
}

const SOURCE_LABELS: Record<SettingSource, string> = {
  agent: "agent 檔案",
  config: "harness.yaml",
  env: "環境變數",
  default: "預設值",
};

function PermissionSection({ project, draft, edit }: EditProps) {
  const [pattern, setPattern] = useState("");
  const [kind, setKind] = useState<RuleKind>("allow");
  const { permissions } = draft;
  const sensitive = project.toolsets
    .flatMap((set) => set.tools)
    .filter((item) => item.needsApproval)
    .map((item) => item.name);

  const add = (event: FormEvent) => {
    event.preventDefault();
    const value = pattern.trim();
    if (!value) return;
    edit((d) => {
      const list = d.permissions[kind];
      if (!list.includes(value)) list.push(value);
      return d;
    });
    setPattern("");
  };

  const remove = (target: RuleKind, value: string) =>
    edit((d) => {
      d.permissions[target] = d.permissions[target].filter((p) => p !== value);
      return d;
    });

  return (
    <section aria-labelledby="h-perm" className="settings-body">
      <div>
        <h2 id="h-perm">權限</h2>
        <p className="lead">
          決定哪些工具可以直接執行、哪些要先問你。判斷順序是：拒絕規則 →
          允許規則 → 模式。儲存後馬上生效，執行中的對話也一樣。
        </p>
      </div>

      <fieldset style={{ border: 0, margin: 0, padding: 0 }}>
        <legend style={{ fontWeight: 600, padding: "0 0 10px" }}>模式</legend>
        <div className="mode-grid">
          {(["ask", "bypass", "dontAsk"] as PermissionMode[]).map((mode) => (
            <label key={mode} className="mode-card">
              <input
                type="radio"
                name="permission-mode"
                value={mode}
                checked={permissions.mode === mode}
                onChange={() =>
                  edit((d) => ({
                    ...d,
                    permissions: { ...d.permissions, mode },
                  }))
                }
              />
              <span className="head">
                {MODE_LABELS[mode]}
                <span className="radio" />
              </span>
              <span className="subtle mono">{mode}</span>
              <span className="desc">{MODE_DESCRIPTIONS[mode]}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="form-row">
        <span className="label">規則</span>
        <div className="rule-grid">
          {RULES.map((rule) => (
            <div key={rule.kind} className="rule-group">
              <div className="head">
                {rule.label}
                <span className="subtle mono">{rule.kind}</span>
              </div>
              <div className="rule-chips">
                {permissions[rule.kind].length === 0 && (
                  <span className="subtle" style={{ lineHeight: "30px" }}>
                    沒有規則
                  </span>
                )}
                {permissions[rule.kind].map((value) => (
                  <span key={value} className={`rule-chip rule-${rule.kind}`}>
                    {value}
                    <button
                      type="button"
                      aria-label={`移除規則 ${value}`}
                      onClick={() => remove(rule.kind, value)}
                    >
                      <CloseIcon size={12} />
                    </button>
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
        <form className="rule-form" onSubmit={add}>
          <label className="sr-only" htmlFor="rule-pattern">
            工具名稱樣式
          </label>
          <input
            id="rule-pattern"
            className="field mono"
            value={pattern}
            placeholder="工具名稱，可用 *，例如 send_*"
            onChange={(event) => setPattern(event.target.value)}
          />
          <label className="sr-only" htmlFor="rule-kind">
            規則類型
          </label>
          <select
            id="rule-kind"
            className="field"
            value={kind}
            onChange={(event) => setKind(event.target.value as RuleKind)}
          >
            {RULES.map((rule) => (
              <option key={rule.kind} value={rule.kind}>
                {rule.label}
              </option>
            ))}
          </select>
          <button type="submit" className="btn btn-dark" style={{ height: 44 }}>
            新增規則
          </button>
        </form>
        <div className="help">
          一定要先問的工具：
          {sensitive.length > 0
            ? sensitive.map((name, index) => (
                <span key={name}>
                  {index > 0 && "、"}
                  <code>{name}</code>
                </span>
              ))
            : "沒有"}
          （工具定義時就標記需要同意）。
        </div>
      </div>

      <div className="form-row">
        <span className="label">寫入 harness.yaml 的內容</span>
        <pre className="yaml">{permissionsYaml(permissions)}</pre>
      </div>
    </section>
  );
}

function permissionsYaml(settings: PermissionSettings): string {
  let text = `permissions:\n  mode: ${settings.mode}`;
  for (const key of ["allow", "ask", "deny"] as const) {
    if (settings[key].length > 0) {
      text += `\n  ${key}: [ ${settings[key].join(", ")} ]`;
    }
  }
  return text;
}

function ToolSection({ project }: { project: ProjectInfo }) {
  return (
    <section aria-labelledby="h-tools" className="settings-body">
      <div>
        <h2 id="h-tools">工具</h2>
        <p className="lead">
          harness.yaml 的 mcpServers、tools/ 底下的檔案，以及內建的 files。agent
          用 toolset 名稱引用一整組工具。
        </p>
      </div>
      {project.toolsets.map((set) => (
        <div key={set.name} className="box toolset">
          <div className="box-row">
            <span className="mono" style={{ fontWeight: 600 }}>
              {set.name}
            </span>
            <span className="subtle">
              {set.builtin ? "內建" : "專案"} · {set.tools.length} 個工具
            </span>
          </div>
          <div className="tool-chips">
            {set.tools.map((item) => (
              <span
                key={item.name}
                className={`tool-chip ${item.needsApproval ? "sensitive" : ""}`}
                title={item.description}
              >
                {item.name}
                {item.needsApproval && " · 需要同意"}
              </span>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}

function AgentSection({ project }: { project: ProjectInfo }) {
  return (
    <section aria-labelledby="h-agents" className="settings-body">
      <div>
        <h2 id="h-agents">Agent</h2>
        <p className="lead">
          agents/ 資料夾裡的每個 Markdown 檔就是一個 agent。
        </p>
      </div>
      {project.agents.map((agent) => (
        <div
          key={agent.name}
          className="box"
          style={{ display: "flex", flexDirection: "column", gap: 8 }}
        >
          <div className="box-row">
            <span className="mono" style={{ fontWeight: 600 }}>
              {agent.name}
            </span>
            <span className="subtle">
              {agent.name === project.defaultAgent ? "預設 agent" : ""}
            </span>
          </div>
          {agent.description && <div>{agent.description}</div>}
          <div className="help mono">
            model: {agent.model ?? "（未設定）"}（
            {SOURCE_LABELS[agent.modelSource]}）{" · "}maxTurns:{" "}
            {agent.maxTurns}（{SOURCE_LABELS[agent.maxTurnsSource]}）
          </div>
          <div className="tool-chips">
            {agent.tools.map((name) => (
              <span key={name} className="tool-chip">
                {name}
              </span>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}
