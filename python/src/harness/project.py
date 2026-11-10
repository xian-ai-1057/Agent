"""載入專案資料夾。

專案格式（與 TypeScript 版共用同一份設定）：
    my-project/
    ├── harness.yaml    名稱、模型、預設 agent、權限
    ├── AGENTS.md       專案說明，會接在每個 agent 的指示後面（可省略）
    ├── agents/*.md     一個檔案一個 agent：frontmatter 是設定，內文是指示
    └── tools/*.py      用 @tool 包裝的函式，檔名就是 toolset 名稱

agent 的 frontmatter 可以用 subagents 與 handoffs 列出其他 agent 的名稱，
讓它把工作交出去或把對話轉交出去（見 delegation 模組）。

harness.yaml 裡的 mcpServers 是給 TypeScript 版用的，這裡不會啟動；
被它當成伺服器執行的 tools/*.py 也不會被當成工具模組載入。
"""

import dataclasses
import importlib.util
import pathlib
import sys
from typing import Any

import yaml

from harness import agents
from harness import builtin
from harness import permissions as permissions_lib
from harness import tools as tools_lib

CONFIG_FILE = "harness.yaml"
SESSIONS_DIR = pathlib.Path(".harness") / "sessions"


@dataclasses.dataclass
class Project:
    """一個載入完成的專案。

    Attributes:
        name: 專案名稱。
        root: 專案根目錄。
        agents: 專案裡所有的 agent，以名稱為鍵。
        tools: 專案 tools/ 底下所有的工具，以名稱為鍵。
        permissions: 專案的權限設定。
        default_agent: 沒指定時使用的 agent 名稱。
    """

    name: str
    root: pathlib.Path
    agents: dict[str, agents.Agent]
    tools: dict[str, tools_lib.Tool]
    permissions: permissions_lib.Permissions
    default_agent: str

    @property
    def sessions_dir(self) -> pathlib.Path:
        """這個專案存放 session 檔案的資料夾。"""
        return self.root / SESSIONS_DIR


def load_project(path: str | pathlib.Path = ".") -> Project:
    """讀取專案資料夾。

    Args:
        path: 專案根目錄（裡面要有 harness.yaml）。

    Returns:
        載入完成的 Project。

    Raises:
        FileNotFoundError: 找不到 harness.yaml。
        ValueError: 設定內容有誤（沒有 agent、引用不存在的工具…）。
    """
    root = pathlib.Path(path).resolve()
    config_path = root / CONFIG_FILE
    if not config_path.exists():
        raise FileNotFoundError(
            f"{root} 裡沒有 {CONFIG_FILE}，可用 `pyharness new` 建立專案"
        )
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    name = config.get("name") or root.name

    servers = config.get("mcpServers") or {}
    toolsets = dict(builtin.TOOLSETS)
    toolsets.update(_load_toolsets(root, name, _server_scripts(root, servers)))
    project_tools = {
        item.name: item
        for set_name, items in toolsets.items()
        if set_name not in builtin.TOOLSETS
        for item in items
    }

    shared = _read_optional(root / "AGENTS.md")
    loaded = {}
    metas = {}
    for agent_path in sorted((root / "agents").glob("*.md")):
        agent, meta = _load_agent(
            agent_path, config, shared, toolsets, project_tools, servers
        )
        loaded[agent.name] = agent
        metas[agent.name] = meta
    if not loaded:
        raise ValueError(f"{root / 'agents'} 裡沒有任何 agent（*.md）")
    _link_agents(loaded, metas)

    default_agent = config.get("defaultAgent") or next(iter(loaded))
    if default_agent not in loaded:
        raise ValueError(f"defaultAgent 指向不存在的 agent：{default_agent}")
    return Project(
        name=name,
        root=root,
        agents=loaded,
        tools=project_tools,
        permissions=_load_permissions(config.get("permissions") or {}),
        default_agent=default_agent,
    )


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """把 Markdown 檔拆成開頭的 YAML 設定與內文。

    Args:
        text: 檔案內容。

    Returns:
        (設定, 內文)；沒有 frontmatter 時設定為空 dict。
    """
    if not text.startswith("---"):
        return {}, text.strip()
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text.strip()
    return yaml.safe_load(parts[1]) or {}, parts[2].strip()


