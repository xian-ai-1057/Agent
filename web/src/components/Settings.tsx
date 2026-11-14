/**
 * 設定：工具與 agent，右上角切換外觀。
 *
 * 每個人看到的設定頁都一樣，內容只能看（工具與 agent 由專案的檔案定
 * 義）。模型連線、全站權限與成員管理在另外的管理平台（/admin，見
 * admin/AdminApp.tsx），只有管理者能用。
 */

import { useEffect, useState } from "react";
import type { ProjectInfo } from "../../../src/web/protocol.ts";
import { SOURCE_LABELS } from "../format.ts";
import { SETTINGS_SECTIONS, type SettingsSection } from "../sections.ts";
import { useStore } from "../store.ts";
import { Link } from "./Sidebar.tsx";
import {
  getThemePreference,
  setThemePreference,
  type ThemePreference,
} from "./shell/theme.ts";

const SECTION_LABELS: Record<SettingsSection, string> = {
  tools: "工具",
  agents: "Agent",
};

export function Settings({ section }: { section: SettingsSection }) {
  const project = useStore((s) => s.project);

  useEffect(() => {
    document.title = "設定 · Harness";
  }, []);

  if (!project) return <main className="main" aria-busy="true" />;

  return (
    <main className="main">
      <header className="main-header">
        <div className="main-title">
          <h1>設定</h1>
          <span className="subtle mono">{project.name}</span>
        </div>
        <ThemeSwitch />
      </header>
      <div className="scroll">
        <div className="settings">
          <nav className="settings-nav" aria-label="設定分類">
            {SETTINGS_SECTIONS.map((id) => (
              <Link key={id} to={`/settings/${id}`} current={id === section}>
                <span className="bar" />
                {SECTION_LABELS[id]}
              </Link>
            ))}
          </nav>
          <div className="settings-body anim-in" key={section}>
            {section === "tools" && <ToolSection project={project} />}
            {section === "agents" && <AgentSection project={project} />}
          </div>
        </div>
      </div>
    </main>
  );
}

const THEMES: [ThemePreference, string][] = [
  ["system", "跟著系統"],
  ["light", "淺色"],
  ["dark", "深色"],
];

/** 外觀：跟著系統、淺色或深色，只存在這個瀏覽器。 */
function ThemeSwitch() {
  const [theme, setTheme] = useState(getThemePreference);
  return (
    <fieldset className="theme-switch">
      <legend>外觀</legend>
      <div className="segmented segmented-sm">
        {THEMES.map(([id, label]) => (
          <button
            key={id}
            type="button"
            aria-pressed={theme === id}
            onClick={() => {
              setThemePreference(id);
              setTheme(id);
            }}
          >
            {label}
          </button>
        ))}
      </div>
    </fieldset>
  );
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
