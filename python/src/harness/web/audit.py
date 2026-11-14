"""稽核紀錄：誰在什麼時候做了什麼，只能新增，不能修改或刪除。

記下會影響安全與權限的操作：登入與登出、成員與群組的變更、權限與模型
設定，以及使用者核准或拒絕工具呼叫。管理平台的「稽核紀錄」頁可以查詢與
匯出（見 server.py 的 /api/admin/audit）。模型用量與花費不在這裡，由模型
中台（LiteLLM Proxy）記錄。

和帳號存在同一個資料庫（.harness/harness.db）的 audit_log 資料表：

    - 資料表上有觸發器，UPDATE 與 DELETE 一律失敗，程式裡也沒有修改或
      刪除的功能。
    - 每一筆存著前一筆的雜湊與自己的雜湊（SHA-256，串成一條鏈）。有人
      繞過觸發器直接改資料庫時，verify() 找得出從哪一筆開始對不上。
    - 不會自動清除舊紀錄；要保存多久、要不要送到 SIEM，由部署的人定期
      匯出處理。

紀錄裡不放密碼、金鑰與對話內容；工具呼叫的參數只留開頭一小段。
"""

import contextlib
import csv
import dataclasses
import datetime
import hashlib
import io
import json
import pathlib
import sqlite3
import time
from collections.abc import Iterator
from typing import Any

from harness.web import accounts as accounts_lib

