"""常見的模型供應商：金鑰的環境變數與預設模型。

用在兩個地方：
    沒有指定模型時，依已設定的金鑰挑一個預設模型（見 llm.default_model）。
    網頁版的連線設定，讓使用者選供應商、貼上金鑰就能用（見
    web.workspace）。

這裡只列常用的幾家；其他 litellm 支援的供應商仍然可以直接在
harness.yaml 寫模型名稱、用環境變數提供金鑰。
"""

import dataclasses
import os

# 連 OpenAI 相容端點（HARNESS_BASE_URL）時，在連線設定裡的代號。
ENDPOINT = "endpoint"
BASE_URL_VAR = "HARNESS_BASE_URL"
API_KEY_VAR = "HARNESS_API_KEY"


@dataclasses.dataclass(frozen=True)
class Provider:
    """一家可以直接連線的模型供應商。

    Attributes:
        id: 代號，也是 litellm 模型名稱的開頭（anthropic/...）。
        label: 顯示給使用者看的名稱。
        default_model: 選了這家、沒有另外指定時使用的模型。
        key_var: 金鑰的環境變數名稱；不需要金鑰時為 None。
        base_url_var: 伺服器位址的環境變數名稱；固定連官方服務時為 None。
        default_base_url: base_url_var 沒有設定時 litellm 使用的位址。
        prefixes: 屬於這家的模型名稱開頭，沒有列出時只有 id。
    """

    id: str
    label: str
    default_model: str
    key_var: str | None = None
    base_url_var: str | None = None
    default_base_url: str | None = None
    prefixes: tuple[str, ...] = ()

    def matches(self, model: str) -> bool:
        """模型名稱是不是屬於這家供應商。"""
        names = self.prefixes or (self.id,)
        return any(model.startswith(f"{name}/") for name in names)

    def key_set(self) -> bool:
        """需要的金鑰是否已經設定；不需要金鑰時一律為 True。"""
        return self.key_var is None or bool(os.environ.get(self.key_var))


# 順序就是挑預設模型時的優先順序。
PROVIDERS = (
    Provider(
        id="anthropic",
        label="Anthropic（Claude）",
        default_model="anthropic/claude-opus-5-5",
        key_var="ANTHROPIC_API_KEY",
    ),
    Provider(
        id="openai",
        label="OpenAI（GPT）",
        default_model="openai/gpt-5",
        key_var="OPENAI_API_KEY",
    ),
    Provider(
        id="gemini",
        label="Google（Gemini）",
        default_model="gemini/gemini-2.5-pro",
        key_var="GEMINI_API_KEY",
    ),
    Provider(
        id="openrouter",
        label="OpenRouter",
        default_model="openrouter/nousresearch/hermes-4-405b",
        key_var="OPENROUTER_API_KEY",
    ),
    Provider(
        id="ollama",
        label="Ollama（本機模型）",
        default_model="ollama_chat/hermes3",
        base_url_var="OLLAMA_API_BASE",
        default_base_url="http://localhost:11434",
        prefixes=("ollama_chat", "ollama"),
    ),
)


def find(provider_id: str) -> Provider | None:
    """依代號找供應商。

    Args:
        provider_id: 供應商代號，例如 anthropic。

    Returns:
        對應的供應商，沒有這個代號時為 None。
    """
    return next((item for item in PROVIDERS if item.id == provider_id), None)


def of_model(model: str) -> Provider | None:
    """依模型名稱的開頭判斷它屬於哪家供應商。

    Args:
        model: litellm 的模型名稱，例如 anthropic/claude-opus-5-5。

    Returns:
        對應的供應商；不在清單裡時為 None。
    """
    return next((item for item in PROVIDERS if item.matches(model)), None)


def detect() -> Provider | None:
    """找出第一家已經設定金鑰的供應商。

    不需要金鑰的供應商（Ollama）無法從環境變數判斷有沒有在用，所以不算。

    Returns:
        已設定金鑰的供應商，都沒有設定時為 None。
    """
    return next(
        (item for item in PROVIDERS if item.key_var and item.key_set()), None
    )


def uses_endpoint() -> bool:
    """是否設定了 OpenAI 相容端點（HARNESS_BASE_URL）。"""
    return bool(os.environ.get(BASE_URL_VAR))


def is_ready(model: str) -> bool:
    """用這個模型需要的連線設定是否齊全。

    只檢查「有沒有設定」，不會真的連線。清單以外的供應商無從判斷，
    一律視為已設定。

    Args:
        model: 實際要使用的模型名稱。

    Returns:
        可以直接開始對話時為 True。
    """
    if uses_endpoint():
        return True
    provider = of_model(model)
    return provider is None or provider.key_set()
