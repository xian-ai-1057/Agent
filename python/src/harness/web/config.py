"""網頁版的設定讀寫：直接改 harness.yaml，保留檔案裡原本的註解與排版。

PyYAML 寫回檔案時會丟掉所有註解，所以這裡只改需要改的那幾行：
頂層的 model、maxTurns，以及 permissions 區塊裡的 mode、allow、ask、
deny。改完會重新解析一次，確認結果和預期相同才寫入。
"""

import json
import pathlib
import re
from typing import Any

import yaml

from harness import project as project_lib

PERMISSION_KEYS = ("mode", "allow", "ask", "deny")


def read_config(root: pathlib.Path) -> dict[str, Any]:
    """讀取 harness.yaml 的內容。

    Args:
        root: 專案根目錄。

    Returns:
        設定內容；檔案是空的時為空 dict。
    """
    text = _path(root).read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}


def read_model_settings(root: pathlib.Path) -> dict[str, Any]:
    """讀取 harness.yaml 裡跟模型有關、網頁上可以修改的欄位。

    Args:
        root: 專案根目錄。

    Returns:
        {"model": str | None, "maxTurns": int | None}。
    """
    config = read_config(root)
    model = config.get("model")
    max_turns = config.get("maxTurns")
    return {
        "model": model if isinstance(model, str) else None,
        "maxTurns": max_turns
        if isinstance(max_turns, int) and not isinstance(max_turns, bool)
        else None,
    }


def save_model_settings(
    root: pathlib.Path, model: str | None, max_turns: int | None
) -> None:
    """把 model 與 maxTurns 寫回 harness.yaml；None 表示刪掉這個欄位。

    Args:
        root: 專案根目錄。
        model: 模型名稱。
        max_turns: 一輪最多呼叫模型幾次。

    Raises:
        ValueError: 檔案格式特殊，無法安全地只改這幾行。
    """
    lines = _read_lines(root)
    _set_top_level(lines, "model", _scalar(model))
    _set_top_level(lines, "maxTurns", _scalar(max_turns))
    _write_checked(
        root,
        lines,
        lambda config: (
            config.get("model") == model and config.get("maxTurns") == max_turns
        ),
    )


def save_permissions(root: pathlib.Path, settings: dict[str, Any]) -> None:
    """把權限設定寫回 harness.yaml；空的規則清單會整行拿掉。

    Args:
        root: 專案根目錄。
        settings: {"mode": str, "allow": [...], "ask": [...], "deny": [...]}。

    Raises:
        ValueError: 檔案格式特殊，無法安全地只改這幾行。
    """
    lines = _read_lines(root)
    values = {"mode": settings["mode"]}
    for key in ("allow", "ask", "deny"):
        values[key] = _flow_list(settings[key]) if settings[key] else None
    _set_permissions(lines, values)

    def matches(config: dict[str, Any]) -> bool:
        written = config.get("permissions") or {}
        return all(
            (written.get(key) or ([] if key != "mode" else None))
            == settings[key]
            for key in PERMISSION_KEYS
        )

    _write_checked(root, lines, matches)


def read_agent_overrides(root: pathlib.Path) -> dict[str, dict[str, Any]]:
    """讀取每個 agent 檔案自己寫的 model 與 maxTurns。

    載入後的 Agent 已經把 harness.yaml 的值合併進去，分不出來源，所以
    要回頭看 agent 檔案，才知道改 harness.yaml 會影響哪些 agent。

    Args:
        root: 專案根目錄。

    Returns:
        以 agent 名稱為鍵，值為 {"model": ..., "maxTurns": ...}（只含
        agent 檔案有寫的欄位）。
    """
    overrides: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "agents").glob("*.md")):
        meta, _ = project_lib.parse_frontmatter(path.read_text("utf-8"))
        found = {}
        if isinstance(meta.get("model"), str):
            found["model"] = meta["model"]
        if isinstance(meta.get("maxTurns"), int):
            found["maxTurns"] = meta["maxTurns"]
        overrides[str(meta.get("name") or path.stem)] = found
    return overrides


