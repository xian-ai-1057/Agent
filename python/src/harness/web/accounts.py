"""網頁版的帳號與登入（單機模式）。

帳號存在專案的 .harness/harness.db（SQLite），只用標準函式庫：

    users           帳號：識別碼、使用者名稱、顯示名稱、角色、密碼雜湊
    login_sessions  登入狀態：只存 token 的 SHA-256，不存 token 本身

密碼用 scrypt 加上每個帳號自己的隨機 salt，存成
scrypt$n$r$p$salt$hash（salt 與 hash 是 base64）。

AccountStore 是身分與登入的唯一入口，介面刻意做得很窄（驗證密碼、
管理帳號、建立與查詢登入）：之後的企業版改用 OIDC SSO 與 PostgreSQL 時，
換掉這個類別即可，網頁其他部分不必改。

角色：
    admin   管理者：管理成員、模型連線、全站權限與工作資料夾
    member  成員：只能使用自己的對話、檔案與記憶

至少要留一位沒有停用的管理者，最後一位管理者不能降級或停用。
"""

import base64
import dataclasses
import hashlib
import hmac
import math
import pathlib
import re
import secrets
import sqlite3
import time
import uuid

from harness import project as project_lib

DB_FILE = project_lib.DATA_DIR / "harness.db"
USERS_DIR = project_lib.DATA_DIR / "users"
ROLES = ("admin", "member")
LOCAL_ID = "local"
# 登入狀態最多保留 14 天（每次使用時往後延），12 小時沒有使用就失效。
SESSION_LIFETIME = 14 * 24 * 3600
IDLE_TIMEOUT = 12 * 3600
MIN_PASSWORD_LENGTH = 10
# 登入失敗的限制（見 LoginThrottle）。
# 同一個帳號名稱連續失敗 MAX_FAILURES 次後，每次失敗都要等一段時間才能
# 再試：BACKOFF_SECONDS 秒起跳、每次加倍，最多 MAX_BACKOFF_SECONDS 秒。
MAX_FAILURES = 5
BACKOFF_SECONDS = 2
MAX_BACKOFF_SECONDS = 60
# 同一個 IP 在 IP_WINDOW_SECONDS 秒內最多失敗幾次（不分帳號名稱）。
MAX_IP_FAILURES = 50
IP_WINDOW_SECONDS = 10 * 60
# 這麼久沒有失敗的記錄就忘掉；記錄的總數也有上限。
FORGET_SECONDS = 15 * 60
MAX_THROTTLE_ENTRIES = 10_000
# 最後使用時間最多每分鐘寫一次，避免每個請求都寫資料庫。
_TOUCH_INTERVAL = 60
# scrypt 的參數：約 16 MB 記憶體；存進雜湊裡，之後調整不影響舊密碼。
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
_SALT_BYTES = 16
_HASH_BYTES = 32
_GENERATED_PASSWORD_BYTES = 12
_USERNAME = re.compile(r"^[A-Za-z0-9._-]{2,32}$")
_DISPLAY_NAME_LIMIT = 50

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_seen_at REAL,
    disabled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS login_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    last_seen_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS login_sessions_user ON login_sessions (user_id);
