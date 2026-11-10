import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  accumulate,
  createModel,
  defaultModel,
  type StreamChunk,
} from "../src/model.ts";
import type { ModelResponse } from "../src/types.ts";

async function* streamOf(chunks: StreamChunk[]) {
  yield* chunks;
}

async function collect(source: AsyncIterable<string | ModelResponse>) {
  const deltas: string[] = [];
  let response: ModelResponse | undefined;
  for await (const item of source) {
    if (typeof item === "string") deltas.push(item);
    else response = item;
  }
  if (!response) throw new Error("沒有收到 ModelResponse");
  return { deltas, response };
}

const delta = (value: object, finish_reason: string | null = null) => ({
  choices: [{ delta: value, finish_reason }],
});

describe("accumulate", () => {
  it("逐段送出文字，最後組成完整訊息", async () => {
    const { deltas, response } = await collect(
      accumulate(
        streamOf([
          delta({ content: "你好" }),
          delta({ content: "，世界" }),
          delta({}, "stop"),
          { choices: [], usage: { prompt_tokens: 11, completion_tokens: 7 } },
        ]),
      ),
    );

    expect(deltas).toEqual(["你好", "，世界"]);
    expect(response.message).toEqual({
      role: "assistant",
      content: "你好，世界",
    });
    expect(response.usage).toEqual({ inputTokens: 11, outputTokens: 7 });
    expect(response.finishReason).toBe("stop");
  });

  it("把分段到達的工具參數組合起來", async () => {
    const first = {
      index: 0,
      id: "call_1",
      type: "function",
      function: { name: "add", arguments: "" },
    };
    const part = (text: string) =>
      delta({ tool_calls: [{ index: 0, function: { arguments: text } }] });

    const { response } = await collect(
      accumulate(
        streamOf([
          delta({ tool_calls: [first] }),
          part('{"a": 1,'),
          part(' "b": 2}'),
          delta({}, "tool_calls"),
        ]),
      ),
    );

    expect(response.toolCalls).toEqual([
      { id: "call_1", name: "add", arguments: { a: 1, b: 2 } },
    ]);
    expect(response.message).toEqual({
      role: "assistant",
      content: null,
      tool_calls: [
        {
          id: "call_1",
          type: "function",
          function: { name: "add", arguments: '{"a": 1, "b": 2}' },
        },
      ],
    });
  });

  it("同時處理多個工具呼叫", async () => {
    const call = (index: number, name: string) => ({
      index,
      id: `call_${index}`,
      function: { name, arguments: "{}" },
    });

    const { response } = await collect(
      accumulate(
        streamOf([
          delta({ tool_calls: [call(0, "a")] }),
          delta({ tool_calls: [call(1, "b")] }),
        ]),
      ),
    );

    expect(response.toolCalls.map((item) => item.name)).toEqual(["a", "b"]);
  });

  it("端點不給 index 時，以 id 區分不同的工具呼叫", async () => {
    const call = (id: string, name: string) => ({
      id,
      function: { name, arguments: "{}" },
    });

    const { response } = await collect(
      accumulate(
        streamOf([
          delta({ tool_calls: [call("x", "a")] }),
          delta({ tool_calls: [call("y", "b")] }),
        ]),
      ),
    );

    expect(response.toolCalls.map((item) => item.id)).toEqual(["x", "y"]);
  });

  it("保留端點在工具呼叫上附加的欄位", async () => {
    const call = {
      index: 0,
      id: "c",
      function: { name: "a", arguments: "{}" },
      provider_specific_fields: { thought_signature: "sig" },
    };

    const { response } = await collect(
      accumulate(streamOf([delta({ tool_calls: [call] })])),
    );

    expect(response.message.tool_calls?.[0]).toMatchObject({
      provider_specific_fields: { thought_signature: "sig" },
    });
  });

  it.each([
    ["不合法的 JSON", "{a:", /JSON/],
    ["不是物件", "[1]", /必須是 JSON 物件/],
  ])("參數是%s時標記 parseError", async (_label, raw, pattern) => {
    const call = { index: 0, id: "c", function: { name: "a", arguments: raw } };

    const { response } = await collect(
      accumulate(streamOf([delta({ tool_calls: [call] })])),
    );

    expect(response.toolCalls[0]?.arguments).toEqual({});
    expect(response.toolCalls[0]?.parseError).toMatch(pattern);
  });

  it("參數是空字串時視為沒有參數", async () => {
    const call = {
      index: 0,
      id: "c",
      function: { name: "ping", arguments: "" },
    };

    const { response } = await collect(
      accumulate(streamOf([delta({ tool_calls: [call] })])),
    );

    expect(response.toolCalls[0]).toEqual({
      id: "c",
      name: "ping",
      arguments: {},
    });
  });

  it("把分段的 thinking 文字與簽章組回完整的區塊", async () => {
    const thinking = (block: object) => delta({ thinking_blocks: [block] });

    const { response } = await collect(
      accumulate(
        streamOf([
          thinking({ type: "thinking", thinking: "先查" }),
          thinking({ type: "thinking", thinking: "資料" }),
          thinking({ type: "thinking", thinking: "", signature: "sig-1" }),
          thinking({ type: "redacted_thinking", data: "加密內容" }),
          delta({ content: "答案" }),
        ]),
      ),
    );

    expect(response.message.thinking_blocks).toEqual([
      { type: "thinking", thinking: "先查資料", signature: "sig-1" },
      { type: "redacted_thinking", data: "加密內容" },
    ]);
  });

  it("沒有任何內容時 content 是空字串", async () => {
    const { response } = await collect(accumulate(streamOf([])));

    expect(response.message).toEqual({ role: "assistant", content: "" });
  });
});