def _path(root: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(root) / project_lib.CONFIG_FILE


def _read_lines(root: pathlib.Path) -> list[str]:
    return _path(root).read_text(encoding="utf-8").splitlines()


def _write_checked(root, lines, check) -> None:
    """重新解析改好的內容，確認正確後才寫入。"""
    text = "\n".join(lines) + "\n"
    try:
        config = yaml.safe_load(text) or {}
    except yaml.YAMLError as error:
        raise ValueError(_UNSAFE) from error
    if not isinstance(config, dict) or not check(config):
        raise ValueError(_UNSAFE)
    _path(root).write_text(text, encoding="utf-8")


_UNSAFE = (
    f"{project_lib.CONFIG_FILE} 的寫法比較特殊，無法安全地自動修改，"
    "請直接編輯檔案"
)


def _scalar(value: Any) -> str | None:
    """把值寫成 YAML 的純量；None 表示要刪掉。"""
    if value is None:
        return None
    if isinstance(value, int):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _flow_list(items: list[str]) -> str:
    """寫成 ["a", "b"]。加引號是因為 * 開頭在 YAML 裡代表別名。"""
    return (
        "[" + ", ".join(json.dumps(i, ensure_ascii=False) for i in items) + "]"
    )


def _indented(line: str) -> bool:
    return line[:1] in (" ", "\t")


def _is_content(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def _set_top_level(lines: list[str], key: str, value: str | None) -> None:
    """設定或刪除頂層的單行欄位。"""
    pattern = re.compile(rf"^{re.escape(key)}\s*:")
    for index, line in enumerate(lines):
        if pattern.match(line):
            if value is None:
                del lines[index]
            else:
                lines[index] = f"{key}: {value}"
            return
    if value is None:
        return
    # 放在註解掉的範例（例如 "# model: ..."）後面，否則放在最上面的
    # name 或 defaultAgent 後面。
    commented = re.compile(rf"^#\s*{re.escape(key)}\s*:")
    anchors = [i for i, line in enumerate(lines) if commented.match(line)]
    if not anchors:
        anchors = [
            i
            for i, line in enumerate(lines)
            if re.match(r"^(name|defaultAgent)\s*:", line)
        ]
    position = anchors[-1] + 1 if anchors else len(lines)
    lines.insert(position, f"{key}: {value}")


def _set_permissions(lines: list[str], values: dict[str, str | None]) -> None:
    """改寫 permissions 區塊裡的欄位，區塊裡的註解保持不動。"""
    start = next(
        (
            i
            for i, line in enumerate(lines)
            if re.match(r"^permissions\s*:", line)
        ),
        None,
    )
    if start is not None and not re.match(
        r"^permissions\s*:\s*(#.*)?$", lines[start]
    ):
        # 寫成單行（permissions: {mode: ask}）時，整個換成區塊寫法。
        lines[start] = "permissions:"
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("permissions:")
        start = len(lines) - 1

    # 區塊到最後一行有縮排的內容為止；遇到頂層的欄位就結束。區塊後面
    # 緊接的頂層註解與空行屬於下一段，不算在區塊裡。
    end = start + 1
    scan = start + 1
    while scan < len(lines):
        line = lines[scan]
        if _is_content(line) and not _indented(line):
            break
        if line.strip() and _indented(line):
            end = scan + 1
        scan += 1

    indent = "  "
    for line in lines[start + 1 : end]:
        if _is_content(line):
            indent = line[: len(line) - len(line.lstrip())]
            break

    for key in PERMISSION_KEYS:
        pattern = re.compile(rf"^\s+{key}\s*:")
        found = next(
            (i for i in range(start + 1, end) if pattern.match(lines[i])), None
        )
        value = values[key]
        if found is None:
            if value is not None:
                lines.insert(end, f"{indent}{key}: {value}")
                end += 1
            continue
        # 這個欄位佔的行數：本身加上底下縮排更深的清單項目。
        key_indent = len(lines[found]) - len(lines[found].lstrip())
        stop = found + 1
        while stop < end:
            line = lines[stop]
            depth = len(line) - len(line.lstrip())
            if _is_content(line) and depth > key_indent:
                stop += 1
            elif (
                _is_content(line)
                and depth == key_indent
                and line.lstrip().startswith("- ")
            ):
                stop += 1
            else:
                break
        if value is None:
            del lines[found:stop]
            end -= stop - found
        else:
            lines[found:stop] = [f"{indent}{key}: {value}"]
            end -= stop - found - 1
