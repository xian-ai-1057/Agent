"""載入專案資料夾。

專案格式（與 TypeScript 版共用同一份設定）：
    my-project/
    ├── harness.yaml    名稱、模型、預設 agent、權限、知識庫、記憶、防護
    ├── AGENTS.md       專案說明，會接在每個 agent 的指示後面（可省略）
    ├── agents/*.md     一個檔案一個 agent：frontmatter 是設定，內文是指示
    ├── tools/*.py      用 @tool 包裝的函式，檔名就是 toolset 名稱
    ├── skills/*/       一個資料夾一個 skill（SKILL.md 加參考檔案）
    └── knowledge/      知識庫文件（.md、.txt、.pdf）

agent 的 frontmatter 可以用 subagents 與 handoffs 列出其他 agent 的名稱，
讓它把工作交出去或把對話轉交出去（見 delegation 模組）；用 skills 列出
可用的 skill；用 knowledge 掛上知識庫。

skills、knowledge、memory、guardrails、compaction 只有 Python 版支援，
TypeScript 版會忽略這些設定。

harness.yaml 的 mcpServers 會在載入時啟動並連線，伺服器名稱就是 toolset
名稱；用完專案要呼叫 Project.close()（或用 with）。被 mcpServers 當成伺服器
執行的 tools/*.py 不會被當成工具模組載入。若 tools/ 裡有與伺服器同名的
Python 檔（同一個專案同時給兩個版本用時的寫法），Python 版直接載入那個
檔案，不啟動伺服器。
"""

import dataclasses
import importlib.util
import pathlib
import sys
from typing import Any

import yaml

from harness import agents
from harness import builtin
from harness import compaction as compaction_lib
from harness import guardrails
from harness import hooks as hooks_lib
from harness import knowledge as knowledge_lib
from harness import mcp_servers
from harness import memory as memory_lib
from harness import permissions as permissions_lib
from harness import skills as skills_lib
from harness import tools as tools_lib

CONFIG_FILE = "harness.yaml"
DATA_DIR = pathlib.Path(".harness")
SESSIONS_DIR = DATA_DIR / "sessions"
MEMORY_FILE = DATA_DIR / "memory.json"
KNOWLEDGE_CACHE_DIR = DATA_DIR / "knowledge"
DEFAULT_KNOWLEDGE_DIR = "knowledge"


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
        skills: 專案 skills/ 底下所有的 skill，以名稱為鍵。
        knowledge: 專案的知識庫，沒有設定時為 None。
        memory: 長期記憶，harness.yaml 沒有開啟 memory 時為 None。
        hooks: 依 guardrails 設定建立的 hooks，執行時傳給 run；也可以
            再加上自己的處理函式。
        compaction: 對話壓縮的設定，預設開啟；harness.yaml 寫
            compaction: false 時為 None。
        mcp_connections: 依 mcpServers 建立的 MCP 連線。
        toolsets: 每個 toolset（含內建的 files）包含哪些工具，以 toolset
            名稱為鍵。
    """

    name: str
    root: pathlib.Path
    agents: dict[str, agents.Agent]
    tools: dict[str, tools_lib.Tool]
    permissions: permissions_lib.Permissions
    default_agent: str
    skills: dict[str, skills_lib.Skill] = dataclasses.field(
        default_factory=dict
    )
    knowledge: knowledge_lib.Knowledge | None = None
    memory: memory_lib.MemoryStore | None = None
    hooks: hooks_lib.Hooks = dataclasses.field(default_factory=hooks_lib.Hooks)
    compaction: compaction_lib.Compaction | None = None
    mcp_connections: list[mcp_servers.McpConnection] = dataclasses.field(
        default_factory=list
    )
    toolsets: dict[str, list[tools_lib.Tool]] = dataclasses.field(
        default_factory=dict
    )

    def __enter__(self) -> "Project":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        """關閉專案啟動的 MCP 伺服器。可以重複呼叫。"""
        _close_all(self.mcp_connections)

    @property
    def sessions_dir(self) -> pathlib.Path:
        """這個專案存放 session 檔案的資料夾。"""
        return self.root / SESSIONS_DIR


def load_project(path: str | pathlib.Path = ".") -> Project:
    """讀取專案資料夾。

    Args:
        path: 專案根目錄（裡面要有 harness.yaml）。

    Returns:
        載入完成的 Project。有設定 mcpServers 時，用完要呼叫 close()。

    Raises:
        FileNotFoundError: 找不到 harness.yaml。
        ValueError: 設定內容有誤（沒有 agent、引用不存在的工具…）。
        RuntimeError: MCP 伺服器無法啟動。
        ImportError: 設定了 mcpServers 但沒有安裝 mcp 套件。
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
    connections: list[mcp_servers.McpConnection] = []
    try:
        toolsets.update(_connect_servers(root, servers, toolsets, connections))
        return _assemble(root, name, config, toolsets, connections)
    except BaseException:
        _close_all(connections)
        raise


