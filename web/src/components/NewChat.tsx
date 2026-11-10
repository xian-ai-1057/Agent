/** 新對話：選 agent、看它能用哪些工具，然後送出第一則訊息。 */

import { useEffect, useState } from "react";
import { api } from "../api.ts";
import { navigate } from "../router.ts";
import { upsertSummary, useStore } from "../store.ts";
import { Composer } from "./Composer.tsx";
import { ChevronDownIcon, FolderIcon } from "./Icons.tsx";

export function NewChat() {
  const project = useStore((s) => s.project);
  const [agentName, setAgentName] = useState<string>();
  const [error, setError] = useState<string>();

  useEffect(() => {
    document.title = project ? `${project.name} · Harness` : "Harness";
  }, [project]);

  if (!project) return <main className="main" aria-busy="true" />;
  const agent =
    project.agents.find((item) => item.name === agentName) ??
    project.agents.find((item) => item.name === project.defaultAgent) ??
    project.agents[0];
  if (!agent) return <main className="main" />;

  const tools = project.toolsets
    .flatMap((set) => set.tools)
    .filter((item) => agent.tools.includes(item.name));

  const start = async (text: string) => {
    setError(undefined);
    try {
      const summary = await api.createSession(text, agent.name);
      upsertSummary(summary);
      navigate(`/s/${summary.id}`);
      return true;
    } catch (failure) {
      setError((failure as Error).message);
      return false;
    }
  };

  return (
    <main className="main">
      <div className="new-chat">
        <div className="new-chat-inner">
          <div
            className="anim-in"
            style={{ display: "flex", flexDirection: "column", gap: 10 }}
          >
            <span
              className="subtle mono"
              style={{ display: "flex", alignItems: "center", gap: 8 }}
            >
              <FolderIcon size={14} />
              {project.name}
            </span>
            <h1>{agent.description || `要 ${agent.name} 做什麼？`}</h1>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              <span className="chip">agent · {agent.name}</span>
              <span className="chip">{tools.length} 個工具</span>
              {agent.model && <span className="chip">{agent.model}</span>}
            </div>
          </div>

          <div className="anim-in" style={{ animationDelay: "0.08s" }}>
            <Composer
              placeholder={`要 ${agent.name} 做什麼？`}
              busy={false}
              rows={3}
              autoFocus
              onSubmit={start}
              controls={
                project.agents.length > 1 ? (
                  <label className="select-chip">
                    agent：{agent.name}
                    <ChevronDownIcon />
                    <select
                      aria-label="選擇 agent"
                      value={agent.name}
                      onChange={(event) => setAgentName(event.target.value)}
                    >
                      {project.agents.map((item) => (
                        <option key={item.name} value={item.name}>
                          {item.name}
                          {item.description ? ` — ${item.description}` : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : (
                  <span className="select-chip">agent：{agent.name}</span>
                )
              }
            />
            {error && (
              <p className="inline-error" role="alert">
                {error}
              </p>
            )}
          </div>

          {tools.length > 0 && (
            <div
              className="tool-list anim-in"
              style={{ animationDelay: "0.16s" }}
            >
              <div className="subtle" style={{ paddingBottom: 2 }}>
                它可以使用的工具
              </div>
              {tools.map((item) => (
                <div key={item.name} className="item">
                  <span className="name">{item.name}</span>
                  <span className="desc">
                    {item.description}
                    {item.needsApproval && "（執行前會先問你）"}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
