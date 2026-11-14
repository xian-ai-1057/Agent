/** 畫面上共用的文字格式。 */

import { useEffect, useState } from "react";
import type {
  Activity,
  PermissionMode,
  SessionSummary,
  SettingSource,
} from "../../src/web/protocol.ts";

export const MODE_LABELS: Record<PermissionMode, string> = {
  ask: "先問我",
  bypass: "全部直接執行",
  dontAsk: "不問，一律拒絕",
};

/** 模型名稱、maxTurns 這類設定是從哪裡讀到的。 */
export const SOURCE_LABELS: Record<SettingSource, string> = {
  agent: "agent 檔案",
  config: "harness.yaml",
  env: "環境變數",
  default: "預設值",
};

export const STOP_NOTICES: Record<string, string> = {
  max_turns: "已達到這一輪的回合上限，可以再傳訊息讓它繼續。",
  max_tokens: "回覆達到長度上限而被截斷。",
};

/** 正在做什麼，例如「執行 read_file」。 */
export function activityLabel(activity: Activity | undefined): string {
  if (!activity) return "";
  const who = activity.agent ? `${activity.agent}：` : "";
  if (activity.kind === "waiting")
    return `${who}等你確認 ${activity.tool ?? ""}`;
  if (activity.kind === "tool") return `${who}執行 ${activity.tool ?? "工具"}`;
  if (activity.kind === "writing") return `${who}撰寫回覆`;
  return `${who}思考中`;
}

export function isBusy(summary: SessionSummary | undefined): boolean {
  return summary?.status === "running" || summary?.status === "waiting";
}

/** 秒數，例如「12 秒」「3 分 05 秒」。 */
export function formatDuration(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes} 分 ${String(seconds % 60).padStart(2, "0")} 秒`;
  }
  return `${Math.floor(minutes / 60)} 小時 ${minutes % 60} 分`;
}

/** 多久以前，例如「3 分鐘前」。 */
export function formatAgo(time: number, now: number): string {
  const minutes = Math.floor((now - time) / 60_000);
  if (minutes < 1) return "剛剛";
  if (minutes < 60) return `${minutes} 分鐘前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小時前`;
  return new Date(time).toLocaleDateString("zh-TW");
}

/** 每隔一段時間更新一次的目前時間，給計時器用。 */
export function useNow(active: boolean, interval = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), interval);
    return () => clearInterval(timer);
  }, [active, interval]);
  return now;
}

/** 檔案大小，例如「512 B」「1.2 MB」。 */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}
