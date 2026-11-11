"""存在專案裡的連線設定與登入密碼。

讓部署的人不必設定環境變數：模型的金鑰可以在網頁上填寫，登入密碼沒有
指定時自動產生。兩者都存在專案的 .harness/secrets/ 裡：

    .harness/secrets/env.json       要套用到環境變數的值（金鑰、端點網址）
    .harness/secrets/web-password   網頁版自動產生的登入密碼

資料夾只有執行 pyharness 的使用者能讀（0700 / 0600），內建的檔案工具
也不能讀寫這個資料夾（見 builtin.paths）。

存起來的值在命令列啟動時套用到 os.environ，所以優先於啟動時的環境
變數：使用者在網頁上改過金鑰，重新啟動後仍然有效。空字串表示「不要
使用這個環境變數」，用來蓋掉啟動時設定的 HARNESS_BASE_URL。
"""

import json
import os
import pathlib
import secrets

from harness import project as project_lib

SECRETS_DIR = project_lib.DATA_DIR / "secrets"
ENV_FILE = "env.json"
PASSWORD_FILE = "web-password"
_PASSWORD_BYTES = 18


def secrets_dir(root: str | pathlib.Path) -> pathlib.Path:
    """專案存放金鑰與密碼的資料夾。"""
    return pathlib.Path(root) / SECRETS_DIR


def load(root: str | pathlib.Path) -> dict[str, str]:
    """讀取存起來的環境變數。

    Args:
        root: 專案根目錄。

    Returns:
        以環境變數名稱為鍵的值；檔案不存在或內容損壞時為空 dict。
    """
    try:
        text = (secrets_dir(root) / ENV_FILE).read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        name: value
        for name, value in data.items()
        if isinstance(name, str) and isinstance(value, str)
    }


def apply(root: str | pathlib.Path) -> list[str]:
    """把存起來的值套用到 os.environ。

    Args:
        root: 專案根目錄。

    Returns:
        套用了哪些環境變數。
    """
    saved = load(root)
    _set_environment(saved)
    return sorted(saved)


def update(root: str | pathlib.Path, values: dict[str, str | None]) -> None:
    """儲存環境變數並立刻套用到 os.environ。

    Args:
        root: 專案根目錄。
        values: 要改的環境變數。值是空字串時表示不使用這個變數（即使
            啟動時有設定）；None 表示刪掉存起來的值，os.environ 維持
            目前的內容。
    """
    saved = load(root)
    changed: dict[str, str] = {}
    for name, value in values.items():
        if value is None:
            saved.pop(name, None)
        else:
            saved[name] = value
            changed[name] = value
    _write_private(
        secrets_dir(root) / ENV_FILE,
        json.dumps(saved, ensure_ascii=False, indent=2) + "\n",
    )
    _set_environment(changed)


def web_password(root: str | pathlib.Path) -> tuple[str, bool]:
    """取得網頁版的登入密碼，沒有的話產生一組並存起來。

    Args:
        root: 專案根目錄。

    Returns:
        (密碼, 是不是這次才產生的)。
    """
    path = secrets_dir(root) / PASSWORD_FILE
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if existing:
        return existing, False
    password = secrets.token_urlsafe(_PASSWORD_BYTES)
    _write_private(path, password + "\n")
    return password, True


def _set_environment(values: dict[str, str]) -> None:
    for name, value in values.items():
        if value:
            os.environ[name] = value
        else:
            os.environ.pop(name, None)


def _write_private(path: pathlib.Path, text: str) -> None:
    """寫入只有擁有者能讀的檔案。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    # 先以 0600 建立再寫入，內容不會有一瞬間能被其他使用者讀到。
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(text)
    path.chmod(0o600)
