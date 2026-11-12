/**
 * 預覽工作資料夾裡的檔案（Python 版的 /api/files）。
 *
 * 圖片、PDF 直接顯示；Markdown 排版；CSV 做成表格；程式碼與文字上色；
 * HTML、SVG 只顯示原始碼，不會執行。其他格式（Word、Excel、壓縮檔…）顯示
 * 檔案卡片與下載按鈕。
 */

import { useEffect, useState } from "react";
import { api, fileUrl } from "../../api.ts";
import { formatBytes } from "../../format.ts";
import { fenced, Markdown } from "../Markdown.tsx";
import { parseCsv } from "./csv.ts";
import { FileBadge } from "./FileBadge.tsx";
import { fileName, languageOf, previewKind } from "./filetype.ts";
import { DownloadIcon } from "./icons.tsx";

/** 最多讀幾個位元組；大檔案只讀開頭，不整個下載。 */
const TEXT_LIMIT = 300_000;
/** 超過這個長度就不上色，直接顯示純文字。 */
const HIGHLIGHT_LIMIT = 50_000;
const CSV_ROWS = 200;

type Loaded =
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "ready"; text: string; truncated: boolean };

export function FilePreview({ path }: { path: string }) {
  const kind = previewKind(path);
  const name = fileName(path);
  if (kind === "image") {
    return (
      <div className="ch-preview-media">
        <img src={fileUrl(path)} alt={name} />
      </div>
    );
  }
  if (kind === "pdf") {
    return (
      <div className="ch-preview-pdf">
        {/* 不加 sandbox：Chromium 不在沙箱框架裡顯示 PDF。伺服器以
            application/pdf 加 nosniff 送出，PDF 由瀏覽器自己的檢視器開，
            碰不到這個網頁。 */}
        <iframe src={fileUrl(path)} title={name} />
        <a
          className="ch-preview-open"
          href={fileUrl(path)}
          target="_blank"
          rel="noreferrer noopener"
        >
          看不到內容？在新分頁開啟
        </a>
      </div>
    );
  }
  if (kind === "binary") return <BinaryCard path={path} />;
  return <TextPreview path={path} kind={kind} />;
}

function TextPreview({
  path,
  kind,
}: {
  path: string;
  kind: "markdown" | "csv" | "html" | "text";
}) {
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    setLoaded({ state: "loading" });
    readHead(fileUrl(path), TEXT_LIMIT, controller.signal)
      .then(({ text, truncated }) => {
        setLoaded({ state: "ready", text, truncated });
      })
      .catch((error: Error) => {
        if (controller.signal.aborted) return;
        setLoaded({ state: "error", message: error.message });
      });
    return () => controller.abort();
  }, [path]);

  if (loaded.state === "loading") {
    return <div className="ch-preview-state">讀取中…</div>;
  }
  if (loaded.state === "error") {
    return (
      <div className="ch-preview-state" role="alert">
        {loaded.message}
      </div>
    );
  }
  const note = loaded.truncated && (
    <div className="ch-preview-note">檔案太大，只顯示開頭的部分。</div>
  );
  if (kind === "markdown") {
    return (
      <div className="preview-body">
        <Markdown text={loaded.text} />
        {note}
      </div>
    );
  }
  if (kind === "csv") return <CsvTable text={loaded.text} />;
  return (
    <div className="preview-body ch-preview-code">
      {loaded.text.length > HIGHLIGHT_LIMIT ? (
        <pre className="ch-preview-plain">{loaded.text}</pre>
      ) : (
        <Markdown text={fenced(loaded.text, languageOf(path))} />
      )}
      {note}
    </div>
  );
}

/**
 * 只讀檔案開頭的 limit 個位元組。先用 Range 要求；伺服器不支援、整個
 * 檔案送來時，讀到夠了就取消下載。
 */
async function readHead(
  url: string,
  limit: number,
  signal: AbortSignal,
): Promise<{ text: string; truncated: boolean }> {
  const response = await fetch(url, {
    signal,
    headers: { Range: `bytes=0-${limit - 1}` },
  });
  // 空檔案沒有可以要求的範圍。
  if (response.status === 416) return { text: "", truncated: false };
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(
      (data as { error?: string }).error ??
        `讀不到這個檔案（${response.status}）`,
    );
  }
  const chunks: Uint8Array[] = [];
  let size = 0;
  let more = false;
  const reader = response.body?.getReader();
  if (reader) {
    while (size < limit) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      size += value.byteLength;
    }
    if (size >= limit) {
      // 伺服器沒照 Range 回應時，剩下的不用再下載。
      more = response.status !== 206;
      await reader.cancel().catch(() => undefined);
    }
  } else {
    const whole = new Uint8Array(await response.arrayBuffer());
    chunks.push(whole);
    size = whole.byteLength;
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  const head = bytes.subarray(0, limit);
  // 檔案大小：206 看 Content-Range，200 看 Content-Length。
  const total =
    response.status === 206
      ? rangeTotal(response.headers.get("Content-Range"))
      : Number(response.headers.get("Content-Length") ?? Number.NaN);
  const truncated =
    total !== undefined && Number.isFinite(total)
      ? total > head.byteLength
      : more || size > limit;
  let text = new TextDecoder().decode(head);
  // 切在多位元組字元中間時，最後會多一個替代字元。
  if (truncated) text = text.replace(/\uFFFD$/, "");
  return { text, truncated };
}

/** Content-Range「bytes 0-99/1234」裡的檔案大小。 */
function rangeTotal(header: string | null): number | undefined {
  const match = header?.match(/\/(\d+)$/);
  return match ? Number(match[1]) : undefined;
}

function CsvTable({ text }: { text: string }) {
  const { rows, truncated } = parseCsv(text, CSV_ROWS + 1);
  const [head, ...body] = rows;
  if (!head) return <div className="ch-preview-state">這個檔案是空的。</div>;
  const shown = body.slice(0, CSV_ROWS);
  return (
    <div className="ch-csv">
      <div className="ch-csv-scroll">
        <table>
          <thead>
            <tr>
              {head.map((cell, index) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: 欄位依位置排列
                <th key={index}>{cell}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((row, rowIndex) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: 資料列依位置排列
              <tr key={rowIndex}>
                {row.map((cell, index) => (
                  // biome-ignore lint/suspicious/noArrayIndexKey: 欄位依位置排列
                  <td key={index}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(truncated || body.length > CSV_ROWS) && (
        <div className="ch-preview-note">只顯示前 {CSV_ROWS} 列。</div>
      )}
    </div>
  );
}

function BinaryCard({ path }: { path: string }) {
  const [size, setSize] = useState<number>();
  useEffect(() => {
    let alive = true;
    const dir = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
    api
      .listFiles(dir)
      .then((entries) => {
        const entry = entries.find((item) => item.path === path);
        if (alive && entry?.size !== undefined) setSize(entry.size);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [path]);
  return (
    <div className="ch-binary">
      <FileBadge path={path} />
      <div className="ch-binary-main">
        <div className="ch-binary-name">{fileName(path)}</div>
        <div className="ch-binary-meta">
          {size !== undefined ? `${formatBytes(size)} · ` : ""}
          這種檔案無法在這裡預覽
        </div>
      </div>
      <a className="btn btn-outline" href={fileUrl(path, true)} download>
        <DownloadIcon size={14} />
        下載
      </a>
    </div>
  );
}
