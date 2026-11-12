"""內建工具共用的路徑處理。

工作目錄裡的檔案可能同時被別的程式改動（網頁版的沙盒和伺服器看的是
同一個資料夾），例如把一個資料夾換成指向 .harness/secrets 的捷徑
（symlink）。先解析路徑、檢查完再打開的話，兩步之間可能已經被換掉。

所以讀寫檔案一律用 open_path：從工作目錄開始，一層一層用 O_NOFOLLOW
打開（openat，以上一層的 fd 為基準），任何一層是捷徑都拒絕；檢查和打
開是同一個動作，中間不可能被換掉。路徑本身只做字面上的整理（去掉 .
與 ..），不跟著任何捷徑走。

不支援 O_NOFOLLOW 或 dir_fd 的平台（例如 Windows）退回原本的作法：
解析出實際位置（resolve_path）後確認還在工作目錄裡再打開。
"""

import dataclasses
import errno
import os
import pathlib
import stat

from harness import datatypes

# 存放金鑰與登入密碼的資料夾（見 harness.credentials），內建工具不能碰。
SECRETS_DIR = pathlib.Path(".harness") / "secrets"

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
# 這個平台能不能一層一層、不跟著捷徑打開檔案（macOS 與 Linux 都可以）。
SAFE_OPEN = bool(
    _NOFOLLOW
    and _DIRECTORY
    and os.open in os.supports_dir_fd
    and os.mkdir in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.scandir in os.supports_fd
)
DIR_FLAGS = os.O_RDONLY | _DIRECTORY
# 讀檔時加上 O_NONBLOCK：路徑是 FIFO 時打開不會卡住（之後會被拒絕）。
READ_FLAGS = os.O_RDONLY | _NONBLOCK
WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _NONBLOCK


class LinkError(ValueError):
    """路徑上有捷徑（symlink）。"""


@dataclasses.dataclass(frozen=True)
class Entry:
    """資料夾裡的一個項目（不含捷徑）。

    Attributes:
        name: 名稱。
        is_dir: 是不是資料夾。
        size: 大小（位元組）。
        modified: 最後修改時間（秒）。
    """

    name: str
    is_dir: bool
    size: int
    modified: float


def relative_parts(context: datatypes.RunContext, path: str) -> tuple[str, ...]:
    """把路徑整理成相對於工作目錄的各層名稱（只看字面，不碰檔案系統）。

    Args:
        context: 這次 run 的環境資訊。
        path: 相對於工作目錄的路徑，或工作目錄底下的絕對路徑。

    Returns:
        各層名稱；空的表示工作目錄本身。

    Raises:
        ValueError: 路徑跑到工作目錄之外、指向存放金鑰的資料夾，或指向
            context 不讓碰的位置。
    """
    text = os.fspath(path)
    if "\x00" in text:
        raise ValueError(f"路徑不合法：{path}")
    candidate = pathlib.PurePath(text)
    if candidate.is_absolute():
        candidate = _inside_root(context.workdir, candidate, path)
    normal = os.path.normpath(candidate)
    parts = () if normal == "." else pathlib.PurePath(normal).parts
    if parts and parts[0] == "..":
        raise ValueError(f"路徑超出專案資料夾：{path}")
    _check_parts(context, parts, path)
    return tuple(parts)


def visible(context: datatypes.RunContext, parts: tuple[str, ...]) -> bool:
    """這個位置（各層名稱）是不是內建工具可以看到的。"""
    try:
        _check_parts(context, parts, "/".join(parts))
    except ValueError:
        return False
    return True


