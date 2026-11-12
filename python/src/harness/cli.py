"""命令列介面。

pyharness new <資料夾>        建立新專案
pyharness chat                在目前專案開始聊天
pyharness run "<訊息>"        執行一次後結束
pyharness memory              查看、刪除或清空長期記憶
pyharness knowledge [查詢]    檢查知識庫的索引，或試著搜尋
pyharness web                 開啟網頁版；資料夾是空的時，自動建立一個
                              可以直接使用的助手
pyharness user                管理網頁版的帳號（新增、列出、重設密碼、
                              停用與啟用）
pyharness sandbox             檢查沙盒（Docker）能不能正常執行程式

指令名稱是 pyharness，避免和 TypeScript 版的 harness 指令衝突；也可以
用 `python -m harness` 執行。
"""

import argparse
import asyncio
import dataclasses
import datetime
import os
import pathlib
import sys
from collections.abc import Callable

from harness import agents
from harness import compaction as compaction_lib
from harness import credentials
from harness import datatypes
from harness import knowledge as knowledge_lib
from harness import llm
from harness import loop
from harness import permissions as permissions_lib
from harness import project as project_lib
from harness import providers
from harness import sandbox as sandbox_lib
from harness import scaffold
from harness import sessions
from harness import tools as tools_lib

WEB_PASSWORD_ENV = "HARNESS_WEB_PASSWORD"
_EXIT_COMMANDS = ("/exit", "/quit")
_COMPACT_COMMAND = "/compact"