# 動作代碼 → 畫面上顯示的名稱。代碼的第一段是分類（見 CATEGORIES）。
ACTIONS = {
    "auth.login": "登入",
    "auth.login_failed": "登入失敗",
    "auth.logout": "登出",
    "auth.password": "修改自己的密碼",
    "member.create": "新增成員",
    "member.update": "修改成員",
    "member.password_reset": "重設成員密碼",
    "group.create": "新增群組",
    "group.update": "修改群組",
    "group.delete": "刪除群組",
    "settings.permissions": "修改權限設定",
    "settings.model": "修改預設模型",
    "settings.connection": "修改模型連線",
    "settings.models": "修改可選的模型",
    "knowledge.create": "新增知識庫",
    "knowledge.update": "修改知識庫",
    "knowledge.delete": "拿掉知識庫",
    "knowledge.upload": "上傳知識庫文件",
    "knowledge.document_delete": "刪除知識庫文件",
    "approval.decide": "工具核准",
    "audit.export": "匯出稽核紀錄",
}
CATEGORIES = {
    "auth": "登入",
    "member": "成員",
    "group": "群組",
    "settings": "設定",
    "knowledge": "知識庫",
    "approval": "工具核准",
    "audit": "稽核紀錄",
}
PAGE_LIMIT = 200
EXPORT_LIMIT = 100_000
_DETAIL_LIMIT = 4000
_GENESIS = "0" * 64
# 匯出成 CSV 時，這些字元開頭的值在 Excel 會被當成公式執行。
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    actor_id TEXT NOT NULL DEFAULT '',
    actor_name TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '{}',
    address TEXT NOT NULL DEFAULT '',
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_log_at ON audit_log (at);
CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log 只能新增，不能修改');
END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log 只能新增，不能刪除');
END;
"""


class AuditError(Exception):
    """查詢條件不對，訊息會直接顯示給使用者。"""


@dataclasses.dataclass(frozen=True)
class Entry:
    """一筆稽核紀錄。

    Attributes:
        seq: 流水號，從 1 開始。
        at: 時間（秒）。
        actor_id: 做這件事的人的識別碼；登入失敗等不知道是誰時是空的。
        actor_name: 做這件事的人的帳號名稱（帳號名稱不能改，之後也查得到）。
        action: 動作代碼（ACTIONS 的鍵）。
        target: 對象，例如被修改的帳號、群組名稱或工具名稱。
        detail: 其他資訊（改了哪些欄位、核准的決定…）。
        address: 請求的來源 IP。
        prev_hash: 前一筆的雜湊。
        hash: 這一筆的雜湊。
    """

    seq: int
    at: float
    actor_id: str
    actor_name: str
    action: str
    target: str
    detail: dict[str, Any]
    address: str
    prev_hash: str
    hash: str

    def info(self) -> dict[str, Any]:
        """AuditEntry（見 src/web/protocol.ts）。"""
        return {
            "seq": self.seq,
            "at": self.at * 1000,
            "actor": {"id": self.actor_id, "username": self.actor_name},
            "action": self.action,
            "label": ACTIONS.get(self.action, self.action),
            "target": self.target,
            "detail": self.detail,
            "address": self.address,
        }


@dataclasses.dataclass(frozen=True)
class Filters:
    """查詢與匯出的條件；沒有給的條件不限制。

    Attributes:
        category: 分類（CATEGORIES 的鍵）。
        actor: 帳號名稱（做這件事的人或對象），不分大小寫、部分符合即可。
        since: 這個時間（秒）之後，包含。
        until: 這個時間（秒）之前，不包含。
    """

    category: str | None = None
    actor: str | None = None
    since: float | None = None
    until: float | None = None

    def where(self) -> tuple[str, list[Any]]:
        """SQL 的 WHERE 條件與參數。"""
        clauses: list[str] = []
        params: list[Any] = []
        if self.category:
            if self.category not in CATEGORIES:
                raise AuditError(f"沒有 {self.category} 這個分類")
            clauses.append("action LIKE ?")
            params.append(f"{self.category}.%")
        if self.actor:
            pattern = "%" + _escape_like(self.actor.strip().lower()) + "%"
            clauses.append(
                "(lower(actor_name) LIKE ? ESCAPE '\\' "
                "OR lower(target) LIKE ? ESCAPE '\\')"
            )
            params += [pattern, pattern]
        if self.since is not None:
            clauses.append("at >= ?")
            params.append(self.since)
        if self.until is not None:
            clauses.append("at < ?")
            params.append(self.until)
        return (" AND ".join(clauses) or "1"), params


NO_FILTERS = Filters()


class AuditLog:
    """稽核紀錄的資料庫。每次操作都開新的連線，CLI 與伺服器可以同時寫。"""

    def __init__(self, path: str | pathlib.Path, clock=time.time) -> None:
        self.path = pathlib.Path(path)
        self._clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)
            db.commit()
        finally:
            db.close()

    @classmethod
    def for_project(cls, root: str | pathlib.Path) -> "AuditLog":
        """專案的稽核紀錄（和帳號同一個資料庫檔案）。"""
        return cls(pathlib.Path(root) / accounts_lib.DB_FILE)

    def record(
        self,
        action: str,
        *,
        actor: accounts_lib.User | None = None,
        target: str = "",
        detail: dict[str, Any] | None = None,
        address: str = "",
    ) -> Entry:
        """新增一筆紀錄。

        Args:
            action: 動作代碼，必須是 ACTIONS 裡的一個。
            actor: 做這件事的人；不知道是誰（登入失敗）時為 None。
            target: 對象。
            detail: 其他資訊，會存成 JSON（太長時截斷）。
            address: 請求的來源 IP。

        Returns:
            新增的紀錄。

        Raises:
            ValueError: 不認得這個動作代碼。
        """
        if action not in ACTIONS:
            raise ValueError(f"不認得這個稽核動作：{action}")
        detail_text = _detail_text(detail or {})
        actor_id = actor.id if actor else ""
        actor_name = actor.username if actor else ""
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            # 讀前一筆的雜湊與寫入要在同一個交易裡，同時寫入時才不會分岔。
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT seq, hash FROM audit_log ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            seq = (row[0] + 1) if row else 1
            prev_hash = row[1] if row else _GENESIS
            at = self._clock()
            values = (
                seq,
                at,
                actor_id,
                actor_name,
                action,
                str(target),
                detail_text,
                address,
            )
            digest = _hash(prev_hash, values)
            db.execute(
                "INSERT INTO audit_log (seq, at, actor_id, actor_name, "
                "action, target, detail, address, prev_hash, hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*values, prev_hash, digest),
            )
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()
        return Entry(
            seq,
            at,
            actor_id,
            actor_name,
            action,
            str(target),
            json.loads(detail_text),
            address,
            prev_hash,
            digest,
        )

    def query(
        self,
        filters: Filters = NO_FILTERS,
        *,
        before: int | None = None,
        limit: int = 50,
    ) -> list[Entry]:
        """查詢紀錄，新的在前。

        Args:
            filters: 條件。
            before: 只列流水號小於它的紀錄（下一頁）。
            limit: 最多幾筆（1 到 PAGE_LIMIT）。
        """
        limit = max(1, min(limit, PAGE_LIMIT))
        where, params = filters.where()
        if before is not None:
            where += " AND seq < ?"
            params.append(before)
        with self._read() as db:
            rows = db.execute(
                f"SELECT * FROM audit_log WHERE {where} "
                "ORDER BY seq DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [_entry(row) for row in rows]

    def count(self, filters: Filters = NO_FILTERS) -> int:
        """符合條件的筆數。"""
        where, params = filters.where()
        with self._read() as db:
            return db.execute(
                f"SELECT count(*) FROM audit_log WHERE {where}", params
            ).fetchone()[0]

    def export(self, filters: Filters = NO_FILTERS) -> Iterator[Entry]:
        """符合條件的紀錄，舊的在前，最多 EXPORT_LIMIT 筆。"""
        where, params = filters.where()
        with self._read() as db:
            cursor = db.execute(
                f"SELECT * FROM audit_log WHERE {where} ORDER BY seq LIMIT ?",
                (*params, EXPORT_LIMIT),
            )
            for row in cursor:
                yield _entry(row)

    def verify(self) -> dict[str, Any]:
        """從第一筆開始檢查雜湊鏈，確認紀錄沒有被改過或刪掉。

        Returns:
            {"ok": 是否完整, "checked": 檢查了幾筆,
             "brokenAt": 第一筆對不上的流水號（完整時沒有）}。
        """
        prev_hash = _GENESIS
        expected_seq = 1
        checked = 0
        with self._read() as db:
            for row in db.execute("SELECT * FROM audit_log ORDER BY seq"):
                values = (
                    row["seq"],
                    row["at"],
                    row["actor_id"],
                    row["actor_name"],
                    row["action"],
                    row["target"],
                    row["detail"],
                    row["address"],
                )
                if (
                    row["seq"] != expected_seq
                    or row["prev_hash"] != prev_hash
                    or row["hash"] != _hash(prev_hash, values)
                ):
                    return {
                        "ok": False,
                        "checked": checked,
                        "brokenAt": expected_seq,
                    }
                prev_hash = row["hash"]
                expected_seq += 1
                checked += 1
        return {"ok": True, "checked": checked}

    def _read(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return contextlib.closing(db)


def to_csv(entries: Iterator[Entry]) -> Iterator[str]:
    """把紀錄轉成 CSV（第一行是欄位名稱，開頭有 BOM，Excel 才認得中文）。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "流水號",
            "時間",
            "帳號",
            "動作代碼",
            "動作",
            "對象",
            "詳細資訊",
            "來源 IP",
            "雜湊",
        ]
    )
    yield "﻿" + _drain(buffer)
    for entry in entries:
        writer.writerow(
            [
                entry.seq,
                _iso(entry.at),
                _cell(entry.actor_name),
                entry.action,
                ACTIONS.get(entry.action, entry.action),
                _cell(entry.target),
                _cell(json.dumps(entry.detail, ensure_ascii=False)),
                _cell(entry.address),
                entry.hash,
            ]
        )
        yield _drain(buffer)


