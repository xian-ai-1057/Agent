/** 測試共用的輔助函式。 */

import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { ModelFn, ModelRequest } from "../src/model.ts";
import type { ModelResponse, RunContext } from "../src/types.ts";

export const FIXTURE_DIR = join(
  dirname(fileURLToPath(import.meta.url)),
  "fixtures",
);
/** 測試用 MCP 伺服器的路徑，以 `node` 啟動。 */
export const MCP_FIXTURE = join(FIXTURE_DIR, "mcp-server.mjs");

/** 建立一個只有文字的模型回應。 */
export function textResponse(
  text: string,
  finishReason = "stop",
): ModelResponse {
  return {
    message: { role: "assistant", content: text },
    toolCalls: [],
    usage: { inputTokens: 10, outputTokens: 5 },
    finishReason,
  };
}

/** 建立一個要求呼叫工具的模型回應，每個參數是 [工具名稱, 參數]。 */
export function toolResponse(
  ...calls: [string, Record<string, unknown>][]
): ModelResponse {
  const toolCalls = calls.map(([name, args], index) => ({
    id: `call_${index}_${name}`,
    name,
    arguments: args,
  }));
  return {
    message: {
      role: "assistant",
      content: null,
      tool_calls: toolCalls.map((call) => ({
        id: call.id,
        type: "function" as const,
        function: {
          name: call.name,
          arguments: JSON.stringify(call.arguments),
        },
      })),
    },
    toolCalls,
    usage: { inputTokens: 10, outputTokens: 5 },
    finishReason: "tool_calls",
  };
}

/** 依照劇本回應的假模型，並記錄每次收到的請求。 */
export function fakeModel(script: ModelResponse[]) {
  const remaining = [...script];
  const requests: ModelRequest[] = [];
  const model: ModelFn = async function* (request) {
    requests.push({ ...request, messages: structuredClone(request.messages) });
    const response = remaining.shift();
    if (!response) throw new Error("假模型的劇本已經用完");
    if (response.message.content) yield response.message.content;
    yield response;
  };
  return { model, requests };
}

/** 取得第 n 次請求送給模型的最後一則訊息內容。 */
export function lastContent(
  requests: ModelRequest[],
  index: number,
): string | null | undefined {
  return requests[index]?.messages.at(-1)?.content;
}

/** 建立臨時資料夾。 */
export function tempDir(): string {
  return mkdtempSync(join(tmpdir(), "harness-test-"));
}

/** 以指定資料夾為專案根目錄的 RunContext。 */
export function contextIn(workdir: string): RunContext {
  return { agentName: "test", workdir, sessionId: "s1", deps: undefined };
}
