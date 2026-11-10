"""命令列介面。

pyharness new <資料夾>        建立新專案
pyharness chat                在目前專案開始聊天
pyharness run "<訊息>"        執行一次後結束
pyharness memory              查看、刪除或清空長期記憶
pyharness knowledge [查詢]    檢查知識庫的索引，或試著搜尋
pyharness web                 開啟網頁版

指令名稱是 pyharness，避免和 TypeScript 版的 harness 指令衝突；也可以
用 `python -m harness` 執行。
"""

import argparse
import asyncio
import dataclasses
import sys
from collections.abc import Callable

from harness import agents
from harness import compaction as compaction_lib
from harness import datatypes
from harness import knowledge as knowledge_lib
from harness import loop
from harness import permissions as permissions_lib
from harness import project as project_lib
from harness import scaffold
from harness import sessions
from harness import tools as tools_lib

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
    commands = {
        "memory": _command_memory,
        "knowledge": _command_knowledge,
        "web": _command_web,
    }
    try:
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


def _command_web(args: argparse.Namespace, loaded: project_lib.Project) -> int:
    """啟動網頁版，直到使用者按 Ctrl-C。"""
    # aiohttp 只有網頁版用得到，延後到這裡才載入。
    from harness import web

    def ready(url: str) -> None:
        print(f"{loaded.name} 的網頁版已啟動：{url}")
        print("按 Ctrl-C 結束。")

    try:
        asyncio.run(
            web.serve(loaded, host=args.host, port=args.port, on_ready=ready)
        )
    except KeyboardInterrupt:
        print()
    except OSError as error:
        raise RuntimeError(
            f"無法在 {args.host}:{args.port} 啟動：{error}"
        ) from error
    return 0


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
    print(
        f"{stats['sources']} 個檔案、{stats['chunks']} 個片段，檢索方式：{mode}"
    )
    for warning in stats["warnings"]:
        print(f"  ⚠ {warning}")
    if not query:
        return 0
    hits = await kb.search(query, labels=labels)
    print()
    print(knowledge_lib.format_hits(hits))
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
        elif event.type == "run_end":
            _print_stop_notice(event.data["result"])


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
