/**
 * 預覽工作資料夾裡的檔案（Python 版的 /api/files）。
 *
 * 圖片、PDF 直接顯示；Markdown 排版；CSV 做成表格；程式碼與文字上色；
 * HTML 只顯示原始碼，不會執行。其他格式（Word、Excel、壓縮檔…）顯示
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

const TEXT_LIMIT = 300_000;
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
        <iframe
          src={fileUrl(path)}
          title={name}
          // 不給 allow-same-origin，PDF 裡的內容碰不到這個網頁。
          sandbox="allow-scripts"
        />
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
    fetch(fileUrl(path), { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          const data = await response.json().catch(() => ({}));
          throw new Error(
            (data as { error?: string }).error ??
              `讀不到這個檔案（${response.status}）`,
          );
        }
        const text = await response.text();
        setLoaded({
          state: "ready",
          text: text.slice(0, TEXT_LIMIT),
          truncated: text.length > TEXT_LIMIT,
        });
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
      <Markdown text={fenced(loaded.text, languageOf(path))} />
      {note}
    </div>
  );
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
