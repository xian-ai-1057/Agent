"""網頁版的工作資料夾。

每段對話選一個工作資料夾，agent 的檔案工具、執行指令與沙盒都只看得到
這個資料夾，上傳的檔案也存在裡面：

    private  每個人自己的「我的檔案」：專案的 private/<使用者識別碼>/，
             只有本人看得到，第一次使用時建立
    shared   管理者在 harness.yaml 的 workspaces 開放的資料夾

    workspaces:
      - name: 營收分析
        path: workspace/revenue   # 相對於專案，或絕對路徑（掛載的資料夾）
        members: all              # 或使用者名稱的清單；管理者都看得到
        writable: true            # false 時 agent 只能讀

沒有寫 workspaces 時，有一個所有人共用的「共用」資料夾：專案的
workspace/（有設定沙盒時是 sandbox.workspace）。

共用資料夾不能和 .harness/、private/、專案的設定與程式（agents/、
tools/、skills/、harness.yaml、AGENTS.md）重疊，彼此也不能互相包含；
寫錯時伺服器啟動就會報錯。

誰能用哪些資料夾只在這裡判斷（Folders.for_user 與 Folders.find）。
"""

import dataclasses
import pathlib
import re
from typing import Any

from harness import project as project_lib
from harness import sandbox as sandbox_lib
from harness.web import accounts as accounts_lib
from harness.web import config as config_lib

PRIVATE_ID = "private"
PRIVATE_NAME = "我的檔案"
PRIVATE_DIR = "private"
SHARED_PREFIX = "shared:"
DEFAULT_SHARED_NAME = "共用"
CONFIG_KEY = "workspaces"
# 共用資料夾不能碰到的位置：harness 自己的資料與專案的設定、程式。
_PROTECTED = (
    project_lib.DATA_DIR.as_posix(),
    PRIVATE_DIR,
    project_lib.CONFIG_FILE,
    "AGENTS.md",
    "agents",
    "tools",
    "skills",
)
_FIELDS = ("id", "name", "path", "members", "writable")
_SLUG = re.compile(r"[^\w-]+")


@dataclasses.dataclass(frozen=True)
class Folder:
    """一個工作資料夾。

    Attributes:
        id: 識別碼：private 或 shared:<名稱>。
        name: 顯示名稱。
        kind: private 或 shared。
        writable: False 時 agent 與上傳都不能寫入。
        path: 資料夾的絕對路徑。
        members: 可以使用的使用者名稱（小寫）；None 表示所有人。
    """

    id: str
    name: str
    kind: str
    writable: bool
    path: pathlib.Path
    members: frozenset[str] | None = None

    def info(self) -> dict[str, Any]:
        """FolderInfo（見 src/web/protocol.ts）。"""
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "writable": self.writable,
        }


class Folders:
    """一個專案的工作資料夾，以及誰可以使用哪些資料夾。

    Attributes:
        root: 專案根目錄。
        shared: 共用資料夾，依 harness.yaml 的順序。
    """

    def __init__(self, root: pathlib.Path, shared: list[Folder]) -> None:
        self.root = root.resolve()
        self.shared = shared

    @classmethod
    def for_project(cls, project: project_lib.Project) -> "Folders":
        """讀取 harness.yaml 的 workspaces。

        Raises:
            ValueError: workspaces 寫錯，或資料夾和專案的設定重疊。
        """
        config = config_lib.read_config(project.root)
        default = sandbox_lib.DEFAULT_WORKSPACE
        if project.sandbox is not None:
            workspace = project.sandbox.workspace
            root = project.root.resolve()
            default = (
                workspace.relative_to(root).as_posix()
                if workspace.is_relative_to(root)
                else workspace.as_posix()
            )
        return cls(project.root, load_shared(project.root, config, default))

    def private(self, user: accounts_lib.User) -> Folder:
        """這個人自己的「我的檔案」。"""
        return Folder(
            id=PRIVATE_ID,
            name=PRIVATE_NAME,
            kind="private",
            writable=True,
            path=self.root / PRIVATE_DIR / user.id,
        )

    def for_user(self, user: accounts_lib.User) -> list[Folder]:
        """這個人可以使用的資料夾，自己的「我的檔案」排第一個。"""
        visible = [
            folder
            for folder in self.shared
            if user.is_admin
            or folder.members is None
            or user.username.lower() in folder.members
        ]
        return [self.private(user), *visible]

    def find(self, user: accounts_lib.User, folder_id: Any) -> Folder | None:
        """找出這個人可以使用的資料夾。

        Args:
            user: 使用者。
            folder_id: FolderInfo.id；None 或空字串表示「我的檔案」。

        Returns:
            資料夾；不存在或這個人不能使用時為 None。
        """
        if folder_id is None or folder_id == "":
            folder_id = PRIVATE_ID
        if not isinstance(folder_id, str):
            return None
        for folder in self.for_user(user):
            if folder.id == folder_id:
                return folder
        return None


