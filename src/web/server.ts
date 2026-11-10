/**
 * 網頁版的 HTTP 伺服器（`harness web`）。
 *
 *   /api/...       JSON API，見下方 route 表
 *   /api/events    SSE，推送 Workspace 的所有事件
 *   其他路徑        打包好的前端（dist/public）
 *
 * 伺服器可以執行專案的工具，所以預設只聽 127.0.0.1，並檢查 Host 與
 * Origin 標頭，避免其他網站透過瀏覽器呼叫這裡的 API。
 */

import { existsSync, readFileSync, statSync } from "node:fs";
import {
  createServer,
  type IncomingMessage,
  type ServerResponse,
} from "node:http";
import type { AddressInfo } from "node:net";
import { extname, join, normalize, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import type { ModelFn } from "../model.ts";
import type { Project } from "../project.ts";
import type { ApprovalDecision, ServerEvent } from "./protocol.ts";
import { WebError, Workspace } from "./workspace.ts";

export interface WebServerOptions {
  project: Project;
  /** 0 表示讓系統挑一個空的埠。預設 4321。 */
  port?: number;
  /** 預設 127.0.0.1。 */
  host?: string;
  model?: ModelFn;
  /** 前端檔案所在的資料夾，預設為打包後的 dist/public。 */
  staticDir?: string;
}

export interface WebServer {
  url: string;
  workspace: Workspace;
  close(): Promise<void>;
}

const DEFAULT_PORT = 4321;
const DEFAULT_HOST = "127.0.0.1";
const MAX_BODY = 1_000_000;
const HEARTBEAT = 25_000;
const LOCAL_HOSTS = ["localhost", "127.0.0.1", "[::1]", "::1"];
const DECISIONS: ApprovalDecision[] = ["once", "always", "deny"];
const CONTENT_TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
  ".json": "application/json; charset=utf-8",
};

type Body = Record<string, unknown>;
type Handler = (params: string[], body: Body) => unknown | Promise<unknown>;

/** 啟動伺服器，回傳實際的網址。 */
export async function startWebServer(
  options: WebServerOptions,
): Promise<WebServer> {
  const host = options.host ?? DEFAULT_HOST;
  const workspace = new Workspace(options.project, { model: options.model });
  const staticDir = resolve(
    options.staticDir ?? fileURLToPath(new URL("../public/", import.meta.url)),
  );
  const streams = new Set<ServerResponse>();
  const routes = buildRoutes(workspace);

  const server = createServer((request, response) => {
    handle(request, response).catch((error: unknown) => {
      const status = error instanceof WebError ? error.status : 500;
      const message = error instanceof Error ? error.message : String(error);
      if (!response.headersSent) sendJson(response, status, { error: message });
      else response.end();
    });
  });

  async function handle(request: IncomingMessage, response: ServerResponse) {
    if (!isAllowedHost(request, host)) {
      throw new WebError(403, "不接受這個 Host 的請求");
    }
    const url = new URL(request.url ?? "/", "http://localhost");
    const method = request.method ?? "GET";
    if (!url.pathname.startsWith("/api/")) {
      return serveStatic(staticDir, url.pathname, response);
    }
    if (url.pathname === "/api/events" && method === "GET") {
      return openStream(response);
    }
    if (method !== "GET") assertSameOrigin(request);
    for (const route of routes) {
      if (route.method !== method) continue;
      const match = route.pattern.exec(url.pathname);
      if (!match) continue;
      const body = method === "GET" ? {} : await readJson(request);
      const params = match.slice(1).map(decodeURIComponent);
      const result = await route.handler(params, body);
      return sendJson(response, route.status ?? 200, result ?? { ok: true });
    }
    throw new WebError(404, `沒有這個 API：${method} ${url.pathname}`);
  }

  function openStream(response: ServerResponse) {
    response.writeHead(200, {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    });
    const send = (event: ServerEvent) => {
      response.write(`data: ${JSON.stringify(event)}\n\n`);
    };
    const unsubscribe = workspace.subscribe(send);
    const heartbeat = setInterval(
      () => response.write(": ping\n\n"),
      HEARTBEAT,
    );
    streams.add(response);
    response.on("close", () => {
      clearInterval(heartbeat);
      unsubscribe();
      streams.delete(response);
    });
    send({ type: "hello" });
  }

  await new Promise<void>((done, fail) => {
    server.once("error", fail);
    server.listen(options.port ?? DEFAULT_PORT, host, () => done());
  });
  const { port } = server.address() as AddressInfo;
  const shownHost = host.includes(":") ? `[${host}]` : host;
  return {
    url: `http://${shownHost}:${port}`,
    workspace,
    close: async () => {
      workspace.close();
      for (const stream of streams) stream.end();
      await new Promise<void>((done) => server.close(() => done()));
    },
  };
}

interface Route {
  method: string;
  pattern: RegExp;
  handler: Handler;
  status?: number;
}