def _assemble(
    root: pathlib.Path,
    name: str,
    config: dict[str, Any],
    toolsets: dict[str, list[tools_lib.Tool]],
    connections: list[mcp_servers.McpConnection],
) -> Project:
    """工具都準備好之後，讀取 skills、知識庫與 agent，組成 Project。"""
    project_tools = {
        item.name: item
        for set_name, items in toolsets.items()
        if set_name not in builtin.TOOLSETS
        for item in items
    }

    project_skills = _load_skills(root, toolsets, project_tools)
    knowledge = _load_knowledge(root, config.get("knowledge"))
    resources = _Resources(toolsets, project_tools, project_skills, knowledge)

    shared = _read_optional(root / "AGENTS.md")
    loaded = {}
    metas = {}
    for agent_path in sorted((root / "agents").glob("*.md")):
        agent, meta = _load_agent(agent_path, config, shared, resources)
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
        skills=project_skills,
        knowledge=knowledge,
        memory=memory_lib.MemoryStore(root / MEMORY_FILE)
        if config.get("memory")
        else None,
        hooks=_load_guardrails(config.get("guardrails") or {}),
        compaction=_load_compaction(config.get("compaction")),
        mcp_connections=connections,
        toolsets=toolsets,
    )


@dataclasses.dataclass
class _Resources:
    """建立 agent 時可以引用的專案資源。"""

    toolsets: dict[str, list[tools_lib.Tool]]
    project_tools: dict[str, tools_lib.Tool]
    skills: dict[str, skills_lib.Skill]
    knowledge: knowledge_lib.Knowledge | None


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

    名稱可以是 toolset（tools/ 底下的檔名、MCP 伺服器名稱，或內建的
    files）或單一工具。

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


def _connect_servers(
    root: pathlib.Path,
    servers: dict[str, Any],
    toolsets: dict[str, list[tools_lib.Tool]],
    connections: list[mcp_servers.McpConnection],
) -> dict[str, list[tools_lib.Tool]]:
    """啟動 mcpServers 設定的伺服器，回傳各伺服器的工具。

    建立的連線會加進 connections，讓呼叫端在之後出錯時也能全部關閉。
    tools/ 裡已有同名 toolset 的伺服器不啟動，直接用那個 Python 檔。
    """
    if not isinstance(servers, dict):
        raise ValueError("mcpServers 必須是「名稱: 設定」的對應")
    existing = {
        item.name: set_name
        for set_name, items in toolsets.items()
        for item in items
    }
    found: dict[str, list[tools_lib.Tool]] = {}
    for server_name, server_config in servers.items():
        if server_name in toolsets:
            continue
        connection = mcp_servers.from_config(server_name, server_config, root)
        connections.append(connection)
        for item in connection.tools:
            if item.name in existing:
                raise ValueError(
                    f"MCP 伺服器 {server_name} 的工具 {item.name} 與 "
                    f"{existing[item.name]} 裡的工具同名"
                )
            existing[item.name] = server_name
        found[server_name] = connection.tools
    return found