describe("createModel：連到 OpenAI 相容端點", () => {
  let server: Server;
  let baseURL: string;
  const received: { body: Record<string, unknown>; auth?: string }[] = [];

  beforeAll(async () => {
    server = createServer((request, response) => {
      let raw = "";
      request.on("data", (chunk) => {
        raw += chunk;
      });
      request.on("end", () => {
        received.push({
          body: JSON.parse(raw),
          auth: request.headers.authorization,
        });
        response.writeHead(200, { "Content-Type": "text/event-stream" });
        const send = (payload: object) =>
          response.write(`data: ${JSON.stringify(payload)}\n\n`);
        send(delta({ role: "assistant", content: "你" }));
        send(delta({ content: "好" }));
        send(delta({}, "stop"));
        send({
          choices: [],
          usage: { prompt_tokens: 3, completion_tokens: 2 },
        });
        response.end("data: [DONE]\n\n");
      });
    });
    await new Promise<void>((done) => server.listen(0, "127.0.0.1", done));
    const { port } = server.address() as AddressInfo;
    baseURL = `http://127.0.0.1:${port}/v1`;
  });
  afterAll(() => server.close());

  it("送出串流請求並組合回應", async () => {
    const model = createModel({ baseURL, apiKey: "test-key" });
    const tools = [
      {
        type: "function" as const,
        function: { name: "add", description: "相加", parameters: {} },
      },
    ];

    const { deltas, response } = await collect(
      model({
        model: "anthropic/claude-x",
        messages: [{ role: "user", content: "嗨" }],
        tools,
        params: { max_tokens: 50 },
      }),
    );

    expect(deltas).toEqual(["你", "好"]);
    expect(response.message.content).toBe("你好");
    expect(response.usage).toEqual({ inputTokens: 3, outputTokens: 2 });
    expect(received.at(-1)).toMatchObject({
      auth: "Bearer test-key",
      body: {
        model: "anthropic/claude-x",
        stream: true,
        stream_options: { include_usage: true },
        max_tokens: 50,
        messages: [{ role: "user", content: "嗨" }],
        tools,
      },
    });
  });

  it("沒有工具時不送 tools 欄位", async () => {
    const model = createModel({ baseURL, apiKey: "k" });

    await collect(model({ model: "m", messages: [], tools: [], params: {} }));

    expect(received.at(-1)?.body).not.toHaveProperty("tools");
  });

  it("本機端點不需要金鑰", async () => {
    const model = createModel({ baseURL });

    const { response } = await collect(
      model({ model: "m", messages: [], tools: [], params: {} }),
    );

    expect(response.message.content).toBe("你好");
  });
});

describe("模型設定", () => {
  it("defaultModel 讀取環境變數 HARNESS_MODEL", () => {
    expect(defaultModel()).toBe("test/model");
  });

  it("沒有設定端點也沒有金鑰時說明怎麼設定", async () => {
    const saved = { ...process.env };
    for (const key of [
      "HARNESS_BASE_URL",
      "OPENAI_BASE_URL",
      "HARNESS_API_KEY",
      "OPENAI_API_KEY",
    ]) {
      delete process.env[key];
    }
    const model = createModel();

    const pending = collect(
      model({ model: "m", messages: [], tools: [], params: {} }),
    );

    await expect(pending).rejects.toThrow(/HARNESS_BASE_URL/);
    Object.assign(process.env, saved);
  });
});