"""


class AccountError(Exception):
    """帳號操作失敗，訊息會直接顯示給使用者。

    Attributes:
        status: HTTP 狀態碼。
    """

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclasses.dataclass(frozen=True)
class User:
    """一位使用者。

    Attributes:
        id: 識別碼（隨機產生，也是資料夾名稱）。
        username: 登入用的名稱，不分大小寫。
        display_name: 畫面上顯示的名稱。
        role: admin 或 member。
        disabled: 停用後不能登入，已經登入的也會失效。
        created_at: 建立時間（秒）。
        last_seen_at: 最後一次使用的時間（秒）；沒有登入過時為 None。
    """

    id: str
    username: str
    display_name: str
    role: str
    disabled: bool = False
    created_at: float = 0.0
    last_seen_at: float | None = None

    @property
    def is_admin(self) -> bool:
        """是不是管理者。"""
        return self.role == "admin"


# 本機模式（只在本機使用、沒有任何帳號）時，所有請求都視為這位管理者。
LOCAL_USER = User(LOCAL_ID, "local", "本機使用者", "admin")


def current_user(user: User) -> dict[str, str]:
    """CurrentUser（見 src/web/protocol.ts）。"""
    return {
        "id": user.id,
        "username": user.username,
        "displayName": user.display_name,
        "role": user.role,
    }


def member_info(user: User) -> dict[str, object]:
    """MemberInfo（見 src/web/protocol.ts），時間換成毫秒。"""
    info: dict[str, object] = {
        **current_user(user),
        "disabled": user.disabled,
        "createdAt": user.created_at * 1000,
    }
    if user.last_seen_at is not None:
        info["lastSeenAt"] = user.last_seen_at * 1000
    return info


def generate_password() -> str:
    """產生一組給成員的隨機密碼（16 個字元）。"""
    return secrets.token_urlsafe(_GENERATED_PASSWORD_BYTES)


def hash_password(password: str) -> str:
    """用 scrypt 與隨機 salt 算出要存起來的密碼雜湊。

    Args:
        password: 密碼。

    Returns:
        scrypt$n$r$p$salt$hash 格式的字串。
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return "$".join(
        (
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.b64encode(salt).decode(),
            base64.b64encode(digest).decode(),
        )
    )


def verify_password(password: str, stored: str) -> bool:
    """確認密碼和存起來的雜湊相符；格式不對時一律不相符。

    Args:
        password: 使用者輸入的密碼。
        stored: hash_password 的結果。

    Returns:
        相符時為 True。
    """
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = _scrypt(
            password, base64.b64decode(salt), int(n), int(r), int(p)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=128 * n * r * p + 1024 * 1024,
        dklen=_HASH_BYTES,
    )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check_new_password(password: str) -> None:
    """檢查使用者自己設定的新密碼。

    Raises:
        AccountError: 密碼太短。
    """
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise AccountError(f"新密碼至少要 {MIN_PASSWORD_LENGTH} 個字元")


