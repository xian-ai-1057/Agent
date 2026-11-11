/**
 * 新對話：打招呼、輸入框，以及幾個建議。
 *
 * 送出後先在畫面上顯示訊息與「思考中」，建立好對話再切到對話頁。
 * 第一次使用、還沒連接模型時，改成顯示連線設定。
 */

import { useEffect, useMemo, useState } from "react";
import type { SessionOptions } from "../../../src/web/protocol.ts";
import { api } from "../api.ts";
import { navigate } from "../router.ts";
import { upsertSummary, useStore } from "../store.ts";
import "./chat/chat.css";
import { Composer } from "./Composer.tsx";
import { ConnectionForm } from "./ConnectionForm.tsx";
import { PendingView } from "./Conversation.tsx";
import {
  agentTools,
  type ComposerOptions,
  EMPTY_OPTIONS,
  optionsEnabled,
} from "./chat/options.ts";
import { handOff } from "./chat/pending.ts";
import { Thinking } from "./chat/Thinking.tsx";
import { ChevronDownIcon } from "./Icons.tsx";

/** 輸入框底下的建議：點了把文字填進輸入框。 */
const SUGGESTIONS = [
  { label: "分析檔案", text: "幫我分析這個檔案，整理出重點與值得注意的地方：" },
  { label: "寫一份文件", text: "幫我寫一份文件，主題是：" },
  { label: "整理重點", text: "幫我把下面的內容整理成條列重點：\n\n" },
  { label: "上網查資料", text: "幫我上網查一下：" },
];

export function NewChat() {
  const project = useStore((s) => s.project);
  const [agentName, setAgentName] = useState<string>();
  const [error, setError] = useState<string>();
  const [draft, setDraft] = useState<{ text: string }>();
  const [sending, setSending] = useState<{
    text: string;
    attachments: string[];
  }>();
  const [options, setOptions] = useState<SessionOptions>(EMPTY_OPTIONS);

  useEffect(() => {
    document.title = project ? `${project.name} · Harness` : "Harness";
  }, [project]);

  const agent = useMemo(
    () =>
      project?.agents.find((item) => item.name === agentName) ??
      project?.agents.find((item) => item.name === project.defaultAgent) ??
      project?.agents[0],
    [project, agentName],
  );

  if (!project) return <main className="main" aria-busy="true" />;
  if (!agent) return <main className="main" />;

  const { connection } = project.model;
  if (connection && !connection.ready) {
    return (
      <main className="main">
        <div className="new-chat ch-new">
          <div className="new-chat-inner ch-new-inner anim-in">
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <span className="subtle mono">第一次使用</span>
              <h1>先連接模型</h1>
              <p className="lead">
                選一家模型供應商並貼上金鑰，就可以開始使用。之後要更換，到「設定
                → 模型」修改。
              </p>
            </div>
            <ConnectionForm project={project} connection={connection} />
          </div>
        </div>
      </main>
    );
  }

  const enabled = optionsEnabled(project);
  const composerOptions: ComposerOptions | undefined = enabled
    ? {
        models: project.models ?? [],
        skills: project.skills ?? [],
        tools: agentTools(project, agent),
        value: options,
        fallbackModel: agent.model,
        onChange: (change) =>
          setOptions((current) => ({ ...current, ...change })),
      }
    : undefined;

  const start = async (text: string, attachments: string[]) => {
    setError(undefined);
    setSending({ text, attachments });
    try {
      const summary = await api.createSession(
        text,
        agent.name,
        attachments,
        enabled ? options : undefined,
      );
      upsertSummary(summary);
      handOff(summary.id, { text, attachments, base: 0 });
      navigate(`/s/${summary.id}`);
      return true;
    } catch (failure) {
      setSending(undefined);
      setError(`沒有開始對話：${(failure as Error).message}`);
      return false;
    }
  };

  const agentPicker =
    project.agents.length > 1 ? (
      <label className="select-chip">
        <span className="ch-hide-narrow">agent：</span>
        {agent.name}
        <ChevronDownIcon />
        <select
          aria-label="選擇 agent"
          value={agent.name}
          onChange={(event) => {
            setAgentName(event.target.value);
            // 換 agent 後工具清單不同，工具選擇重新來過。
            setOptions((current) => ({ ...current, tools: null }));
          }}
        >
          {project.agents.map((item) => (
            <option key={item.name} value={item.name}>
              {item.name}
              {item.description ? ` — ${item.description}` : ""}
            </option>
          ))}
        </select>
      </label>
    ) : composerOptions ? undefined : (
      <span className="select-chip">agent：{agent.name}</span>
    );

  return (
    <main className="main">
      <div className={`new-chat ch-new${sending ? " sending" : ""}`}>
        <div className="new-chat-inner ch-new-inner">
          {sending ? (
            <div className="thread ch-thread ch-new-thread">
              <PendingView pending={sending} />
              <Thinking />
            </div>
          ) : (
            <h1 className="ch-greeting anim-in">今天要做什麼？</h1>
          )}

          <div className="anim-in" style={{ animationDelay: "0.06s" }}>
            <Composer
              placeholder="問點什麼，或交代一件工作"
              busy={Boolean(sending)}
              rows={3}
              autoFocus
              draft={draft}
              onSubmit={start}
              onUpload={project.uploads ? api.upload : undefined}
              maxUploadBytes={project.uploads?.maxBytes}
              options={composerOptions}
              controls={agentPicker}
            />
            {error && (
              <p className="inline-error" role="alert">
                {error}
              </p>
            )}
          </div>

          {!sending && (
            <ul
              className="ch-suggestions anim-in"
              style={{ animationDelay: "0.12s" }}
              aria-label="建議"
            >
              {SUGGESTIONS.map((item) => (
                <li key={item.label}>
                  <button
                    type="button"
                    className="ch-suggestion"
                    onClick={() => setDraft({ text: item.text })}
                  >
                    {item.label}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </main>
  );
}