def _close_all(connections: list[mcp_servers.McpConnection]) -> None:
    """關閉所有 MCP 連線。"""
    for connection in connections:
        connection.close()


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
    resources: _Resources,
) -> tuple[agents.Agent, dict[str, Any]]:
    """讀取一個 agent 的 Markdown 檔，回傳 agent 與它開頭的設定。"""
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    name = meta.get("name") or path.stem
    agent_skills = _resolve_skills(name, meta, resources.skills)
    if "tools" in meta:
        names = meta["tools"] or []
        agent_tools = resolve_tools(
            names, resources.toolsets, resources.project_tools
        )
    else:
        # 沒寫 tools 欄位時，預設可以使用專案裡所有的工具；但屬於 skill
        # 的工具要等 skill 載入後才開放，所以先排除。
        skill_owned = {item.name for s in agent_skills for item in s.tools}
        agent_tools = [
            item
            for item in resources.project_tools.values()
            if item.name not in skill_owned
        ]
    knowledge_tool = _knowledge_tool(name, meta, resources.knowledge)
    if knowledge_tool is not None:
        agent_tools.append(knowledge_tool)
    instructions = "\n\n".join(part for part in (body, shared) if part)
    agent = agents.Agent(
        name=name,
        instructions=instructions,
        tools=agent_tools,
        model=meta.get("model") or config.get("model"),
        model_params=meta.get("modelParams") or config.get("modelParams") or {},
        max_turns=meta.get("maxTurns") or config.get("maxTurns") or 20,
        description=meta.get("description") or "",
        skills=agent_skills,
    )
    return agent, meta


def _resolve_skills(
    agent_name: str,
    meta: dict[str, Any],
    available: dict[str, skills_lib.Skill],
) -> list[skills_lib.Skill]:
    """依 agent 設定的 skills 欄位選出 skill；沒寫時可以使用全部。"""
    if "skills" not in meta:
        return list(available.values())
    selected = []
    for skill_name in meta["skills"] or []:
        if skill_name not in available:
            raise ValueError(
                f"agent {agent_name} 引用不存在的 skill：{skill_name}，"
                f"可用的有：{sorted(available)}"
            )
        selected.append(available[skill_name])
    return selected


def _knowledge_tool(
    agent_name: str,
    meta: dict[str, Any],
    knowledge: knowledge_lib.Knowledge | None,
) -> tools_lib.Tool | None:
    """依 agent 設定的 knowledge 欄位建立 search_knowledge 工具。

    knowledge: true 表示可以搜尋整個知識庫；knowledge: {labels: [...]}
    限定只能搜尋這些標籤（與沒有標籤）的文件。
    """
    setting = meta.get("knowledge")
    if not setting:
        return None
    if knowledge is None:
        raise ValueError(
            f"agent {agent_name} 設定了 knowledge，但專案沒有知識庫：請建立 "
            f"{DEFAULT_KNOWLEDGE_DIR}/ 資料夾，或在 {CONFIG_FILE} 設定 "
            "knowledge.path"
        )
    if setting is True:
        return knowledge.as_tool()
    if not isinstance(setting, dict):
        raise ValueError(
            f"agent {agent_name} 的 knowledge 必須是 true 或 {{labels: [...]}}"
        )
    labels = setting.get("labels")
    top_k = setting.get("topK") or 5
    if labels is None:
        return knowledge.as_tool(top_k=top_k)
    return knowledge.as_tool(labels=[str(item) for item in labels], top_k=top_k)


def _load_skills(
    root: pathlib.Path,
    toolsets: dict[str, list[tools_lib.Tool]],
    project_tools: dict[str, tools_lib.Tool],
) -> dict[str, skills_lib.Skill]:
    """載入 skills/ 底下每個資料夾的 SKILL.md。"""
    loaded: dict[str, skills_lib.Skill] = {}
    for skill_file in sorted(
        (root / "skills").glob(f"*/{skills_lib.SKILL_FILE}")
    ):
        folder = skill_file.parent
        meta, body = parse_frontmatter(skill_file.read_text(encoding="utf-8"))
        name = str(meta.get("name") or folder.name)
        description = str(meta.get("description") or "").strip()
        if not description:
            raise ValueError(
                f"{skill_file} 缺少 description：agent 靠這段說明決定什麼時候"
                "載入這個 skill"
            )
        if name in loaded:
            raise ValueError(f"skill 名稱重複：{name}")
        loaded[name] = skills_lib.Skill(
            name=name,
            description=description,
            instructions=body,
            tools=resolve_tools(
                meta.get("tools") or [], toolsets, project_tools
            ),
            root=folder,
        )
    return loaded


