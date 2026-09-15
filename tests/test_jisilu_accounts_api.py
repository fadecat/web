from datetime import datetime, timedelta, timezone

from backend.models.jisilu_account import JisiluAccount


def test_accounts_list_is_redacted_and_has_summary(contract_client):
    contract_client.post("/api/jisilu/accounts", json={"name": "A", "username": "user-a", "password": "secret"})
    response = contract_client.get("/api/jisilu/accounts")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total"] == 1
    assert body["summary"]["enabled"] == 1
    assert body["items"][0]["username"] == "user-a"
    assert "secret" not in response.text and "sid=private" not in response.text
    assert body["items"][0]["password_configured"] is True


def test_accounts_crud_duplicate_missing_and_empty_password(contract_client):
    created = contract_client.post("/api/jisilu/accounts", json={"name": "A", "username": "same", "password": "secret"})
    assert created.status_code == 201
    account_id = created.json()["id"]
    duplicate = contract_client.post("/api/jisilu/accounts", json={"name": "B", "username": "same", "password": "other"})
    assert duplicate.status_code == 409
    updated = contract_client.put(f"/api/jisilu/accounts/{account_id}", json={"name": "renamed", "password": ""})
    assert updated.status_code == 200 and updated.json()["name"] == "renamed"
    missing = contract_client.put("/api/jisilu/accounts/9999", json={"name": "x"})
    assert missing.status_code == 404
    assert contract_client.delete(f"/api/jisilu/accounts/{account_id}").status_code == 204
    assert contract_client.delete(f"/api/jisilu/accounts/{account_id}").status_code == 404


def test_reset_status_clears_cooldown_and_failures_without_login(contract_client, monkeypatch):
    from backend.services import jisilu

    created = contract_client.post("/api/jisilu/accounts", json={"name": "A", "username": "reset", "password": "secret"})
    account_id = created.json()["id"]
    monkeypatch.setattr(jisilu, "login_account", lambda *_: (_ for _ in ()).throw(AssertionError("must not login")))
    response = contract_client.post(f"/api/jisilu/accounts/{account_id}/reset-status")
    assert response.status_code == 200
    assert response.json()["account"]["status"] == "ready"


def test_check_uses_valid_cookie_without_login(contract_client, contract_db_state, monkeypatch):
    from backend.services import jisilu

    created = contract_client.post("/api/jisilu/accounts", json={"name": "A", "username": "check", "password": "secret"})
    account_id = created.json()["id"]
    row = contract_db_state.opened[-1]._session.get(JisiluAccount, account_id)
    row.cookie = "sid=private"; contract_db_state.opened[-1]._session.commit()
    calls = []
    monkeypatch.setattr(jisilu, "_probe_once", lambda cookie: calls.append(cookie) or (True, False))
    monkeypatch.setattr(jisilu, "login_account", lambda *_: (_ for _ in ()).throw(AssertionError("must not login")))
    response = contract_client.post(f"/api/jisilu/accounts/{account_id}/check")
    assert response.status_code == 200 and response.json()["status"] == "ready"
    assert calls == ["sid=private"]


def test_check_reserves_once_then_logs_in_when_cookie_invalid(contract_client, contract_db_state, monkeypatch):
    from backend.services import jisilu

    created = contract_client.post("/api/jisilu/accounts", json={"name": "A", "username": "login", "password": "secret"})
    account_id = created.json()["id"]
    monkeypatch.setattr(jisilu, "login_account", lambda username, password: "sid=new")
    response = contract_client.post(f"/api/jisilu/accounts/{account_id}/check")
    assert response.status_code == 200 and response.json()["status"] == "ready"
    row = contract_client  # keep test focused on API response and no secret leakage
    assert "sid=new" not in response.text and "secret" not in response.text
    stored = contract_db_state.opened[-1]._session.get(JisiluAccount, account_id)
    assert stored.daily_login_count == 1 and stored.daily_request_count == 1


def test_reset_status_rolls_back_and_hides_commit_error(contract_client, monkeypatch):
    from sqlalchemy.orm import Session
    created = contract_client.post("/api/jisilu/accounts", json={"name": "A", "username": "commit", "password": "p"})
    account_id = created.json()["id"]
    def fail_commit(self):
        raise RuntimeError("database secret details")
    monkeypatch.setattr(Session, "commit", fail_commit)
    response = contract_client.post(f"/api/jisilu/accounts/{account_id}/reset-status")
    assert response.status_code == 500
    assert response.json()["detail"] == "状态重置失败"


def test_management_payload_cannot_set_runtime_secrets_or_status(contract_client):
    assert contract_client.post("/api/jisilu/accounts", json={"name": "A", "username": "x", "password": "p", "cookie": "c"}).status_code == 422
    assert contract_client.post("/api/jisilu/accounts", json={"name": "A", "username": "x", "password": "p", "status": "ready"}).status_code == 422


def test_check_network_probe_does_not_login_or_mark_failure_and_counts_requests(contract_client, contract_db_state, monkeypatch):
    from backend.services import jisilu
    created = contract_client.post("/api/jisilu/accounts", json={"name": "A", "username": "net", "password": "secret"})
    account_id = created.json()["id"]
    from backend.models.jisilu_account import JisiluAccount
    # Seed a cookie through the request DB dependency's shared session.
    session = contract_db_state.opened[-1]._session
    row = session.get(JisiluAccount, account_id); row.cookie = "sid=old"; session.commit()
    class Network:
        def __init__(self): self.calls = 0
        def __call__(self, cookie):
            self.calls += 1
            return False, True
    probe = Network()
    monkeypatch.setattr(jisilu, "_probe_once", probe)
    monkeypatch.setattr(jisilu, "login_account", lambda *_: (_ for _ in ()).throw(AssertionError("must not login")))
    monkeypatch.setattr(jisilu.time, "sleep", lambda *_: None)
    response = contract_client.post(f"/api/jisilu/accounts/{account_id}/check")
    assert response.status_code == 502 and probe.calls == 2
    assert row.daily_request_count == 2


def test_expired_cooldown_is_normalized_in_list(contract_client, contract_db_state):
    created = contract_client.post("/api/jisilu/accounts", json={"name": "A", "username": "expired", "password": "p"})
    account_id = created.json()["id"]
    session = contract_db_state.opened[-1]._session
    row = session.get(JisiluAccount, account_id); row.status = "cooldown"; row.cooldown_until = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1); session.commit()
    response = contract_client.get("/api/jisilu/accounts")
    assert response.json()["items"][0]["status"] == "ready"
    assert response.json()["summary"]["ready"] == 1 and response.json()["summary"]["cooldown"] == 0