class AccountStore:
    """存在 SQLite 裡的帳號與登入狀態。

    每次操作都開一個新的連線，CLI 與正在執行的伺服器可以同時使用
    （資料庫用 WAL 模式）。

    Attributes:
        path: 資料庫檔案。
    """

    def __init__(self, path: str | pathlib.Path, clock=time.time) -> None:
        """開啟（或建立）資料庫。

        Args:
            path: 資料庫檔案的路徑。
            clock: 取得目前時間（秒）的函式，測試時可以換掉。
        """
        self.path = pathlib.Path(path)
        self._clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # journal_mode 不能在交易裡切換，建立資料表也自己處理交易。
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)
        finally:
            db.close()

    @classmethod
    def for_project(
        cls, root: str | pathlib.Path, clock=time.time
    ) -> "AccountStore":
        """專案的帳號資料庫（.harness/harness.db）。"""
        return cls(pathlib.Path(root) / DB_FILE, clock)

    def has_users(self) -> bool:
        """有沒有任何帳號（含停用的）。"""
        with self._connect() as db:
            row = db.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        return row is not None

    def list_users(self) -> list[User]:
        """所有帳號，依建立時間排序。"""
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM users ORDER BY created_at, username"
            ).fetchall()
        return [_user(row) for row in rows]

    def get_user(self, user_id: str) -> User | None:
        """依識別碼找帳號，找不到時為 None。"""
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return _user(row) if row else None

    def find_user(self, username: str) -> User | None:
        """依使用者名稱（不分大小寫）找帳號，找不到時為 None。"""
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM users WHERE username = ?", (username.strip(),)
            ).fetchone()
        return _user(row) if row else None

    def authenticate(self, username: str, password: str) -> User | None:
        """確認帳號密碼；停用的帳號一律失敗。

        Returns:
            帳號與密碼都正確時為那位使用者，否則為 None。
        """
        if not isinstance(username, str) or not isinstance(password, str):
            return None
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM users WHERE username = ?", (username.strip(),)
            ).fetchone()
        if row is None:
            # 帳號不存在時也算一次雜湊，回應時間才看不出帳號存不存在。
            verify_password(password, _DUMMY_HASH)
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        user = _user(row)
        return None if user.disabled else user

    def create_user(
        self,
        username: str,
        display_name: str | None = None,
        role: str = "member",
        password: str | None = None,
    ) -> tuple[User, str]:
        """建立帳號。

        Args:
            username: 使用者名稱：2–32 個英文字母、數字或 . _ -。
            display_name: 顯示名稱（1–50 個字），沒給時用使用者名稱。
            role: admin 或 member。
            password: 密碼；沒給時自動產生一組。

        Returns:
            (新的帳號, 密碼)。

        Raises:
            AccountError: 名稱不合法、已經有人使用，或角色不存在。
        """
        username = _checked_username(username)
        display_name = _checked_display_name(
            username if display_name is None else display_name
        )
        _checked_role(role)
        password = password or generate_password()
        now = self._clock()
        user_id = uuid.uuid4().hex[:12]
        try:
            with self._connect() as db:
                db.execute(
                    "INSERT INTO users (id, username, display_name, role, "
                    "password_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        user_id,
                        username,
                        display_name,
                        role,
                        hash_password(password),
                        now,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise AccountError(f"{username} 已經有人使用", 409) from error
        user = self.get_user(user_id)
        assert user is not None
        return user, password

    def update_user(
        self,
        user_id: str,
        *,
        display_name: str | None = None,
        role: str | None = None,
        disabled: bool | None = None,
    ) -> User:
        """修改顯示名稱、角色或停用狀態；None 表示不改。

        停用的帳號會立刻登出。

        Raises:
            AccountError: 找不到帳號、值不合法，或會讓系統沒有任何
                可以使用的管理者。
        """
        user = self._require(user_id)
        changes: dict[str, object] = {}
        if display_name is not None:
            changes["display_name"] = _checked_display_name(display_name)
        if role is not None:
            changes["role"] = _checked_role(role)
        if disabled is not None:
            changes["disabled"] = int(bool(disabled))
        losing_admin = user.is_admin and not user.disabled
        losing_admin = losing_admin and (
            changes.get("role", "admin") != "admin"
            or changes.get("disabled", 0) == 1
        )
        with self._connect() as db:
            if losing_admin:
                count = db.execute(
                    "SELECT COUNT(*) FROM users WHERE role = 'admin' "
                    "AND disabled = 0 AND id != ?",
                    (user_id,),
                ).fetchone()[0]
                if count == 0:
                    raise AccountError(
                        "至少要留一位可以使用的管理者，不能降級或停用最後一位"
                    )
            for column, value in changes.items():
                # column 只會是上面固定的三個欄位名稱之一。
                db.execute(
                    f"UPDATE users SET {column} = ? WHERE id = ?",
                    (value, user_id),
                )
            if changes.get("disabled") == 1:
                db.execute(
                    "DELETE FROM login_sessions WHERE user_id = ?", (user_id,)
                )
        return self._require(user_id)

    def set_password(self, user_id: str, password: str) -> None:
        """換密碼，並登出這個人所有的登入狀態。

        Raises:
            AccountError: 找不到帳號，或密碼是空的。
        """
        self._require(user_id)
        if not password:
            raise AccountError("密碼不可以是空的")
        with self._connect() as db:
            db.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(password), user_id),
            )
            db.execute(
                "DELETE FROM login_sessions WHERE user_id = ?", (user_id,)
            )

    def create_login(self, user_id: str) -> str:
        """登入成功後建立登入狀態。

        Returns:
            放進 cookie 的 token；資料庫只存它的雜湊。
        """
        token = secrets.token_urlsafe(32)
        now = self._clock()
        with self._connect() as db:
            db.execute(
                "INSERT INTO login_sessions (token_hash, user_id, created_at, "
                "expires_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
                (_token_hash(token), user_id, now, now + SESSION_LIFETIME, now),
            )
            db.execute(
                "UPDATE users SET last_seen_at = ? WHERE id = ?", (now, user_id)
            )
        return token

    def resolve_login(self, token: str | None) -> User | None:
        """依 cookie 裡的 token 找出登入的人，並延長登入狀態。

        Returns:
            登入的人；token 不存在、過期、太久沒用，或帳號已停用時為
            None。
        """
        if not token:
            return None
        key = _token_hash(token)
        now = self._clock()
        with self._connect() as db:
            row = db.execute(
                "SELECT s.expires_at AS s_expires, s.last_seen_at AS s_seen, "
                "u.* FROM login_sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            expired = now >= row["s_expires"]
            idle = now - row["s_seen"] >= IDLE_TIMEOUT
            if expired or idle or row["disabled"]:
                db.execute(
                    "DELETE FROM login_sessions WHERE token_hash = ?", (key,)
                )
                return None
            if now - row["s_seen"] >= _TOUCH_INTERVAL:
                db.execute(
                    "UPDATE login_sessions SET last_seen_at = ?, "
                    "expires_at = ? WHERE token_hash = ?",
                    (now, now + SESSION_LIFETIME, key),
                )
                db.execute(
                    "UPDATE users SET last_seen_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
        return _user(row)

    def end_login(self, token: str | None) -> None:
        """登出：刪掉這個 token 的登入狀態。"""
        if not token:
            return
        with self._connect() as db:
            db.execute(
                "DELETE FROM login_sessions WHERE token_hash = ?",
                (_token_hash(token),),
            )

    def _require(self, user_id: str) -> User:
        user = self.get_user(user_id)
        if user is None:
            raise AccountError("找不到這位成員", 404)
        return user

    def _connect(self) -> "_Transaction":
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        return _Transaction(db)


class _Transaction:
    """with 區塊：開始交易，結束時提交（出錯時復原）並關閉連線。"""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def __enter__(self) -> sqlite3.Connection:
        self._db.execute("BEGIN IMMEDIATE")
        return self._db

    def __exit__(self, kind, *exc_info) -> None:
        try:
            self._db.execute("ROLLBACK" if kind else "COMMIT")
        finally:
            self._db.close()


@dataclasses.dataclass
class _Attempts:
    """一個帳號名稱（或 IP）的登入記錄。"""

    failures: int = 0
    # 在這個時間（clock 的秒數）之前不能再試。
    until: float = 0.0
    # 已經開始、還沒有結果的登入。
    in_flight: int = 0
    # IP 的計數從什麼時候開始。
    window: float = 0.0
    seen: float = 0.0


class LoginThrottle:
    """記在記憶體裡的登入限制，拖慢線上猜密碼。

    - 帳號名稱：連續失敗 MAX_FAILURES 次之後，每次失敗都要等一段時間
      （BACKOFF_SECONDS 秒起跳、每次加倍，最多 MAX_BACKOFF_SECONDS 秒）
      才能再試，換 IP 也一樣；成功登入後歸零。等待時間很短，所以真正
      的使用者不會因為別人亂試而被擋在外面太久。
    - IP：IP_WINDOW_SECONDS 秒內最多失敗 MAX_IP_FAILURES 次，避免同一個
      來源一次試很多帳號名稱。
    - 同時進行：還沒有結果的登入也算一次（reserve 時先占位），所以同時
      送出一大堆密碼也只有剩下的次數會被檢查。

    記錄 FORGET_SECONDS 秒沒有動靜就忘掉，總數最多 MAX_THROTTLE_ENTRIES
    筆。伺服器重新啟動後歸零；目的只是拖慢線上猜密碼，不是完整的防護。
    呼叫前請先確認帳號名稱的格式（見 valid_username），亂打的名稱不會
    占用記錄。
    """

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._names: dict[str, _Attempts] = {}
        self._addresses: dict[str, _Attempts] = {}
        self._pruned = 0.0

    def reserve(self, username: str, address: str) -> int:
        """開始一次登入：可以試的話先占位並回傳 0，否則回傳要等幾秒。

        回傳 0 之後一定要呼叫 release。

        Args:
            username: 帳號名稱（已經確認過格式）。
            address: 用戶端的 IP。
        """
        now = self._clock()
        self._prune(now)
        name = self._names.setdefault(_name_key(username), _Attempts())
        source = self._addresses.setdefault(address, _Attempts())
        if now - source.window >= IP_WINDOW_SECONDS:
            source.failures = 0
            source.window = now
        wait = 0.0
        if now < name.until:
            wait = name.until - now
        elif name.in_flight >= max(1, MAX_FAILURES - name.failures):
            wait = 1.0
        if source.failures >= MAX_IP_FAILURES:
            wait = max(wait, source.window + IP_WINDOW_SECONDS - now)
        if wait > 0:
            return max(1, math.ceil(wait))
        for record in (name, source):
            record.in_flight += 1
            record.seen = now
        return 0

    def release(
        self, username: str, address: str, succeeded: bool | None
    ) -> None:
        """結束 reserve 開始的登入，記下成功或失敗。

        Args:
            username: 帳號名稱。
            address: 用戶端的 IP。
            succeeded: 密碼對不對；None 表示沒有驗證完（例如請求被取消），
                不算一次失敗。
        """
        now = self._clock()
        name = self._names.setdefault(_name_key(username), _Attempts())
        source = self._addresses.setdefault(address, _Attempts())
        for record in (name, source):
            record.in_flight = max(0, record.in_flight - 1)
            record.seen = now
        if succeeded is None:
            return
        if succeeded:
            name.failures = 0
            name.until = 0.0
            return
        name.failures += 1
        if name.failures >= MAX_FAILURES:
            extra = name.failures - MAX_FAILURES
            delay = BACKOFF_SECONDS * 2 ** min(extra, 16)
            name.until = now + min(delay, MAX_BACKOFF_SECONDS)
        source.failures += 1

    def _prune(self, now: float) -> None:
        """忘掉太久沒有動靜的記錄；太多時從最舊的開始忘。"""
        total = len(self._names) + len(self._addresses)
        if total <= MAX_THROTTLE_ENTRIES and now - self._pruned < 60:
            return
        self._pruned = now
        for table in (self._names, self._addresses):
            idle = [
                key
                for key, record in table.items()
                if record.in_flight == 0
                and now - record.seen >= FORGET_SECONDS
                and now >= record.until
            ]
            for key in idle:
                del table[key]
            excess = len(table) - MAX_THROTTLE_ENTRIES // 2
            if excess > 0:
                # 還在等待中的記錄最後才忘。
                oldest = sorted(
                    (now < r.until, r.seen, k)
                    for k, r in table.items()
                    if r.in_flight == 0
                )
                for *_, key in oldest[:excess]:
                    del table[key]


def valid_username(username: object) -> bool:
    """帳號名稱的格式對不對（2–32 個英文字母、數字或 . _ -）。"""
    return isinstance(username, str) and bool(_USERNAME.match(username.strip()))


def _name_key(username: str) -> str:
    return str(username).strip().lower()


def _user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        display_name=row["display_name"],
        role=row["role"],
        disabled=bool(row["disabled"]),
        created_at=row["created_at"],
        last_seen_at=row["last_seen_at"],
    )


def _checked_username(username: object) -> str:
    if not isinstance(username, str) or not _USERNAME.match(username.strip()):
        raise AccountError(
            "使用者名稱要有 2 到 32 個字元，只能用英文字母、數字與 . _ -"
        )
    return username.strip()


def _checked_display_name(display_name: object) -> str:
    if not isinstance(display_name, str):
        raise AccountError("顯示名稱必須是文字")
    display_name = display_name.strip()
    if not 1 <= len(display_name) <= _DISPLAY_NAME_LIMIT:
        raise AccountError(f"顯示名稱要有 1 到 {_DISPLAY_NAME_LIMIT} 個字")
    return display_name


def _checked_role(role: object) -> str:
    if role not in ROLES:
        raise AccountError(f"角色必須是 {'、'.join(ROLES)}")
    return str(role)


# 帳號不存在時用來比對的雜湊，讓回應時間和密碼錯誤時差不多。
_DUMMY_HASH = (
    "scrypt$16384$8$1$AAAAAAAAAAAAAAAAAAAAAA==$"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
)
