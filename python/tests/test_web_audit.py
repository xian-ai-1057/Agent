"""稽核紀錄：只能新增、雜湊鏈、查詢與匯出，以及哪些操作會被記下來。"""

import json
import sqlite3

import pytest

from harness.web import accounts as accounts_lib
from harness.web import audit as audit_lib
from tests import conftest
from tests import test_web_accounts
from tests.test_web_accounts import WRITE
from tests.test_web_accounts import password_of
from tests.test_web_accounts import user_id

site = test_web_accounts.site
fast_hash = test_web_accounts.fast_hash

BOSS = accounts_lib.User("id-boss", "boss", "老闆", "admin")


def log_of(root) -> audit_lib.AuditLog:
    return audit_lib.AuditLog.for_project(root)


def actions(root) -> list[str]:
    return [item.action for item in reversed(log_of(root).query(limit=200))]


class TestAuditLog:
    def test_records_are_chained_and_verified(self, tmp_path):
        log = audit_lib.AuditLog(tmp_path / "h.db", clock=lambda: 100.0)

        first = log.record("auth.login", actor=BOSS, address="10.0.0.1")
        second = log.record(
            "group.update", actor=BOSS, target="人資", detail={"x": 1}
        )

        assert (first.seq, second.seq) == (1, 2)
        assert second.prev_hash == first.hash
        assert log.verify() == {"ok": True, "checked": 2}
        [latest, oldest] = log.query()
        assert latest.info()["label"] == "修改群組"
        assert latest.info()["at"] == 100_000
        assert oldest.address == "10.0.0.1"

    def test_rows_cannot_be_changed_or_deleted(self, tmp_path):
        log = audit_lib.AuditLog(tmp_path / "h.db")
        log.record("auth.login", actor=BOSS)
        db = sqlite3.connect(tmp_path / "h.db")

        try:
            with pytest.raises(sqlite3.DatabaseError, match="只能新增"):
                db.execute("UPDATE audit_log SET actor_name = 'x'")
            with pytest.raises(sqlite3.DatabaseError, match="只能新增"):
                db.execute("DELETE FROM audit_log")
        finally:
            db.close()

    def test_tampering_around_the_triggers_is_detected(self, tmp_path):
        log = audit_lib.AuditLog(tmp_path / "h.db")
        for _ in range(3):
            log.record("auth.login", actor=BOSS)
        db = sqlite3.connect(tmp_path / "h.db")
        db.execute("DROP TRIGGER audit_log_no_update")
        db.execute("UPDATE audit_log SET actor_name = 'x' WHERE seq = 2")
        db.commit()
        db.close()

        assert log.verify() == {"ok": False, "checked": 1, "brokenAt": 2}

    def test_deleted_rows_are_detected(self, tmp_path):
        log = audit_lib.AuditLog(tmp_path / "h.db")
        for _ in range(3):
            log.record("auth.login", actor=BOSS)
        db = sqlite3.connect(tmp_path / "h.db")
        db.execute("DROP TRIGGER audit_log_no_delete")
        db.execute("DELETE FROM audit_log WHERE seq = 2")
        db.commit()
        db.close()

        assert log.verify()["brokenAt"] == 2

    def test_unknown_actions_are_refused(self, tmp_path):
        log = audit_lib.AuditLog(tmp_path / "h.db")

        with pytest.raises(ValueError):
            log.record("whatever", actor=BOSS)

    def test_filters_and_pages(self, tmp_path):
        times = iter([10.0, 20.0, 30.0, 40.0])
        log = audit_lib.AuditLog(tmp_path / "h.db", clock=lambda: next(times))
        alice = accounts_lib.User("id-a", "alice", "A", "member")
        log.record("auth.login", actor=alice)
        log.record("auth.login_failed", target="alice_50%")
        log.record("member.update", actor=BOSS, target="alice")
        log.record("group.create", actor=BOSS, target="人資")

        def seqs(filters=audit_lib.NO_FILTERS, **options):
            return [item.seq for item in log.query(filters, **options)]

        assert seqs(audit_lib.Filters(category="auth")) == [2, 1]
        assert seqs(audit_lib.Filters(actor="ALICE")) == [3, 2, 1]
        assert seqs(audit_lib.Filters(actor="50%")) == [2]
        assert seqs(audit_lib.Filters(since=20, until=40)) == [3, 2]
        assert seqs(before=3, limit=1) == [2]
        assert log.count(audit_lib.Filters(category="member")) == 1
        with pytest.raises(audit_lib.AuditError):
            log.query(audit_lib.Filters(category="nope"))

    def test_csv_has_bom_and_defuses_formulas(self, tmp_path):
        log = audit_lib.AuditLog(tmp_path / "h.db")
        log.record("auth.login_failed", target="=HYPERLINK(1)")

        text = "".join(audit_lib.to_csv(log.export()))

        assert text.startswith("﻿流水號,時間,帳號")
        assert ",'=HYPERLINK(1)," in text

    def test_jsonl_keeps_the_hashes(self, tmp_path):
        log = audit_lib.AuditLog(tmp_path / "h.db")
        entry = log.record("auth.login", actor=BOSS)

        [line] = list(audit_lib.to_jsonl(log.export()))

        data = json.loads(line)
        assert data["hash"] == entry.hash
        assert data["actor"]["username"] == "boss"

    def test_long_details_are_truncated(self, tmp_path):
        log = audit_lib.AuditLog(tmp_path / "h.db")

        entry = log.record("auth.login", actor=BOSS, detail={"x": "a" * 9000})

        assert "truncated" in entry.detail
        assert len(entry.detail["truncated"]) <= 4000