def main(argv: list[str] | None = None) -> int:
    """CLI 進入點。

    Args:
        argv: 命令列參數，預設讀取 sys.argv。

    Returns:
        結束代碼，0 表示成功。
    """
    args = _build_parser().parse_args(argv)
    if args.command == "new":
        return _command_new(args)
    if args.command == "user":
        return _command_user(args)
    commands = {
        "memory": _command_memory,
        "knowledge": _command_knowledge,
        "web": _command_web,
        "sandbox": _command_sandbox,
    }
    try:
        if args.command == "web":
            _prepare_web_project(args.project)
        # 在網頁上填過的金鑰存在專案裡，聊天與單次執行也用得到。
        credentials.apply(args.project)
        # with 確保專案啟動的 MCP 伺服器在結束時關閉。
        with project_lib.load_project(args.project) as loaded:
            return commands.get(args.command, _command_agent)(args, loaded)
    except (FileNotFoundError, ValueError, ImportError, RuntimeError) as error:
        print(f"錯誤：{error}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    """建立命令列參數解析器。"""
    parser = argparse.ArgumentParser(prog="pyharness")
    commands = parser.add_subparsers(dest="command", required=True)

    new = commands.add_parser("new", help="建立新專案")
    new.add_argument("path", help="專案資料夾")

    chat = commands.add_parser("chat", help="開始聊天")
    run = commands.add_parser("run", help="執行一次後結束")
    run.add_argument("prompt", help="要交給 agent 的訊息")
    for command in (chat, run):
        command.add_argument("-p", "--project", default=".", help="專案資料夾")
        command.add_argument("-a", "--agent", help="agent 名稱")
        command.add_argument("-m", "--model", help="覆寫模型（litellm 格式）")
        command.add_argument(
            "-c",
            "--continue",
            dest="resume_latest",
            action="store_true",
            help="接續最新的 session",
        )
        command.add_argument(
            "-r", "--resume", metavar="SESSION_ID", help="接續指定的 session"
        )
        command.add_argument(
            "--bypass", action="store_true", help="所有工具都不詢問直接執行"
        )

    memory = commands.add_parser("memory", help="管理長期記憶")
    memory.add_argument("-p", "--project", default=".", help="專案資料夾")
    actions = memory.add_subparsers(dest="action")
    actions.add_parser("list", help="列出所有記憶（預設）")
    forget = actions.add_parser("forget", help="刪除一則記憶")
    forget.add_argument("memory_id", help="記憶的識別碼")
    actions.add_parser("clear", help="刪除所有記憶")

    web = commands.add_parser("web", help="開啟網頁版")
    web.add_argument("-p", "--project", default=".", help="專案資料夾")
    web.add_argument("--port", type=int, default=4321, help="埠號（預設 4321）")
    web.add_argument(
        "--host",
        default="127.0.0.1",
        help="要聽的位址（預設 127.0.0.1，只有本機能連）",
    )
    web.add_argument(
        "--allowed-host",
        action="append",
        metavar="主機名稱",
        help="瀏覽器網址上的主機名稱，例如反向代理的網域（可以重複）",
    )
    web.add_argument(
        "--max-upload-mb",
        type=int,
        default=50,
        help="上傳的單一檔案大小上限（MB，預設 50）",
    )

    user = commands.add_parser("user", help="管理網頁版的帳號")
    user.add_argument("-p", "--project", default=".", help="專案資料夾")
    user_actions = user.add_subparsers(dest="action", required=True)
    add = user_actions.add_parser("add", help="新增帳號，印出產生的密碼")
    add.add_argument("username", help="使用者名稱（英文字母、數字與 . _ -）")
    add.add_argument("--name", help="顯示名稱，預設和使用者名稱相同")
    add.add_argument("--admin", action="store_true", help="設為管理者")
    actions_with_project = [
        add,
        user_actions.add_parser("list", help="列出所有帳號"),
    ]
    for action, text in (
        ("passwd", "重設密碼，印出新產生的密碼"),
        ("disable", "停用帳號（立刻登出）"),
        ("enable", "重新啟用帳號"),
    ):
        command = user_actions.add_parser(action, help=text)
        command.add_argument("username", help="使用者名稱")
        actions_with_project.append(command)
    # -p 寫在動作前後都可以（pyharness user -p x list 或 user list -p x）。
    for command in actions_with_project:
        command.add_argument(
            "-p", "--project", default=argparse.SUPPRESS, help="專案資料夾"
        )

    sandbox = commands.add_parser(
        "sandbox", help="檢查沙盒（Docker）能不能正常執行程式"
    )
    sandbox.add_argument("-p", "--project", default=".", help="專案資料夾")

    knowledge = commands.add_parser("knowledge", help="檢查知識庫")
    knowledge.add_argument("-p", "--project", default=".", help="專案資料夾")
    knowledge.add_argument("query", nargs="?", help="要試著搜尋的內容")
    knowledge.add_argument(
        "-l", "--labels", help="只搜尋這些標籤（逗號分隔），模擬受限的 agent"
    )
    return parser


def _command_new(args: argparse.Namespace) -> int:
    """建立新專案。"""
    created = scaffold.create_project(args.path)
    if not created:
        print("檔案都已存在，沒有建立任何東西。")
        return 0
    for path in created:
        print(f"建立 {path}")
    print(f"\n完成。接著執行：cd {args.path} && pyharness chat")
    return 0


def _command_memory(
    args: argparse.Namespace, loaded: project_lib.Project
) -> int:
    """查看、刪除或清空長期記憶。"""
    if loaded.memory is None:
        print(
            "這個專案沒有開啟長期記憶。要使用的話，在 harness.yaml 加上 "
            "memory: true"
        )
        return 1
    store = loaded.memory
    if args.action == "forget":
        try:
            item = store.remove(args.memory_id)
        except KeyError:
            print(f"找不到識別碼為 {args.memory_id} 的記憶", file=sys.stderr)
            return 1
        print(f"已刪除：{item.content}")
        return 0
    if args.action == "clear":
        print(f"已刪除 {store.clear()} 則記憶。")
        return 0
    items = store.items()
    if not items:
        print("目前沒有任何記憶。")
    for item in items:
        print(f"[{item.id}] {item.content}（{item.created_at}）")
    return 0


def _prepare_web_project(path: str) -> None:
    """資料夾是空的時，建立一個可以直接使用的助手專案。

    已經有檔案、卻沒有 harness.yaml 的資料夾不會動它：多半是指錯了位置，
    交給 load_project 回報錯誤。
    """
    root = pathlib.Path(path)
    if (root / project_lib.CONFIG_FILE).exists() or not scaffold.is_empty(root):
        return
    scaffold.create_assistant(root, sandbox=sandbox_lib.available())
    print(f"{root.resolve()} 是空的，已經建立一個可以直接使用的助手。")


def _web_password(
    args: argparse.Namespace, loaded: project_lib.Project
) -> tuple[str | None, str]:
    """決定網頁版的密碼，回傳 (密碼, 給使用者看的說明)。

    只在本機使用、也沒有任何帳號時不需要登入。否則第一次啟動時用這組
    密碼建立管理者 admin：有設定環境變數時用它，沒有時自動產生一組並
    存在專案裡，之後每次啟動都會再顯示（admin 自己改過密碼後就不顯示）。
    """
    from harness.web import accounts
    from harness.web import server

    store = accounts.AccountStore.for_project(loaded.root)
    password = os.environ.get(WEB_PASSWORD_ENV) or None
    mode = server.auth_mode(
        store,
        host=args.host,
        password=password,
        allowed_hosts=args.allowed_host or [],
    )
    if mode == "local":
        return None, ""
    if password:
        return password, f"密碼是環境變數 {WEB_PASSWORD_ENV} 的值。"
    has_admin = any(
        user.is_admin and not user.disabled for user in store.list_users()
    )
    if has_admin:
        password = credentials.stored_web_password(loaded.root)
    else:
        password, _ = credentials.web_password(loaded.root)
    stored = credentials.SECRETS_DIR / credentials.PASSWORD_FILE
    return password, (
        f"密碼：{password}\n"
        f"（自動產生，存在 {stored}；要用自己的密碼請設定環境變數 "
        f"{WEB_PASSWORD_ENV}，或登入後在網頁上修改）"
    )


def _login_note(root: pathlib.Path, password: str | None, note: str) -> str:
    """啟動後給管理者看的登入說明。"""
    from harness.web import accounts
    from harness.web import server

    store = accounts.AccountStore.for_project(root)
    admin = server.ADMIN_USERNAME
    if password and store.authenticate(admin, password) is not None:
        return f"需要登入：管理者帳號 {admin}，{note}"
    return (
        "需要登入：請用自己的帳號登入。忘記密碼時，管理者可以用 "
        "pyharness user passwd <名稱> 重設。"
    )


def _command_web(args: argparse.Namespace, loaded: project_lib.Project) -> int:
    """啟動網頁版，直到使用者按 Ctrl-C。"""
    # aiohttp 只有網頁版用得到，延後到這裡才載入。
    from harness import web
    from harness.web import accounts
    from harness.web import server

    password, password_note = _web_password(args, loaded)
    store = accounts.AccountStore.for_project(loaded.root)
    mode = server.auth_mode(
        store,
        host=args.host,
        password=password,
        allowed_hosts=args.allowed_host or [],
    )

    def ready(url: str) -> None:
        print(f"{loaded.name} 的網頁版已啟動：{url}")
        if mode == "accounts":
            print(_login_note(loaded.root, password, password_note))
        agent = loaded.agents[loaded.default_agent]
        if not providers.is_ready(agent.model or llm.default_model()):
            print("還沒有設定模型的金鑰：打開網頁後依畫面選供應商、貼上金鑰。")
        print("按 Ctrl-C 結束。", flush=True)

    try:
        asyncio.run(
            web.serve(
                loaded,
                host=args.host,
                port=args.port,
                on_ready=ready,
                password=password,
                allowed_hosts=args.allowed_host or [],
                max_upload_bytes=args.max_upload_mb * 1024 * 1024,
            )
        )
    except KeyboardInterrupt:
        print()
    except OSError as error:
        raise RuntimeError(
            f"無法在 {args.host}:{args.port} 啟動：{error}"
        ) from error
    return 0


def _command_user(args: argparse.Namespace) -> int:
    """管理網頁版的帳號（存在專案的 .harness/harness.db）。"""
    from harness.web import accounts

    root = pathlib.Path(args.project)
    if not (root / project_lib.CONFIG_FILE).exists():
        print(
            f"錯誤：{root.resolve()} 裡沒有 {project_lib.CONFIG_FILE}，"
            "請用 -p 指定專案資料夾",
            file=sys.stderr,
        )
        return 1
    store = accounts.AccountStore.for_project(root)
    try:
        if args.action == "list":
            return _list_users(store)
        if args.action == "add":
            had_users = store.has_users()
            role = "admin" if args.admin else "member"
            user, password = store.create_user(args.username, args.name, role)
            label = "管理者" if user.is_admin else "成員"
            print(f"已建立{label} {user.username}（{user.display_name}）")
            print(f"密碼：{password}")
            print("密碼只會顯示這一次，請交給本人；登入後可以自己修改。")
            if not had_users:
                print(
                    "這是第一個帳號：網頁版之後要登入才能使用（重新啟動後"
                    "生效）。"
                )
            if not had_users and not user.is_admin:
                print(
                    "還沒有管理者：下次啟動網頁版時會建立管理者 admin，"
                    "密碼和目前的網頁密碼相同。"
                )
            return 0
        user = store.find_user(args.username)
        if user is None:
            print(f"錯誤：沒有 {args.username} 這個帳號", file=sys.stderr)
            return 1
        if args.action == "passwd":
            password = accounts.generate_password()
            store.set_password(user.id, password)
            print(f"{user.username} 的新密碼：{password}")
            print("原本的登入都已經失效。密碼只會顯示這一次。")
            return 0
        disabled = args.action == "disable"
        store.update_user(user.id, disabled=disabled)
        print(f"已{'停用' if disabled else '啟用'} {user.username}")
        return 0
    except accounts.AccountError as error:
        print(f"錯誤：{error}", file=sys.stderr)
        return 1


def _list_users(store) -> int:
    """印出所有帳號。"""
    users = store.list_users()
    if not users:
        print("還沒有任何帳號；網頁版只在本機使用時不需要登入。")
        return 0
    for user in users:
        role = "管理者" if user.is_admin else "成員"
        state = "（停用）" if user.disabled else ""
        seen = "從未登入"
        if user.last_seen_at is not None:
            seen = "最後使用 " + datetime.datetime.fromtimestamp(
                user.last_seen_at
            ).strftime("%Y-%m-%d %H:%M")
        print(f"{user.username}\t{user.display_name}\t{role}{state}\t{seen}")
    return 0


def _command_sandbox(
    args: argparse.Namespace, loaded: project_lib.Project
) -> int:
    """在沙盒裡跑一段檢查程式，確認 Docker、映像檔與限制都正常。"""
    box = loaded.sandbox
    if box is None:
        print(
            "這個專案沒有設定沙盒。要使用的話，在 harness.yaml 加上 "
            "sandbox 區塊。"
        )
        return 1
    network = "允許" if box.network else "關閉"
    print(f"映像檔：{box.image}")
    print(f"掛載：{box.workspace} → {sandbox_lib.CONTAINER_WORKDIR}")
    print(
        f"限制：{box.timeout_seconds:g} 秒、記憶體 {box.memory}、"
        f"CPU {box.cpus:g}、網路{network}"
    )
    if hasattr(os, "getuid") and os.getuid() == 0:
        print(
            "  ⚠ 目前以 root 執行，容器裡的程式也會是 root。建議用一般使用者"
            "執行 pyharness。"
        )
    print("\n執行檢查程式…")
    result = asyncio.run(box.run(_SANDBOX_CHECK))
    if result.timed_out:
        print(f"✗ 超過 {box.timeout_seconds:g} 秒沒有結束")
        return 1
    if result.exit_code != 0:
        print(f"✗ 執行失敗（結束代碼 {result.exit_code}）")
        print(sandbox_lib.format_output(result))
        return 1
    print(result.stdout.rstrip())
    print("\n✓ 沙盒可以正常使用")
    return 0


# 在沙盒裡印出 Python 版本、可用的常見套件，並確認網路與寫入的限制。
_SANDBOX_CHECK = """
import importlib.util, os, socket, sys
print("Python", sys.version.split()[0])
names = ["pandas", "numpy", "openpyxl", "pdfplumber", "docx", "matplotlib"]
found = [n for n in names if importlib.util.find_spec(n)]
missing = [n for n in names if n not in found]
print("已安裝：", "、".join(found) or "（無）")
if missing:
    print("未安裝：", "、".join(missing))
try:
    socket.create_connection(("pypi.org", 443), timeout=3).close()
    print("網路：可以連線")
except OSError:
    print("網路：無法連線")
print("工作目錄可寫入：", "是" if os.access(".", os.W_OK) else "否")
"""


def _command_knowledge(
    args: argparse.Namespace, loaded: project_lib.Project
) -> int:
    """顯示知識庫的索引概況，有查詢時再列出搜尋結果。"""
    if loaded.knowledge is None:
        print("這個專案沒有知識庫。建立 knowledge/ 資料夾並放入文件即可。")
        return 1
    labels = None
    if args.labels is not None:
        labels = [item.strip() for item in args.labels.split(",") if item]
    return asyncio.run(_show_knowledge(loaded.knowledge, args.query, labels))


async def _show_knowledge(
    kb: knowledge_lib.Knowledge, query: str | None, labels: list[str] | None
) -> int:
    """印出知識庫的概況與搜尋結果。"""
    stats = await kb.stats()
    mode = "關鍵字 + 語意（embedding）" if stats["embedding"] else "關鍵字"
    if stats["reranker"]:
        mode += "，再用 reranker 重新排序"
    print(
        f"{stats['sources']} 個檔案、{stats['chunks']} 個片段，檢索方式：{mode}"
    )
    thresholds = []
    if stats["min_similarity"] is not None:
        thresholds.append(f"相似度 ≥ {stats['min_similarity']}")
    if stats["min_rerank_score"] is not None:
        thresholds.append(f"rerank ≥ {stats['min_rerank_score']}")
    if thresholds:
        print(f"門檻：{'、'.join(thresholds)}")
    for warning in stats["warnings"]:
        print(f"  ⚠ {warning}")
    if not query:
        return 0
    hits = await kb.search(query, labels=labels)
    print()
    print(knowledge_lib.format_hits(hits, show_scores=True))
    return 0


def _command_agent(
    args: argparse.Namespace, loaded: project_lib.Project
) -> int:
    """chat 與 run 共用的流程。"""
    agent = _pick_agent(loaded, args.agent, args.model)
    session = _open_session(loaded, args.resume, args.resume_latest)
    permissions = dataclasses.replace(loaded.permissions)
    if args.bypass:
        permissions.mode = "bypass"
    if sys.stdin.isatty():
        permissions.approver = _ask_user

    # 整個聊天過程共用同一個事件迴圈，模型的連線才能重複使用。
    with asyncio.Runner() as runner:

        def turn(user_input: str) -> bool:
            """執行一輪對話，回傳是否正常跑完（沒有被 Ctrl-C 中斷）。"""
            if user_input == _COMPACT_COMMAND:
                job = _compact_now(agent, session, loaded)
            else:
                job = _run_turn(agent, user_input, session, permissions, loaded)
            try:
                runner.run(job)
            except KeyboardInterrupt:
                print("\n  ⚠ 已中斷這一輪。")
                return False
            # 模型或網路出錯時顯示原因並讓使用者可以繼續對話，而不是
            # 讓整個聊天結束；錯誤種類取決於所用的模型供應商。
            except Exception as error:  # noqa: BLE001
                print(f"\n  ✗ {type(error).__name__}: {error}")
                return False
            return True

        if args.command == "run":
            return 0 if turn(args.prompt) else 1
        print(f"{loaded.name} / {agent.name}（session {session.id}）")
        print(
            "輸入訊息開始對話，/compact 壓縮對話，/exit 離開，"
            "Ctrl-C 中斷目前這一輪。"
        )
        _chat_loop(turn)
    return 0


def _chat_loop(turn: Callable[[str], bool]) -> None:
    """反覆讀取使用者輸入並執行，直到使用者離開。"""
    while True:
        try:
            user_input = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if user_input in _EXIT_COMMANDS:
            return
        if user_input:
            turn(user_input)


def _pick_agent(
    loaded: project_lib.Project, name: str | None, model: str | None
) -> agents.Agent:
    """依命令列參數選出要執行的 agent。"""
    name = name or loaded.default_agent
    if name not in loaded.agents:
        raise ValueError(
            f"沒有 {name} 這個 agent，可用的有：{list(loaded.agents)}"
        )
    if model:
        # 對話可能轉交給別的 agent，所以整個專案的 agent 都改用這個模型。
        for item in loaded.agents.values():
            item.model = model
    return loaded.agents[name]


def _open_session(
    loaded: project_lib.Project, session_id: str | None, use_latest: bool
) -> sessions.Session:
    """建立新的 session，或依 --resume / --continue 讀取既有的。"""
    if session_id:
        return sessions.load(loaded.sessions_dir, session_id)
    if not use_latest:
        return sessions.create(loaded.sessions_dir)
    found = sessions.latest(loaded.sessions_dir)
    if found is None:
        raise ValueError("這個專案還沒有任何 session 可以接續")
    return found


async def _run_turn(
    agent: agents.Agent,
    user_input: str,
    session: sessions.Session,
    permissions: permissions_lib.Permissions,
    loaded: project_lib.Project,
) -> None:
    """執行一輪對話，並把過程印到終端機。"""
    at_line_start = True
    async for event in loop.run_stream(
        agent,
        user_input,
        session=session,
        permissions=permissions,
        workdir=loaded.root,
        hooks=loaded.hooks,
        memory=loaded.memory,
        compaction=loaded.compaction,
    ):
        if event.type == "text_delta":
            print(event.data["text"], end="", flush=True)
            at_line_start = event.data["text"].endswith("\n")
            continue
        if not at_line_start:
            print()
            at_line_start = True
        if event.type == "tool_call":
            call = event.data["call"]
            print(f"  ⚙ {call.name}({loop.format_arguments(call)})")
        elif event.type == "tool_result" and event.data["result"].is_error:
            print(f"  ✗ {event.data['result'].output}")
        elif event.type == "handoff":
            print(f"  → 對話轉交給 {event.data['to']}")
        elif event.type == "compaction":
            _print_compaction(event.data["result"])
        elif event.type == "compaction_failed":
            print(f"  ⚠ 自動壓縮失敗：{event.data['error']}")
        elif event.type == "subagent":
            _print_subagent_progress(event)
        elif event.type == "run_end":
            _print_stop_notice(event.data["result"])


def _print_subagent_progress(event: datatypes.Event) -> None:
    """印出 subagent 呼叫的工具與錯誤，縮排表示它在哪一層。

    subagent 的文字回覆不逐字印出：最後的回覆會當成工具結果交回上一層，
    由上一層的 agent 整理後回答。
    """
    inner = event.data["event"]
    indent = "  " * (len(event.data["path"]) + 1)
    agent = event.data["agent"]
    if inner.type == "tool_call":
        call = inner.data["call"]
        arguments = loop.format_arguments(call)
        print(f"{indent}↳ {agent} ⚙ {call.name}({arguments})")
    elif inner.type == "tool_result" and inner.data["result"].is_error:
        print(f"{indent}↳ {agent} ✗ {inner.data['result'].output}")


async def _compact_now(
    agent: agents.Agent, session: sessions.Session, loaded: project_lib.Project
) -> None:
    """處理聊天中的 /compact。"""
    try:
        result = await loop.compact(
            agent,
            session,
            settings=loaded.compaction,
            memory=loaded.memory,
        )
    except compaction_lib.CompactionError as error:
        print(f"  ✗ 壓縮失敗：{error}")
        return
    if result is None:
        print("  對話還很短，沒有可以壓縮的內容。")
        return
    _print_compaction(result)


def _print_compaction(result: compaction_lib.CompactionResult) -> None:
    """顯示壓縮了多少內容。"""
    parts = []
    if result.summarized:
        parts.append(f"摘要了 {result.summarized} 則較早的訊息")
    if result.pruned:
        parts.append(f"修剪了 {result.pruned} 個超大的工具結果")
    detail = "、".join(parts) or "沒有變動"
    print(
        f"  ⟳ 對話已壓縮：{detail}"
        f"（約 {result.tokens_before:,} → {result.tokens_after:,} tokens）"
    )


def _print_stop_notice(result: datatypes.RunResult) -> None:
    """不是正常結束時，告訴使用者原因。"""
    # 被防護擋下時，原因已經當成回覆印出來了。
    if result.stop_reason in ("end_turn", "blocked"):
        return
    notices = {
        "max_turns": "已達到單次對話的工具呼叫輪數上限，可以再傳訊息讓它繼續。",
        "max_tokens": "回覆達到長度上限而被截斷。",
    }
    notice = notices.get(result.stop_reason, f"模型停止：{result.stop_reason}")
    print(f"  ⚠ {notice}")


async def _ask_user(call: datatypes.ToolCall, target: tools_lib.Tool) -> bool:
    """在終端機詢問使用者是否同意執行工具。"""
    question = (
        f"  ? 允許執行 {target.name}({loop.format_arguments(call)}) 嗎？[y/N] "
    )
    # 確認時沒有其他工具在執行，直接在主執行緒讀取輸入即可。
    answer = input(question)
    return answer.strip().lower() in ("y", "yes")


if __name__ == "__main__":
    sys.exit(main())
