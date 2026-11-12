/**
 * 輸入框左下角的工作資料夾選單（Python 版，features.folders）：列出
 * 我的檔案與管理者開放的共用資料夾，唯讀的加上標籤。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { FolderInfo } from "../../../../src/web/protocol.ts";
import { findFolder, folderLabel, sortFolders } from "../../folders.ts";
import { CheckIcon, ChevronDownIcon, FolderIcon } from "../Icons.tsx";
import { focusTrigger, Popover } from "./Popover.tsx";

export interface FolderChoice {
  folders: FolderInfo[];
  /** 目前的資料夾 id。 */
  value: string;
  onChange: (id: string) => void;
  /** 在對話裡換資料夾要等下一則訊息才生效，選單裡要說明。 */
  nextTurn?: boolean;
}

export function FolderMenu({
  choice,
  closeSignal,
}: {
  choice: FolderChoice;
  /** 改變時關掉選單（例如送出了訊息）。 */
  closeSignal?: number;
}) {
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  // biome-ignore lint/correctness/useExhaustiveDependencies: 只在 closeSignal 改變時關
  useEffect(close, [closeSignal]);

  const list = sortFolders(choice.folders);
  const current = findFolder(list, choice.value);
  const label = folderLabel(list, choice.value);
  const readOnly = current?.writable === false;
  const choose = (id: string) => {
    if (id !== choice.value) choice.onChange(id);
    focusTrigger(anchor);
    close();
  };

  return (
    <div className="ch-anchor ch-folder-anchor" ref={anchor}>
      <button
        type="button"
        className="ch-model-btn ch-folder-btn"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`工作資料夾：${label}${readOnly ? "（唯讀）" : ""}`}
        title={`工作資料夾：${label}`}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <FolderIcon size={15} />
        <span className="ch-model-name">{label}</span>
        {readOnly && <span className="ch-tag ch-hide-narrow">唯讀</span>}
        <ChevronDownIcon size={14} />
      </button>
      <Popover
        open={open}
        onClose={close}
        anchor={anchor}
        label="選擇工作資料夾"
        className="ch-folder-pop"
      >
        <div className="ch-pop-list" role="menu" aria-label="工作資料夾">
          {list.map((folder) => {
            const checked = folder.id === choice.value;
            return (
              <button
                key={folder.id}
                type="button"
                role="menuitemradio"
                aria-checked={checked}
                className="ch-pop-item"
                onClick={() => choose(folder.id)}
              >
                <FolderIcon size={16} />
                <span className="ch-pop-main">
                  <span className="ch-pop-title">{folder.name}</span>
                  <span className="ch-pop-sub">
                    {folder.kind === "private" ? "只有你看得到" : "共用"}
                    {!folder.writable && <span className="ch-tag">唯讀</span>}
                  </span>
                </span>
                <span className="ch-pop-check">
                  {checked && <CheckIcon size={14} />}
                </span>
              </button>
            );
          })}
        </div>
        <div className="ch-pop-note">
          agent 只能讀寫這個資料夾裡的檔案
          {choice.nextTurn && "。換了之後，下一則訊息開始使用"}
        </div>
      </Popover>
    </div>
  );
}
