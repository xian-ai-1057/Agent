"""框架共用的資料型別。

對話訊息一律使用 OpenAI chat 格式的 dict（litellm 會把各家模型都轉成
這個格式），這裡只定義框架自己需要的包裝型別。
"""

import dataclasses
import pathlib
from typing import Any

Message = dict[str, Any]


@dataclasses.dataclass
class ToolCall:
    """模型提出的一次工具呼叫。

    Attributes:
        id: 模型給這次呼叫的識別碼，回傳結果時要對應。
        name: 工具名稱。
        arguments: 已解析的參數。
        parse_error: 參數不是合法 JSON 時的錯誤訊息，否則為 None。
    """

    id: str
    name: str
    arguments: dict[str, Any]
    parse_error: str | None = None


@dataclasses.dataclass
class ToolResult:
    """一次工具呼叫的結果。

    Attributes:
        call_id: 對應的 ToolCall.id。
        name: 工具名稱。
        output: 要送回模型的文字。
        is_error: 是否為失敗（被拒絕、找不到工具、執行出錯）。
    """

    call_id: str
    name: str
    output: str
    is_error: bool = False


@dataclasses.dataclass
class Usage:
    """Token 用量。

    Attributes:
        input_tokens: 輸入 token 數。
        output_tokens: 輸出 token 數。
    """

    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: "Usage") -> None:
        """把另一筆用量累加進來。

        Args:
            other: 要累加的用量。
        """
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


@dataclasses.dataclass
class ModelResponse:
    """模型一次回應的完整內容。

    Attributes:
        message: assistant 訊息（OpenAI 格式），原樣存進對話紀錄。
        tool_calls: 這次回應要求的工具呼叫。
        usage: 這次回應的 token 用量。
        finish_reason: 模型停止的原因（stop、length…）。
    """

    message: Message
    tool_calls: list[ToolCall]
    usage: Usage
    finish_reason: str = "stop"


@dataclasses.dataclass
class Event:
    """主迴圈串流出來的事件。

    type 與 data 的對應：
        text_delta:  {"text": str}
        message:     {"message": Message}
        tool_call:   {"call": ToolCall}
        tool_result: {"result": ToolResult}
        run_end:     {"result": RunResult}

    Attributes:
        type: 事件種類。
        data: 事件內容。
    """

    type: str
    data: dict[str, Any]


@dataclasses.dataclass
class RunResult:
    """一次 run 的結果。

    Attributes:
        output: agent 最後的文字回覆。
        messages: 這次 run 結束後的完整對話紀錄。
        usage: 這次 run 累計的 token 用量。
        turns: 呼叫模型的次數。
        stop_reason: end_turn、max_turns、max_tokens 或模型回報的其他原因。
    """

    output: str
    messages: list[Message]
    usage: Usage
    turns: int
    stop_reason: str


@dataclasses.dataclass
class RunContext:
    """執行期間傳給工具與 hooks 的環境資訊。

    工具函式只要有一個參數標註為 RunContext，框架就會自動填入，而且
    不會出現在給模型看的參數裡。

    Attributes:
        agent_name: 目前執行的 agent 名稱。
        workdir: 專案根目錄，內建檔案工具只能存取這個目錄底下。
        session_id: 目前的 session 識別碼。
        deps: 呼叫 run 時傳進來的任意物件（資料庫連線、使用者資訊…）。
    """

    agent_name: str
    workdir: pathlib.Path
    session_id: str
    deps: Any = None
