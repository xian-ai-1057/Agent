/**
 * Word、Excel、PowerPoint 的預覽。
 *
 * 檔案下載到瀏覽器後在這裡轉成畫面，伺服器不必安裝 Office 或
 * LibreOffice。轉換的程式只在打開這種檔案時才載入，不會拖慢一般頁面。
 *
 * - Word（.docx）：docx-preview 排成一頁一頁。
 * - PowerPoint（.pptx）：pptx-preview 把每張投影片排成清單。
 * - Excel（.xlsx、.xlsm）：讀出儲存格文字（見 xlsx.ts），用 React 顯示成
 *   表格，每個工作表一個分頁。
 *
 * Word 與 PowerPoint 的內容放在沒有 allow-scripts 的 sandbox iframe 裡：
 * 檔案是使用者或 agent 給的，就算轉換程式把檔案裡的東西變成 HTML，
 * 裡面的程式與 javascript: 連結也不會執行，樣式也不會影響這個網頁。
 */

import { useEffect, useRef, useState } from "react";
import { fileUrl, officePdfUrl } from "../../api.ts";
import { formatBytes } from "../../format.ts";
import { useStore } from "../../store.ts";
import { columnName } from "./columns.ts";
import { useFolder } from "./folder.ts";
import type { Sheet } from "./xlsx.ts";

/** 超過這個大小就不在瀏覽器裡轉換，請使用者下載。 */
export const OFFICE_LIMIT = 30 * 1024 * 1024;
/** 表格最多顯示幾列。 */
const SHEET_ROWS = 1000;

type Load<T> =
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "ready"; value: T };

/** 下載整個檔案；太大時丟出說明。 */
async function download(url: string, signal: AbortSignal) {
  const response = await fetch(url, { signal });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(
      (data as { error?: string }).error ??
        `讀不到這個檔案（${response.status}）`,
    );
  }
  const size = Number(response.headers.get("Content-Length") ?? Number.NaN);
  if (Number.isFinite(size) && size > OFFICE_LIMIT) {
    await response.body?.cancel().catch(() => undefined);
    throw new TooLarge(size);
  }
  const data = await response.arrayBuffer();
  if (data.byteLength > OFFICE_LIMIT) throw new TooLarge(data.byteLength);
  return data;
}

class TooLarge extends Error {
  constructor(size: number) {
    super(
      `檔案有 ${formatBytes(size)}，超過 ${formatBytes(OFFICE_LIMIT)}，` +
        "請下載後用 Office 開啟。",
    );
  }
}

function useDownload<T>(
  path: string,
  convert: (data: ArrayBuffer) => Promise<T>,
): Load<T> {
  const folder = useFolder();
  const [load, setLoad] = useState<Load<T>>({ state: "loading" });
  const convertRef = useRef(convert);
  convertRef.current = convert;
  useEffect(() => {
    const controller = new AbortController();
    setLoad({ state: "loading" });
    download(fileUrl(path, false, folder), controller.signal)
      .then((data) => convertRef.current(data))
      .then((value) => {
        if (!controller.signal.aborted) setLoad({ state: "ready", value });
      })
      .catch((error: Error) => {
        if (controller.signal.aborted) return;
        setLoad({ state: "error", message: failure(error) });
      });
    return () => controller.abort();
  }, [path, folder]);
  return load;
}

function failure(error: Error): string {
  if (error instanceof TooLarge) return error.message;
  if (/zip|central directory|signature/i.test(error.message)) {
    return "檔案已損毀，或不是這種格式（舊版的 .doc、.xls、.ppt 請下載後開啟）。";
  }
  return error.message || "無法預覽這個檔案。";
}

function Status({ load }: { load: Load<unknown> }) {
  if (load.state === "loading") {
    return <div className="ch-preview-state">轉換中…</div>;
  }
  if (load.state === "error") {
    return (
      <div className="ch-preview-state" role="alert">
        {load.message}
      </div>
    );
  }
  return null;
}

// ── Excel ─────────────────────────────────────────────

async function readSheets(data: ArrayBuffer): Promise<Sheet[]> {
  const { readWorkbook } = await import("./xlsx.ts");
  return readWorkbook(data, { maxRows: SHEET_ROWS, maxColumns: 100 });
}

