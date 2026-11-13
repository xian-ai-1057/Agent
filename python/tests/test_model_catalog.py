"""向供應商與端點查詢模型清單（harness.model_catalog）。

用本機的假伺服器代替各家的 API，記下收到的網址與標頭。
"""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from harness import model_catalog
from harness import providers

RESPONSES = {
    "/v1/models": {
        "data": [
            {"id": "qwen3-32b", "object": "model"},
            {"id": "gpt-oss-120b"},
            {"id": "qwen3-32b"},
        ]
    },
    "/anthropic": {
        "data": [
            {"id": "claude-opus-5-5", "display_name": "Claude Opus 5.5"},
            {"id": "claude-haiku-5-5", "display_name": ""},
        ]
    },
    "/openai": {
        "data": [
            {"id": "gpt-5"},
            {"id": "o4-mini"},
            {"id": "text-embedding-3-small"},
            {"id": "gpt-4o-realtime-preview"},
            {"id": "dall-e-3"},
        ]
    },
    "/gemini": {
        "models": [
            {
                "name": "models/gemini-2.5-pro",
                "displayName": "Gemini 2.5 Pro",
                "supportedGenerationMethods": ["generateContent"],
            },
            {
                "name": "models/text-embedding-004",
                "supportedGenerationMethods": ["embedContent"],
            },
        ]
    },
    "/openrouter": {"data": [{"id": "meta/llama-4", "name": "Llama 4"}]},
    "/api/tags": {"models": [{"name": "hermes3:latest"}, {"name": "qwen3"}]},
}


@pytest.fixture
async def fake(monkeypatch):
    """假的 API；回傳 (base_url, 收到的請求)。"""
    seen = []

    async def handle(request: web.Request) -> web.Response:
        seen.append((request.path, dict(request.headers)))
        if request.path == "/broken/models":
            return web.Response(text="<html>not json</html>")
        if request.path == "/denied/models":
            return web.Response(status=401)
        body = RESPONSES.get(request.path)
        if body is None:
            return web.Response(status=404)
        return web.json_response(body)

    app = web.Application()
    app.router.add_get("/{tail:.*}", handle)
    server = TestServer(app)
    await server.start_server()
    base = str(server.make_url("")).rstrip("/")
    monkeypatch.setattr(
        model_catalog,
        "_OFFICIAL",
        {
            name: f"{base}/{name}"
            for name in ("anthropic", "openai", "gemini", "openrouter")
        },
    )
    for name in ("HARNESS_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    yield base, seen
    await server.close()


def names(found):
    return [item.name for item in found]


async def test_endpoint_lists_models_and_tries_v1(fake):
    base, seen = fake

    found = await model_catalog.list_models(providers.ENDPOINT, base, "sk-x")

    # 先試 /models（404），再試 /v1/models；重複的名稱只留一個。
    assert [path for path, _ in seen] == ["/models", "/v1/models"]
    assert seen[-1][1]["Authorization"] == "Bearer sk-x"
    assert names(found) == ["gpt-oss-120b", "qwen3-32b"]


async def test_endpoint_uses_the_saved_key_only_when_allowed(fake, monkeypatch):
    base, seen = fake
    monkeypatch.setenv("HARNESS_API_KEY", "saved")

    await model_catalog.list_models(providers.ENDPOINT, f"{base}/v1")
    await model_catalog.list_models(
        providers.ENDPOINT, f"{base}/v1", saved_key=False
    )

    assert seen[0][1]["Authorization"] == "Bearer saved"
    assert "Authorization" not in seen[1][1]


@pytest.mark.parametrize(
    ("provider", "expected", "header"),
    [
        (
            "anthropic",
            ["anthropic/claude-haiku-5-5", "anthropic/claude-opus-5-5"],
            ("x-api-key", "k"),
        ),
        (
            "openai",
            ["openai/gpt-5", "openai/o4-mini"],
            ("Authorization", "Bearer k"),
        ),
        ("gemini", ["gemini/gemini-2.5-pro"], ("x-goog-api-key", "k")),
        (
            "openrouter",
            ["openrouter/meta/llama-4"],
            ("Authorization", "Bearer k"),
        ),
    ],
)
async def test_providers_list_their_chat_models(
    fake, provider, expected, header
):
    _, seen = fake

    found = await model_catalog.list_models(provider, api_key="k")

    assert names(found) == expected
    assert seen[0][1][header[0]] == header[1]


async def test_labels_come_from_the_provider(fake):
    found = await model_catalog.list_models("anthropic", api_key="k")

    assert [item.label for item in found] == [
        "claude-haiku-5-5",
        "Claude Opus 5.5",
    ]


async def test_ollama_lists_local_models(fake):
    base, _ = fake

    found = await model_catalog.list_models("ollama", base)

    assert names(found) == ["ollama_chat/hermes3:latest", "ollama_chat/qwen3"]


@pytest.mark.parametrize(
    ("provider", "url", "key", "message"),
    [
        ("anthropic", "", "", "請先填上金鑰"),
        (providers.ENDPOINT, "", "", "請先填上端點網址"),
        (providers.ENDPOINT, "/denied", "k", "金鑰不正確"),
        (providers.ENDPOINT, "/broken", "k", "不是模型清單"),
        (providers.ENDPOINT, "/missing/v1", "k", "不支援列出模型"),
        ("nope", "", "", "不認得"),
    ],
)
async def test_problems_are_explained(
    fake, monkeypatch, provider, url, key, message
):
    base, _ = fake
    monkeypatch.delenv("HARNESS_BASE_URL", raising=False)

    with pytest.raises(model_catalog.CatalogError, match=message):
        await model_catalog.list_models(
            provider, f"{base}{url}" if url else "", key
        )


async def test_unreachable_endpoints_are_explained():
    with pytest.raises(model_catalog.CatalogError, match="連不到"):
        await model_catalog.list_models(
            providers.ENDPOINT, "http://127.0.0.1:9", "k"
        )