def resolve_path(context: datatypes.RunContext, path: str) -> pathlib.Path:
    """把相對路徑轉成工作目錄底下的絕對路徑（會跟著捷徑解析）。

    只用來決定位置（例如執行指令的目錄）；讀寫檔案請用 open_path，
    解析與打開之間檔案可能被換掉。

    Args:
        context: 這次 run 的環境資訊。
        path: 相對於工作目錄的路徑。

    Returns:
        解析後的絕對路徑。

    Raises:
        ValueError: 路徑跑到工作目錄之外、指向存放金鑰的資料夾，或指向
            context 不讓碰的位置。
    """
    parts = relative_parts(context, path)
    root = context.workdir.resolve()
    target = root.joinpath(*parts).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"路徑超出專案資料夾：{path}")
    _check_parts(context, target.relative_to(root).parts, path)
    return target


def open_dir(
    root: pathlib.Path, parts: tuple[str, ...], create: bool = False
) -> int:
    """從 root 開始一層一層打開資料夾，任何一層是捷徑都拒絕。

    Args:
        root: 起點（由伺服器決定的資料夾，本身不檢查捷徑）。
        parts: 相對於 root 的各層名稱，必須已經整理過（不含 . 與 ..）。
        create: 不存在的資料夾是否逐層建立。

    Returns:
        最後一層資料夾的 fd，用完要自己 os.close。

    Raises:
        LinkError: 有一層是捷徑。
        OSError: 資料夾不存在（create 為 False 時）、有一層不是資料夾
            等其他錯誤。
    """
    fd = os.open(root, DIR_FLAGS)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, 0o777, dir_fd=fd)
                except FileExistsError:
                    pass
            child = _open_at(fd, part, DIR_FLAGS | _NOFOLLOW, 0, part)
            os.close(fd)
            fd = child
    except BaseException:
        os.close(fd)
        raise
    return fd


def open_path(
    context: datatypes.RunContext,
    path: str,
    flags: int,
    mode: int = 0o666,
    create_parents: bool = False,
) -> int:
    """打開工作目錄裡的檔案或資料夾，路徑上的捷徑一律拒絕。

    Args:
        context: 這次 run 的環境資訊。
        path: 相對於工作目錄的路徑。
        flags: os.open 的 flags（會自動加上 O_NOFOLLOW）。
        mode: 建立檔案時的權限。
        create_parents: 是否逐層建立不存在的上層資料夾（不跟著捷徑）。

    Returns:
        fd，用完要自己關閉。

    Raises:
        ValueError: 路徑不能存取（超出範圍、金鑰、不讓碰的位置）。
        LinkError: 路徑上有捷徑。
        OSError: 檔案不存在等其他錯誤。
    """
    parts = relative_parts(context, path)
    if not SAFE_OPEN:
        target = resolve_path(context, path)
        if create_parents:
            target.parent.mkdir(parents=True, exist_ok=True)
        return os.open(target, flags, mode)
    if not parts:
        return os.open(context.workdir, flags)
    directory = open_dir(context.workdir, parts[:-1], create_parents)
    try:
        return _open_at(directory, parts[-1], flags | _NOFOLLOW, mode, path)
    finally:
        os.close(directory)


def read_text(
    context: datatypes.RunContext,
    path: str,
    newline: str | None = None,
    errors: str = "strict",
) -> str:
    """讀取工作目錄裡的文字檔（UTF-8），路徑上的捷徑一律拒絕。

    Raises:
        ValueError: 路徑不能存取，或不是一般的檔案。
        OSError: 檔案不存在、是資料夾等。
    """
    fd = open_path(context, path, READ_FLAGS)
    with os.fdopen(fd, encoding="utf-8", newline=newline, errors=errors) as f:
        require_file(f.fileno(), path)
        return f.read()


def write_text(
    context: datatypes.RunContext,
    path: str,
    text: str,
    newline: str | None = None,
) -> None:
    """把文字寫進工作目錄裡的檔案，上層資料夾不存在時逐層建立。

    Raises:
        ValueError: 路徑不能存取，或不是一般的檔案。
        OSError: 寫入失敗。
    """
    fd = open_path(context, path, WRITE_FLAGS, create_parents=True)
    with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as file:
        require_file(file.fileno(), path)
        file.write(text)