export function SheetPreview({ path }: { path: string }) {
  const load = useDownload(path, readSheets);
  const [active, setActive] = useState(0);
  if (load.state !== "ready") return <Status load={load} />;
  const sheets = load.value;
  if (sheets.length === 0) {
    return <div className="ch-preview-state">這個活頁簿沒有工作表。</div>;
  }
  const sheet = sheets[Math.min(active, sheets.length - 1)] ?? sheets[0];
  if (!sheet) return null;
  const width = sheet.rows[0]?.length ?? 0;
  return (
    <div className="ch-sheet">
      {sheets.length > 1 && (
        <div className="ch-sheet-tabs" role="tablist" aria-label="工作表">
          {sheets.map((item, index) => (
            <button
              // biome-ignore lint/suspicious/noArrayIndexKey: 工作表依位置排列，名稱可能重複
              key={index}
              type="button"
              role="tab"
              aria-selected={index === active}
              className="ch-sheet-tab"
              onClick={() => setActive(index)}
            >
              {item.name || `工作表 ${index + 1}`}
              {item.hidden && <span className="subtle">（隱藏）</span>}
            </button>
          ))}
        </div>
      )}
      {sheet.rows.length === 0 ? (
        <div className="ch-preview-state">這個工作表是空的。</div>
      ) : (
        <div className="ch-csv-scroll ch-sheet-scroll">
          <table>
            <thead>
              <tr>
                <th className="ch-sheet-corner" />
                {Array.from({ length: width }, (_, index) => (
                  // biome-ignore lint/suspicious/noArrayIndexKey: 欄位依位置排列
                  <th key={index}>{columnName(index)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sheet.rows.map((row, rowIndex) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: 資料列依位置排列
                <tr key={rowIndex}>
                  <th className="ch-sheet-row" scope="row">
                    {rowIndex + 1}
                  </th>
                  {row.map((cell, index) => (
                    // biome-ignore lint/suspicious/noArrayIndexKey: 欄位依位置排列
                    <td key={index} title={cell.length > 40 ? cell : undefined}>
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {sheet.truncated && (
        <div className="ch-preview-note">
          只顯示前 {SHEET_ROWS} 列、100 欄；完整內容請下載。
        </div>
      )}
    </div>
  );
}

// ── Word 與 PowerPoint ─────────────────────────────────

type Simple = (props: { path: string; title: string }) => React.ReactNode;

export function WordPreview(props: { path: string; title: string }) {
  return <Converted {...props} simple={WordSimple} />;
}

export function SlidesPreview(props: { path: string; title: string }) {
  return <Converted {...props} simple={SlidesSimple} />;
}

const SIMPLE_NOTE = "簡易預覽：版面可能和 Office 不一樣，完整內容請下載。";

type Conversion =
  | { kind: "loading" }
  | { kind: "pdf"; url: string }
  | { kind: "simple"; note: string }
  | { kind: "error"; message: string };

/**
 * 伺服器能轉換時（features.officePdf）顯示轉好的 PDF，版面和 Office 相同；
 * 不能轉換或轉換失敗時改用瀏覽器裡的簡易預覽。
 */
function Converted({
  path,
  title,
  simple: SimpleView,
}: {
  path: string;
  title: string;
  simple: Simple;
}) {
  const folder = useFolder();
  const enabled = useStore((s) => s.project?.features?.officePdf === true);
  const [conversion, setConversion] = useState<Conversion>({
    kind: "loading",
  });

  useEffect(() => {
    if (!enabled) {
      setConversion({ kind: "simple", note: SIMPLE_NOTE });
      return;
    }
    const controller = new AbortController();
    let url: string | undefined;
    setConversion({ kind: "loading" });
    fetch(officePdfUrl(path, folder), { signal: controller.signal })
      .then(async (response) => {
        if (response.ok) {
          url = URL.createObjectURL(await response.blob());
          setConversion({ kind: "pdf", url });
          return;
        }
        const data = await response.json().catch(() => ({}));
        const message = (data as { error?: string }).error ?? "";
        if (response.status === 413) {
          setConversion({ kind: "error", message });
        } else if (response.status === 501 || response.status === 415) {
          setConversion({ kind: "simple", note: SIMPLE_NOTE });
        } else {
          setConversion({
            kind: "simple",
            note: `${message || "轉成 PDF 失敗"}。${SIMPLE_NOTE}`,
          });
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setConversion({ kind: "simple", note: SIMPLE_NOTE });
        }
      });
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [enabled, path, folder]);

  if (conversion.kind === "loading") {
    return (
      <div className="ch-preview-state">轉換中…第一次打開需要幾秒鐘。</div>
    );
  }
  if (conversion.kind === "error") {
    return (
      <div className="ch-preview-state" role="alert">
        {conversion.message}
      </div>
    );
  }
  if (conversion.kind === "pdf") {
    return (
      <div className="ch-preview-pdf">
        {/* 轉好的 PDF 由瀏覽器自己的檢視器顯示，和一般 PDF 預覽相同。 */}
        <iframe src={conversion.url} title={title} />
        <a
          className="ch-preview-open"
          href={conversion.url}
          target="_blank"
          rel="noreferrer noopener"
        >
          看不到內容？在新分頁開啟
        </a>
      </div>
    );
  }
  return (
    <>
      <div className="ch-preview-note ch-office-note">{conversion.note}</div>
      <SimpleView path={path} title={title} />
    </>
  );
}

/** iframe 裡的基本樣式：灰色底，頁面或投影片置中。 */
const FRAME_STYLE = `
html, body { margin: 0; background: #e9e9ec; }
body { padding: 16px 0; font-family: system-ui, sans-serif; }
.docx-wrapper { background: transparent !important; padding: 0 !important; }
.docx-wrapper > section.docx {
  margin: 0 auto 16px !important;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.18);
}
.ch-slides { display: flex; flex-direction: column; align-items: center; }
.ch-slides .pptx-preview-wrapper { background: transparent !important; overflow: visible !important; height: auto !important; }
.ch-slides .pptx-preview-slide-wrapper { margin: 0 auto 16px !important; box-shadow: 0 1px 4px rgba(0, 0, 0, 0.18); }
`;

type Render = (
  body: HTMLElement,
  head: HTMLElement,
  width: number,
) => Promise<void>;

/**
 * 沒有 allow-scripts 的 sandbox iframe：文件內容放在這裡面。
 * allow-same-origin 讓這個網頁可以把轉好的內容放進去；因為不能執行
 * 程式，放進去的東西不能反過來碰這個網頁。
 */
function IsolatedFrame({
  title,
  render,
}: {
  title: string;
  render: Render | undefined;
}) {
  const ref = useRef<HTMLIFrameElement>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    const doc = ref.current?.contentDocument;
    if (!loaded || !render || !doc) return;
    let alive = true;
    doc.body.replaceChildren();
    setError(undefined);
    const width = Math.max(320, doc.documentElement.clientWidth - 32);
    render(doc.body, doc.head, width).catch((failed: Error) => {
      if (alive) setError(failure(failed));
    });
    return () => {
      alive = false;
    };
  }, [loaded, render]);

  return (
    <div className="ch-office">
      {error && (
        <div className="ch-preview-state" role="alert">
          {error}
        </div>
      )}
      <iframe
        ref={ref}
        className="ch-office-frame"
        title={title}
        sandbox="allow-same-origin"
        srcDoc={`<!doctype html><html><head><meta charset="utf-8"><style>${FRAME_STYLE}</style></head><body></body></html>`}
        onLoad={() => setLoaded(true)}
        hidden={Boolean(error)}
      />
    </div>
  );
}

function WordSimple({ path, title }: { path: string; title: string }) {
  const load = useDownload(path, async (data) => data);
  const render = useRender(load, async (data, body, head) => {
    const { renderAsync } = await import("docx-preview");
    await renderAsync(data, body, head, {
      className: "docx",
      inWrapper: true,
      ignoreLastRenderedPageBreak: true,
      experimental: true,
      useBase64URL: true,
      // altChunk 是嵌在 Word 檔裡的整段 HTML，不顯示。
      renderAltChunks: false,
    });
    safeLinks(body);
    fitWidth(body, ".docx-wrapper");
  });
  if (load.state !== "ready") return <Status load={load} />;
  return <IsolatedFrame title={title} render={render} />;
}

function SlidesSimple({ path, title }: { path: string; title: string }) {
  const load = useDownload(path, async (data) => data);
  const render = useRender(load, async (data, body, _head, width) => {
    const { init } = await import("pptx-preview");
    const container = body.ownerDocument.createElement("div");
    container.className = "ch-slides";
    body.append(container);
    const slideWidth = Math.min(width, 960);
    const previewer = init(container, {
      width: slideWidth,
      height: Math.round((slideWidth * 9) / 16),
      mode: "list",
    });
    await previewer.preview(data);
    safeLinks(body);
    fitWidth(body, ".ch-slides");
  });
  if (load.state !== "ready") return <Status load={load} />;
  return <IsolatedFrame title={title} render={render} />;
}

/**
 * 拿掉 http、https、mailto 以外的連結（例如 javascript:）。iframe 本身不能
 * 執行程式，這是多一層保險。
 */
function safeLinks(body: HTMLElement) {
  for (const link of body.querySelectorAll<HTMLAnchorElement>("a[href]")) {
    let protocol = "";
    try {
      protocol = new URL(link.href).protocol;
    } catch {
      // 不是合法網址：一樣拿掉。
    }
    if (!["http:", "https:", "mailto:"].includes(protocol)) {
      link.removeAttribute("href");
    }
  }
}

/**
 * 頁面比面板寬時整個縮小（CSS zoom），不必左右捲動，左邊也不會被切掉。
 */
function fitWidth(body: HTMLElement, selector: string) {
  const content = body.querySelector<HTMLElement>(selector);
  if (!content) return;
  const available = body.clientWidth - 16;
  const width = content.scrollWidth;
  if (width > available) {
    content.style.zoom = String(available / width);
  }
}

/** 檔案下載好之後，產生把它畫進 iframe 的函式。 */
function useRender(
  load: Load<ArrayBuffer>,
  draw: (
    data: ArrayBuffer,
    body: HTMLElement,
    head: HTMLElement,
    width: number,
  ) => Promise<void>,
): Render | undefined {
  const drawRef = useRef(draw);
  drawRef.current = draw;
  const data = load.state === "ready" ? load.value : undefined;
  const [render, setRender] = useState<Render>();
  useEffect(() => {
    if (!data) {
      setRender(undefined);
      return;
    }
    // 每次換檔案都產生新的函式，讓 IsolatedFrame 重新畫。
    setRender(
      () => (body: HTMLElement, head: HTMLElement, width: number) =>
        drawRef.current(data, body, head, width),
    );
  }, [data]);
  return render;
}