def to_jsonl(entries: Iterator[Entry]) -> Iterator[str]:
    """把紀錄轉成 JSON Lines（一行一筆，含雜湊，方便送進 SIEM）。"""
    for entry in entries:
        data = {
            **entry.info(),
            "at": _iso(entry.at),
            "prevHash": entry.prev_hash,
            "hash": entry.hash,
        }
        yield json.dumps(data, ensure_ascii=False) + "\n"


def summarize_arguments(arguments: Any, limit: int = 300) -> str:
    """工具參數的開頭一小段（寫檔的內容等可能很長，也可能有個資）。"""
    text = json.dumps(arguments, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def _hash(prev_hash: str, values: tuple[Any, ...]) -> str:
    payload = json.dumps(
        [prev_hash, *values], ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _detail_text(detail: dict[str, Any]) -> str:
    text = json.dumps(detail, ensure_ascii=False, sort_keys=True, default=str)
    if len(text) <= _DETAIL_LIMIT:
        return text
    return json.dumps({"truncated": text[:_DETAIL_LIMIT]}, ensure_ascii=False)


def _entry(row: sqlite3.Row) -> Entry:
    return Entry(
        seq=row["seq"],
        at=row["at"],
        actor_id=row["actor_id"],
        actor_name=row["actor_name"],
        action=row["action"],
        target=row["target"],
        detail=json.loads(row["detail"] or "{}"),
        address=row["address"],
        prev_hash=row["prev_hash"],
        hash=row["hash"],
    )


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _iso(seconds: float) -> str:
    """伺服器所在時區的時間，帶時差（例如 2026-11-14T13:05:00+08:00）。"""
    moment = datetime.datetime.fromtimestamp(seconds).astimezone()
    return moment.isoformat(timespec="seconds")


def _cell(value: str) -> str:
    """CSV 的值：公式字元開頭的加上 '，避免在 Excel 裡被執行。"""
    return "'" + value if value.startswith(_FORMULA_PREFIXES) else value


def _drain(buffer: io.StringIO) -> str:
    text = buffer.getvalue()
    buffer.seek(0)
    buffer.truncate()
    return text