def list_entries(context: datatypes.RunContext, path: str) -> list[Entry]:
    """列出工作目錄裡的一個資料夾；捷徑與不讓看的項目不會列出。

    Raises:
        ValueError: 路徑不能存取。
        OSError: 資料夾不存在或不是資料夾。
    """
    parts = relative_parts(context, path)
    if not SAFE_OPEN:
        return _list_by_path(context, path, parts)
    fd = open_path(context, path, DIR_FLAGS)
    try:
        with os.scandir(fd) as iterator:
            items = list(iterator)
        entries = []
        for item in items:
            if not visible(context, (*parts, item.name)):
                continue
            try:
                info = os.stat(item.name, dir_fd=fd, follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISLNK(info.st_mode):
                continue
            entries.append(_entry(item.name, info))
    finally:
        os.close(fd)
    return entries


def require_file(fd: int, path: str) -> os.stat_result:
    """確認 fd 是一般的檔案（不是資料夾、FIFO 或裝置）。

    Returns:
        檔案的 stat。

    Raises:
        IsADirectoryError: 是資料夾。
        ValueError: 不是一般的檔案。
    """
    info = os.fstat(fd)
    if stat.S_ISDIR(info.st_mode):
        raise IsADirectoryError(errno.EISDIR, "是資料夾，不是檔案", path)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"不是一般的檔案：{path}")
    return info


def split_lines(text: str) -> list[str]:
    """把文字切成行；結尾的換行不算多出一行，空字串沒有任何一行。

    Args:
        text: 要切的文字。

    Returns:
        每一行的內容（不含換行字元）。
    """
    if not text:
        return []
    body = text[:-1] if text.endswith("\n") else text
    return body.split("\n")


def _inside_root(
    root: pathlib.Path, candidate: pathlib.PurePath, shown: str
) -> pathlib.PurePath:
    """工作目錄底下的絕對路徑轉成相對路徑；在外面時拒絕。"""
    for base in (root.absolute(), root.resolve()):
        try:
            return candidate.relative_to(base)
        except ValueError:
            continue
    raise ValueError(f"路徑超出專案資料夾：{shown}")


def _check_parts(
    context: datatypes.RunContext, parts: tuple[str, ...], shown: str
) -> None:
    secrets = SECRETS_DIR.parts
    if tuple(parts[: len(secrets)]) == secrets:
        raise ValueError(f"不能存取存放金鑰的資料夾：{shown}")
    if parts and parts[0] in context.hidden:
        raise ValueError(f"不能存取這個位置：{shown}")
    if context.hide_dotfiles and any(
        part.startswith(".") and part not in (".", "..") for part in parts
    ):
        raise ValueError(f"不能存取以點開頭的檔案或資料夾：{shown}")


def _open_at(directory: int, name: str, flags: int, mode: int, shown) -> int:
    """在 directory 裡打開 name；是捷徑時改丟 LinkError。"""
    try:
        return os.open(name, flags, mode, dir_fd=directory)
    except OSError as error:
        if error.errno not in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
            raise
        try:
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except OSError:
            raise error from None
        if stat.S_ISLNK(info.st_mode):
            raise LinkError(f"不能透過捷徑（symlink）存取：{shown}") from error
        raise


def _entry(name: str, info: os.stat_result) -> Entry:
    return Entry(
        name=name,
        is_dir=stat.S_ISDIR(info.st_mode),
        size=info.st_size,
        modified=info.st_mtime,
    )


def _list_by_path(
    context: datatypes.RunContext, path: str, parts: tuple[str, ...]
) -> list[Entry]:
    """不支援 dir_fd 的平台：依解析後的位置列出。"""
    target = resolve_path(context, path)
    entries = []
    for child in target.iterdir():
        if not visible(context, (*parts, child.name)):
            continue
        try:
            info = child.lstat()
        except OSError:
            continue
        if not stat.S_ISLNK(info.st_mode):
            entries.append(_entry(child.name, info))
    return entries
