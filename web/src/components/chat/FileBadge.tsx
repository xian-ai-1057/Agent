/** 檔案卡片左邊的副檔名標籤，依檔案類型上色。 */

import { badgeOf, extensionOf } from "./filetype.ts";

const GROUPS: Record<string, string[]> = {
  doc: ["doc", "docx", "odt", "rtf", "md", "markdown", "txt"],
  sheet: ["xls", "xlsx", "csv", "tsv", "ods"],
  slide: ["ppt", "pptx", "key", "odp"],
  pdf: ["pdf"],
  image: ["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "avif"],
  archive: ["zip", "gz", "tar", "rar", "7z"],
};

export function badgeKind(path: string): string {
  const ext = extensionOf(path);
  for (const [kind, list] of Object.entries(GROUPS)) {
    if (list.includes(ext)) return kind;
  }
  return "code";
}

export function FileBadge({ path }: { path: string }) {
  return (
    <span className="ch-badge" data-kind={badgeKind(path)} aria-hidden="true">
      {badgeOf(path)}
    </span>
  );
}