class TestRecordedActions:
    async def test_logins_are_recorded(self, site):
        s = await site()
        await s.login("alice", password_of("alice"))
        anonymous = s.anonymous()
        await anonymous.api(
            "POST",
            "/auth/login",
            {"username": "alice", "password": "bad-guess-1"},
        )
        await anonymous.api(
            "POST", "/auth/login", {"username": "no such!", "password": "x"}
        )

        entries = list(log_of(s.root).export())

        assert [item.action for item in entries] == [
            "auth.login",
            "auth.login_failed",
            "auth.login_failed",
        ]
        assert entries[0].actor_name == "alice"
        assert entries[0].address == "127.0.0.1"
        assert entries[1].actor_name == "" and entries[1].target == "alice"
        assert "bad-guess-1" not in json.dumps(
            [item.info() for item in entries]
        )

    async def test_logout_and_password_change(self, site):
        s = await site()
        alice = await s.login("alice", password_of("alice"))
        bob = await s.login("bob", password_of("bob"))

        await alice.api(
            "PUT",
            "/auth/password",
            {"current": password_of("alice"), "next": "another-pass-1"},
        )
        await bob.api("POST", "/auth/logout", {})
        # 已經登出（cookie 失效）時不再記一次。
        await bob.api("POST", "/auth/logout", {})

        entries = list(log_of(s.root).export())
        assert [(item.action, item.actor_name) for item in entries][-2:] == [
            ("auth.password", "alice"),
            ("auth.logout", "bob"),
        ]

    async def test_member_changes_are_recorded(self, site):
        s = await site()
        boss = await s.login("boss", password_of("boss"))
        alice_id = user_id(s.root, "alice")

        await boss.api(
            "POST", "/admin/users", {"username": "carol", "displayName": "C"}
        )
        await boss.api("PATCH", f"/admin/users/{alice_id}", {"disabled": True})
        await boss.api("PATCH", f"/admin/users/{alice_id}", {})
        _, reset = await boss.api(
            "POST", f"/admin/users/{alice_id}/password", {}
        )

        entries = log_of(s.root).query(
            audit_lib.Filters(category="member"), limit=10
        )
        assert [item.action for item in reversed(entries)] == [
            "member.create",
            "member.update",
            "member.password_reset",
        ]
        update = entries[1]
        assert update.target == "alice"
        assert update.detail == {"changes": {"disabled": [False, True]}}
        # 新密碼不會出現在紀錄裡。
        assert reset["password"] not in json.dumps(
            [item.info() for item in entries]
        )

    async def test_group_and_settings_changes_are_recorded(self, site):
        s = await site()
        boss = await s.login("boss", password_of("boss"))

        _, group = await boss.api(
            "POST", "/admin/groups", {"name": "資訊處", "toolsets": ["shell"]}
        )
        await boss.api(
            "PATCH", f"/admin/groups/{group['id']}", {"agents": ["main"]}
        )
        await boss.api("DELETE", f"/admin/groups/{group['id']}")
        await boss.api(
            "PUT",
            "/settings/permissions",
            {"mode": "ask", "allow": ["read_file"], "ask": [], "deny": []},
        )
        await boss.api(
            "PUT", "/settings/model", {"model": "m-1", "maxTurns": 5}
        )
        await boss.api("PUT", "/settings/models", {"models": ["m-2"]})

        entries = list(log_of(s.root).export())
        assert [item.action for item in entries][-6:] == [
            "group.create",
            "group.update",
            "group.delete",
            "settings.permissions",
            "settings.model",
            "settings.models",
        ]
        by_action = {item.action: item for item in entries}
        assert by_action["group.update"].detail["changes"] == {
            "agents": ["main"]
        }
        assert by_action["group.delete"].target == "資訊處"
        assert by_action["settings.permissions"].detail["allow"] == [
            "read_file"
        ]

    async def test_tool_approvals_are_recorded(self, site):
        s = await site(
            [conftest.tool_response(WRITE), conftest.text_response("好了")]
        )
        alice = await s.login("alice", password_of("alice"))
        await alice.listen()
        _, created = await alice.api("POST", "/sessions", {"text": "寫 a"})
        approval = (await alice.wait_for("approval"))["approval"]

        await alice.api(
            "POST",
            f"/sessions/{created['id']}/approvals/{approval['id']}",
            {"decision": "once"},
        )
        await alice.wait_idle(created["id"])

        [entry] = log_of(s.root).query(audit_lib.Filters(category="approval"))
        assert entry.actor_name == "alice"
        assert entry.target == "write_file"
        assert entry.detail["decision"] == "once"
        assert entry.detail["sessionId"] == created["id"]
        assert '"path": "a.md"' in entry.detail["arguments"]


