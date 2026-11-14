"""模型中台（LiteLLM Proxy）：使用者身分、模型清單與看得懂的錯誤。"""

import litellm
import pytest

from harness import model_catalog
from harness import project as project_lib
from harness.web import accounts as accounts_lib
from harness.web import workspace as workspace_lib
from tests import conftest
from tests import test_web_accounts
from tests.test_web_accounts import password_of

site = test_web_accounts.site
fast_hash = test_web_accounts.fast_hash

MODELS = "model: base-model\nmodels: [big-model, small-model]\n"


@pytest.fixture
def gateway(monkeypatch):
    """假的模型中台：依 x-litellm-end-user-id 回傳這個人能用的模型。"""

    class Gateway:
        allowed: dict[str, list[str]] = {}
        calls: list[dict] = []
        failing = False

    async def list_models(provider_id, *args, extra_headers=None, **kwargs):
        user = (extra_headers or {}).get("x-litellm-end-user-id", "")
        Gateway.calls.append({"provider": provider_id, "user": user})
        if Gateway.failing:
            raise model_catalog.CatalogError("連不到中台")
        names = Gateway.allowed.get(user, [])
        return [model_catalog.ModelChoice(name, name) for name in names]

    monkeypatch.setenv("HARNESS_BASE_URL", "http://gateway.test/v1")
    monkeypatch.setattr(model_catalog, "list_models", list_models)
    Gateway.allowed = {}
    Gateway.calls = []
    return Gateway


def names(project) -> list[str]:
    return [item["name"] for item in project["models"]]


class TestIdentity:
    async def test_model_calls_carry_the_user(self, site):
        s = await site([conftest.text_response("好")])
        alice = await s.login("alice", password_of("alice"))

        _, created = await alice.api("POST", "/sessions", {"text": "嗨"})
        await alice.wait_idle(created["id"])

        caller = s.model.requests[0]["caller"]
        assert caller.user == "alice"
        assert caller.session == created["id"]
        assert dict(caller.metadata)["agent"] == "main"
        headers = caller.headers()
        assert headers["x-litellm-end-user-id"] == "alice"
        assert headers["x-litellm-trace-id"] == created["id"]

    def test_local_mode_sends_no_identity(self, tmp_path):
        project = project_lib.load_project(
            test_web_accounts.make_project(tmp_path / "p")
        )
        try:
            workspace = workspace_lib.Workspace(project, auth_mode="local")
            user = accounts_lib.LOCAL_USER
            assert workspace._caller(user, "s1", "main") is None  # noqa: SLF001
        finally:
            project.close()


class TestModelsFromGateway:
    async def test_menu_lists_only_models_the_gateway_allows(
        self, site, gateway
    ):
        gateway.allowed = {
            "alice": ["small-model", "not-configured"],
            "boss": ["base-model", "big-model", "small-model"],
        }
        s = await site([conftest.text_response("好")], extra=MODELS)
        boss = await s.login("boss", password_of("boss"))
        alice = await s.login("alice", password_of("alice"))

        _, mine = await alice.api("GET", "/project")
        _, admin = await boss.api("GET", "/project")
        refused, _ = await alice.api(
            "POST",
            "/sessions",
            {"text": "嗨", "options": {"model": "big-model"}},
        )
        _, created = await alice.api("POST", "/sessions", {"text": "嗨"})
        await alice.wait_idle(created["id"])

        assert names(mine) == ["small-model"]
        assert mine["models"][0]["isDefault"] is True
        assert mine["agents"][0]["model"] == "small-model"
        assert names(admin) == ["base-model", "big-model", "small-model"]
        assert refused == 400
        # 預設的 base-model 中台不讓 alice 用：這一輪改用她能用的模型。
        assert s.model.requests[0]["model"] == "small-model"
        assert {call["user"] for call in gateway.calls} == {"alice", "boss"}

    async def test_answers_are_cached(self, site, gateway):
        gateway.allowed = {"alice": ["small-model"]}
        s = await site(extra=MODELS)
        alice = await s.login("alice", password_of("alice"))

        for _ in range(3):
            await alice.api("GET", "/project")

        assert len(gateway.calls) == 1

    async def test_gateway_failure_does_not_hide_models(self, site, gateway):
        gateway.failing = True
        s = await site(extra=MODELS)
        alice = await s.login("alice", password_of("alice"))

        status, project = await alice.api("GET", "/project")

        assert status == 200
        assert names(project) == ["base-model", "big-model", "small-model"]

    async def test_no_overlap_means_names_differ_so_nothing_is_hidden(
        self, site, gateway
    ):
        gateway.allowed = {"alice": ["openai/*"]}
        s = await site(extra=MODELS)
        alice = await s.login("alice", password_of("alice"))

        _, project = await alice.api("GET", "/project")

        assert names(project) == ["base-model", "big-model", "small-model"]

    async def test_proxy_prefix_counts_as_the_same_model(self, site, gateway):
        gateway.allowed = {"alice": ["small-model"]}
        s = await site(
            extra="model: litellm_proxy/small-model\nmodels: [big-model]\n"
        )
        alice = await s.login("alice", password_of("alice"))

        _, project = await alice.api("GET", "/project")

        assert names(project) == ["litellm_proxy/small-model"]

    async def test_direct_providers_are_not_asked(
        self, site, gateway, monkeypatch
    ):
        monkeypatch.delenv("HARNESS_BASE_URL")
        s = await site(extra=MODELS)
        alice = await s.login("alice", password_of("alice"))

        _, project = await alice.api("GET", "/project")

        assert gateway.calls == []
        assert names(project) == ["base-model", "big-model", "small-model"]

    async def test_changing_the_connection_asks_again(self, site, gateway):
        gateway.allowed = {"boss": ["small-model"]}
        s = await site(extra=MODELS)
        boss = await s.login("boss", password_of("boss"))
        await boss.api("GET", "/project")

        await boss.api("PUT", "/settings/models", {"models": ["small-model"]})
        await boss.api("GET", "/project")

        assert len(gateway.calls) == 2


class TestFriendlyErrors:
    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (
                Exception(
                    "Budget has been exceeded! Current cost: 3.2, "
                    "Max budget: 3.0"
                ),
                "額度已經用完",
            ),
            (
                litellm.RateLimitError(
                    "Max parallel request limit reached",
                    llm_provider="openai",
                    model="m",
                ),
                "重試",
            ),
            (
                Exception(
                    "key not allowed to access model. This key can only "
                    "access models=['a']. Tried to access b"
                ),
                "沒有使用這個模型的權限",
            ),
        ],
    )
    async def test_gateway_refusals_are_explained(self, site, error, expected):
        s = await site([error])
        alice = await s.login("alice", password_of("alice"))

        _, created = await alice.api("POST", "/sessions", {"text": "嗨"})
        detail = await alice.wait_idle(created["id"])

        assert detail["summary"]["status"] == "error"
        assert expected in detail["summary"]["error"]

    async def test_unknown_errors_keep_the_original_message(self, site):
        s = await site([RuntimeError("連線被重設")])
        alice = await s.login("alice", password_of("alice"))

        _, created = await alice.api("POST", "/sessions", {"text": "嗨"})
        detail = await alice.wait_idle(created["id"])

        assert detail["summary"]["error"] == "RuntimeError: 連線被重設"