def _load_knowledge(
    root: pathlib.Path, config: Any
) -> knowledge_lib.Knowledge | None:
    """依 harness.yaml 的 knowledge 區塊（或 knowledge/ 資料夾）建立知識庫。"""
    if config is None and not (root / DEFAULT_KNOWLEDGE_DIR).is_dir():
        return None
    if config is False:
        return None
    settings = config if isinstance(config, dict) else {}
    path = root / str(settings.get("path") or DEFAULT_KNOWLEDGE_DIR)
    if not path.is_dir():
        raise ValueError(f"找不到知識庫資料夾：{path}")
    unknown = sorted(set(settings) - _KNOWLEDGE_FIELDS)
    if unknown:
        raise ValueError(
            f"{CONFIG_FILE} 的 knowledge 有不認得的設定：{unknown}，"
            f"可用的有：{sorted(_KNOWLEDGE_FIELDS)}"
        )
    return knowledge_lib.Knowledge(
        path,
        embedding_model=settings.get("embeddingModel"),
        chunk_size=settings.get("chunkSize") or 800,
        cache_dir=root / KNOWLEDGE_CACHE_DIR,
        reranker=settings.get("reranker"),
        rerank_candidates=settings.get("rerankCandidates") or 20,
        min_similarity=settings.get("minSimilarity"),
        min_rerank_score=settings.get("minRerankScore"),
    )


_KNOWLEDGE_FIELDS = {
    "path",
    "embeddingModel",
    "chunkSize",
    "reranker",
    "rerankCandidates",
    "minSimilarity",
    "minRerankScore",
}


_COMPACTION_FIELDS = {
    "contextWindow": "context_window",
    "thresholdRatio": "threshold_ratio",
    "retainRatio": "retain_ratio",
    "summaryModel": "summary_model",
    "summaryMaxTokens": "summary_max_tokens",
    "pruneChars": "prune_chars",
    "pruneHead": "prune_head",
    "pruneTail": "prune_tail",
    "auto": "auto",
    "maxOverflowRetries": "max_overflow_retries",
}


def _load_compaction(config: Any) -> compaction_lib.Compaction | None:
    """依 harness.yaml 的 compaction 區塊建立設定；沒寫時使用預設值。"""
    if config is False:
        return None
    if config is None or config is True:
        return compaction_lib.Compaction()
    if not isinstance(config, dict):
        raise ValueError("compaction 必須是 true、false 或設定區塊")
    unknown = sorted(set(config) - set(_COMPACTION_FIELDS))
    if unknown:
        raise ValueError(
            f"compaction 有不認得的設定：{unknown}，"
            f"可用的有：{sorted(_COMPACTION_FIELDS)}"
        )
    return compaction_lib.Compaction(
        **{_COMPACTION_FIELDS[key]: value for key, value in config.items()}
    )


def _load_guardrails(config: dict[str, Any]) -> hooks_lib.Hooks:
    """依 harness.yaml 的 guardrails 區塊建立 hooks。"""
    return guardrails.install(
        hooks_lib.Hooks(),
        mask_pii=bool(config.get("maskPii")),
        block_injection=bool(config.get("blockInjection")),
        blocked_words=[str(word) for word in config.get("blockedWords") or []],
    )


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


def _load_permissions(config: dict[str, Any]) -> permissions_lib.Permissions:
    """把 harness.yaml 的 permissions 區塊轉成 Permissions。"""
    return permissions_lib.Permissions(
        mode=config.get("mode") or "ask",
        allow=list(config.get("allow") or []),
        deny=list(config.get("deny") or []),
        ask=list(config.get("ask") or []),
    )