def ensure(folder: Folder) -> pathlib.Path:
    """確保資料夾存在（「我的檔案」第一次使用時才建立），回傳它的路徑。"""
    folder.path.mkdir(parents=True, exist_ok=True)
    return folder.path


def load_shared(
    root: pathlib.Path, config: dict[str, Any], default_path: str
) -> list[Folder]:
    """依 harness.yaml 的 workspaces 建立共用資料夾。

    Args:
        root: 專案根目錄。
        config: harness.yaml 的內容。
        default_path: 沒有寫 workspaces 時「共用」資料夾的位置。

    Returns:
        共用資料夾。

    Raises:
        ValueError: 設定寫錯，或資料夾和專案的設定重疊。
    """
    root = root.resolve()
    entries = config.get(CONFIG_KEY)
    if entries is None:
        entries = [{"name": DEFAULT_SHARED_NAME, "path": default_path}]
    if not isinstance(entries, list):
        raise ValueError(f"{CONFIG_KEY} 必須是清單")
    folders: list[Folder] = []
    for index, entry in enumerate(entries):
        folder = _folder(root, entry, index)
        if any(item.id == folder.id for item in folders):
            raise ValueError(
                f"{CONFIG_KEY} 有兩個資料夾的識別碼都是 {folder.id}，"
                "請用 id 分開"
            )
        for other in folders:
            if _overlaps(folder.path, other.path):
                raise ValueError(
                    f"{CONFIG_KEY} 的 {folder.name} 和 {other.name} 互相包含，"
                    "共用資料夾不能重疊"
                )
        folders.append(folder)
    return folders


def _folder(root: pathlib.Path, entry: Any, index: int) -> Folder:
    """檢查 workspaces 的一項並建立 Folder。"""
    where = f"{CONFIG_KEY} 第 {index + 1} 項"
    if not isinstance(entry, dict):
        raise ValueError(f"{where} 必須是含 name 與 path 的設定")
    unknown = sorted(set(entry) - set(_FIELDS))
    if unknown:
        raise ValueError(
            f"{where} 有不認得的設定：{unknown}，可用的有：{list(_FIELDS)}"
        )
    name = entry.get("name")
    raw_path = entry.get("path")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{where} 缺少 name")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"{where}（{name}）缺少 path")
    writable = entry.get("writable", True)
    if not isinstance(writable, bool):
        raise ValueError(f"{where}（{name}）的 writable 必須是 true 或 false")
    path = (root / raw_path.strip()).resolve()
    _check_path(root, path, name)
    slug = entry.get("id")
    if slug is None:
        slug = _SLUG.sub("-", raw_path.strip()).strip("-") or str(index + 1)
    if not isinstance(slug, str) or not slug.strip():
        raise ValueError(f"{where}（{name}）的 id 必須是文字")
    return Folder(
        id=f"{SHARED_PREFIX}{slug.strip()}",
        name=name.strip(),
        kind="shared",
        writable=writable,
        path=path,
        members=_members(entry.get("members", "all"), where, name),
    )


def _members(value: Any, where: str, name: str) -> frozenset[str] | None:
    if value == "all":
        return None
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise ValueError(
            f"{where}（{name}）的 members 必須是 all 或使用者名稱的清單"
        )
    return frozenset(item.strip().lower() for item in value if item.strip())


def _check_path(root: pathlib.Path, path: pathlib.Path, name: str) -> None:
    """共用資料夾不能和 harness 的資料或專案的設定、程式重疊。"""
    for protected in _PROTECTED:
        target = root / protected
        if _overlaps(path, target):
            raise ValueError(
                f"{CONFIG_KEY} 的 {name}（{path}）和 {protected} 重疊：agent "
                "可以改寫專案的設定或讀到別人的資料，請改用專用的資料夾，"
                "例如 workspace/"
            )


def _overlaps(a: pathlib.Path, b: pathlib.Path) -> bool:
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)
