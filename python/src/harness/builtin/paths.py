"""內建工具共用的路徑處理。"""

import pathlib

from harness import datatypes

# 存放金鑰與登入密碼的資料夾（見 harness.credentials），內建工具不能碰。
SECRETS_DIR = pathlib.Path(".harness") / "secrets"


def resolve_path(context: datatypes.RunContext, path: str) -> pathlib.Path:
    """把相對路徑轉成專案根目錄底下的絕對路徑。

    Args:
        context: 這次 run 的環境資訊。
        path: 相對於專案根目錄的路徑。

    Returns:
        解析後的絕對路徑。

    Raises:
        ValueError: 路徑跑到專案根目錄之外，或指向存放金鑰的資料夾。
    """
    root = context.workdir.resolve()
    target = (root / path).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"路徑超出專案資料夾：{path}")
    secrets = root / SECRETS_DIR
    if target == secrets or secrets in target.parents:
        raise ValueError(f"不能存取存放金鑰的資料夾：{path}")
    return target


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