function buildRoutes(workspace: Workspace): Route[] {
  const id = "([\\w-]+)";
  const route = (
    method: string,
    path: string,
    handler: Handler,
    status?: number,
  ): Route => ({
    method,
    pattern: new RegExp(`^/api${path}$`),
    handler,
    status,
  });
  return [
    route("GET", "/project", () => workspace.projectInfo()),
    route("GET", "/sessions", () => workspace.listSessions()),
    route(
      "POST",
      "/sessions",
      (_, body) =>
        workspace.startRun({
          text: requireString(body, "text"),
          agent: optionalString(body, "agent"),
        }),
      201,
    ),
    route("GET", `/sessions/${id}`, ([sessionId]) =>
      workspace.getSession(sessionId as string),
    ),
    route(
      "POST",
      `/sessions/${id}/messages`,
      ([sessionId], body) =>
        workspace.startRun({
          sessionId,
          text: requireString(body, "text"),
        }),
      202,
    ),
    route(
      "POST",
      `/sessions/${id}/retry`,
      ([sessionId]) => workspace.retry(sessionId as string),
      202,
    ),
    route("POST", `/sessions/${id}/stop`, ([sessionId]) =>
      workspace.stop(sessionId as string),
    ),
    route("PUT", `/sessions/${id}/mode`, ([sessionId], body) => {
      const mode = body.mode === null ? null : requireString(body, "mode");
      workspace.setMode(sessionId as string, mode as never);
    }),
    route(
      "POST",
      `/sessions/${id}/approvals/${id}`,
      ([sessionId, approvalId], body) => {
        const decision = requireString(body, "decision") as ApprovalDecision;
        if (!DECISIONS.includes(decision)) {
          throw new WebError(400, `decision 必須是 ${DECISIONS.join("、")}`);
        }
        workspace.resolveApproval(
          sessionId as string,
          approvalId as string,
          decision,
        );
      },
    ),
    route("PUT", "/settings/permissions", (_, body) =>
      workspace.updatePermissions({
        mode: requireString(body, "mode") as never,
        allow: body.allow as string[],
        ask: body.ask as string[],
        deny: body.deny as string[],
      }),
    ),
    route("PUT", "/settings/model", (_, body) => {
      const model = body.model === null ? null : requireString(body, "model");
      const maxTurns = body.maxTurns === null ? null : Number(body.maxTurns);
      workspace.updateModelSettings({ model, maxTurns });
    }),
    route("POST", "/settings/model/test", () => workspace.testModel()),
  ];
}

/** 只接受 Host 是本機（或使用者指定的 host）的請求，擋掉 DNS rebinding。 */
function isAllowedHost(request: IncomingMessage, host: string): boolean {
  const header = request.headers.host;
  if (!header) return false;
  const hostname = header.replace(/:\d+$/, "");
  return LOCAL_HOSTS.includes(hostname) || hostname === host;
}

/** 會改變狀態的請求必須來自同一個網址的頁面。 */
function assertSameOrigin(request: IncomingMessage): void {
  const origin = request.headers.origin;
  if (origin && origin !== `http://${request.headers.host}`) {
    throw new WebError(403, "不接受其他網站送來的請求");
  }
  const type = request.headers["content-type"] ?? "";
  if (!type.startsWith("application/json")) {
    throw new WebError(415, "請求內容必須是 JSON");
  }
}

async function readJson(request: IncomingMessage): Promise<Body> {
  let size = 0;
  const chunks: Buffer[] = [];
  for await (const chunk of request) {
    size += (chunk as Buffer).length;
    if (size > MAX_BODY) throw new WebError(413, "請求內容太大");
    chunks.push(chunk as Buffer);
  }
  const text = Buffer.concat(chunks).toString("utf8");
  if (!text) return {};
  try {
    const parsed = JSON.parse(text);
    if (typeof parsed !== "object" || parsed === null) throw new Error();
    return parsed as Body;
  } catch {
    throw new WebError(400, "請求內容不是合法的 JSON 物件");
  }
}

function requireString(body: Body, key: string): string {
  const value = body[key];
  if (typeof value !== "string") throw new WebError(400, `缺少 ${key}`);
  return value;
}

function optionalString(body: Body, key: string): string | undefined {
  const value = body[key];
  return typeof value === "string" ? value : undefined;
}

function sendJson(response: ServerResponse, status: number, data: unknown) {
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  });
  response.end(JSON.stringify(data));
}

/** 送出前端檔案；找不到的路徑都回 index.html，交給前端的路由處理。 */
function serveStatic(
  directory: string,
  pathname: string,
  response: ServerResponse,
) {
  const index = join(directory, "index.html");
  if (!existsSync(index)) {
    response.writeHead(503, { "Content-Type": "text/plain; charset=utf-8" });
    response.end("找不到網頁檔案，請先執行 npm run build");
    return;
  }
  const target = resolve(directory, `.${normalize(decodeURI(pathname))}`);
  const inside = target === directory || target.startsWith(directory + sep);
  const file =
    inside && existsSync(target) && statSync(target).isFile() ? target : index;
  const type = CONTENT_TYPES[extname(file)] ?? "application/octet-stream";
  // 只有 Vite 產生、檔名帶雜湊的 assets/ 可以長期快取。
  const cache = pathname.startsWith("/assets/")
    ? "public, max-age=31536000, immutable"
    : "no-cache";
  response.writeHead(200, { "Content-Type": type, "Cache-Control": cache });
  response.end(readFileSync(file));
}