def resolve_tools(
    names: list[str],
    toolsets: dict[str, list[tools_lib.Tool]],
    project_tools: dict[str, tools_lib.Tool],
) -> list[tools_lib.Tool]:
    """把 agent 設定裡的名稱換成實際的工具。

    名稱可以是 toolset（tools/ 底下的檔名，或內建的 files）或單一工具。

    Args:
        names: agent 設定的 tools 欄位。
        toolsets: 所有可用的 toolset。
        project_tools: 專案裡所有的工具。

    Returns:
        不重複的工具列表，順序依設定。

    Raises:
        ValueError: 名稱既不是 toolset 也不是工具。
    """
    resolved: dict[str, tools_lib.Tool] = {}
    for name in names:
        if name in toolsets:
            found = toolsets[name]
        elif name in project_tools:
            found = [project_tools[name]]
        else:
            known = sorted({*toolsets, *project_tools})
            raise ValueError(f"找不到工具或 toolset：{name}，可用的有：{known}")
        for item in found:
            resolved[item.name] = item
    return list(resolved.values())


def _read_optional(path: pathlib.Path) -> str:
    """讀取可能不存在的文字檔。"""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _server_scripts(
    root: pathlib.Path, servers: dict[str, Any]
) -> set[pathlib.Path]:
    """找出被 mcpServers 當成伺服器執行的檔案。"""
    scripts = set()
    for server in servers.values():
        for argument in (server or {}).get("args") or []:
            scripts.add((root / str(argument)).resolve())
    return scripts


def _load_toolsets(
    root: pathlib.Path, project_name: str, skip: set[pathlib.Path]
) -> dict[str, list[tools_lib.Tool]]:
    """載入 tools/ 底下每個 Python 檔裡的工具。"""
    # 讓工具檔可以直接 import 專案裡既有的套件。
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    toolsets = {}
    for path in sorted((root / "tools").glob("*.py")):
        if path.name.startswith("_") or path.resolve() in skip:
            continue
        module_name = f"_harness_tools_{project_name}_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        # 先註冊模組，工具檔裡的 dataclass 與型別註解才能正確解析。
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        found = {
            id(value): value
            for value in vars(module).values()
            if isinstance(value, tools_lib.Tool)
        }
        toolsets[path.stem] = list(found.values())
    return toolsets


def _load_agent(
    path: pathlib.Path,
    config: dict[str, Any],
    shared: str,
    toolsets: dict[str, list[tools_lib.Tool]],
    project_tools: dict[str, tools_lib.Tool],
    servers: dict[str, Any],
) -> tuple[agents.Agent, dict[str, Any]]:
    """讀取一個 agent 的 Markdown 檔，回傳 agent 與它開頭的設定。"""
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    # 沒寫 tools 欄位時，預設可以使用專案裡所有的工具。
    if "tools" in meta:
        names = meta["tools"] or []
        _check_server_only(names, toolsets, project_tools, servers)
        agent_tools = resolve_tools(names, toolsets, project_tools)
    else:
        agent_tools = list(project_tools.values())
    instructions = "\n\n".join(part for part in (body, shared) if part)
    agent = agents.Agent(
        name=meta.get("name") or path.stem,
        instructions=instructions,
        tools=agent_tools,
        model=meta.get("model") or config.get("model"),
        model_params=meta.get("modelParams") or config.get("modelParams") or {},
        max_turns=meta.get("maxTurns") or config.get("maxTurns") or 20,
        description=meta.get("description") or "",
    )
    return agent, meta


def _link_agents(
    loaded: dict[str, agents.Agent], metas: dict[str, dict[str, Any]]
) -> None:
    """把各 agent 設定裡的 subagents / handoffs 名稱換成實際的 agent。

    要等所有 agent 都讀完才能做，因為 agent 之間可以互相引用。
    """
    for name, meta in metas.items():
        for field in ("subagents", "handoffs"):
            targets = []
            for target in meta.get(field) or []:
                if target not in loaded:
                    raise ValueError(
                        f"agent {name} 的 {field} 引用不存在的 agent："
                        f"{target}，可用的有：{sorted(loaded)}"
                    )
                if field == "handoffs" and target == name:
                    raise ValueError(f"agent {name} 不能把對話轉交給自己")
                targets.append(loaded[target])
            setattr(loaded[name], field, targets)


def _check_server_only(
    names: list[str],
    toolsets: dict[str, list[tools_lib.Tool]],
    project_tools: dict[str, tools_lib.Tool],
    servers: dict[str, Any],
) -> None:
    """agent 引用只存在於 mcpServers 的 toolset 時，說明原因。"""
    for name in names:
        if name in servers and name not in {*toolsets, *project_tools}:
            raise ValueError(
                f"toolset {name} 是 mcpServers 裡的伺服器，只有 TypeScript 版"
                f"會啟動；Python 版請在 tools/{name}.py 用 @harness.tool "
                "直接包裝函式"
            )


def _load_permissions(config: dict[str, Any]) -> permissions_lib.Permissions:
    """把 harness.yaml 的 permissions 區塊轉成 Permissions。"""
    return permissions_lib.Permissions(
        mode=config.get("mode") or "ask",
        allow=list(config.get("allow") or []),
        deny=list(config.get("deny") or []),
        ask=list(config.get("ask") or []),
    )