class TestAuditApi:
    async def test_only_admins_can_read_or_export(self, site):
        s = await site()
        alice = await s.login("alice", password_of("alice"))

        for path in (
            "/admin/audit",
            "/admin/audit/verify",
            "/admin/audit/export",
        ):
            status, _ = await alice.api("GET", path)
            assert status == 403, path

    async def test_list_filter_and_verify(self, site):
        s = await site()
        boss = await s.login("boss", password_of("boss"))
        await s.login("alice", password_of("alice"))

        _, page = await boss.api("GET", "/admin/audit?limit=1")
        _, older = await boss.api(
            "GET", f"/admin/audit?before={page['entries'][0]['seq']}"
        )
        _, filtered = await boss.api("GET", "/admin/audit?actor=alice")
        bad, _ = await boss.api("GET", "/admin/audit?category=nope")
        _, verified = await boss.api("GET", "/admin/audit/verify")

        assert page["total"] == 2
        assert [item["actor"]["username"] for item in page["entries"]] == [
            "alice"
        ]
        assert [item["actor"]["username"] for item in older["entries"]] == [
            "boss"
        ]
        assert filtered["total"] == 1
        assert page["categories"]["auth"] == "登入"
        assert bad == 400
        assert verified == {"ok": True, "checked": 2}

    async def test_export_is_itself_recorded(self, site):
        s = await site()
        boss = await s.login("boss", password_of("boss"))

        response = await s.client.get(
            "/api/admin/audit/export?format=csv&category=audit",
            headers=boss.headers,
        )
        text = await response.text()
        jsonl = await s.client.get(
            "/api/admin/audit/export?format=jsonl", headers=boss.headers
        )
        bad, _ = await boss.api("GET", "/admin/audit/export?format=xml")

        assert response.status == 200
        assert "attachment" in response.headers["Content-Disposition"]
        assert text.startswith("﻿流水號")
        assert "audit.export" in text and "auth.login" not in text
        lines = (await jsonl.text()).strip().split("\n")
        assert [json.loads(line)["action"] for line in lines] == [
            "auth.login",
            "audit.export",
            "audit.export",
        ]
        assert bad == 400
