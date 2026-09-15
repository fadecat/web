from datetime import date, datetime, timedelta
import threading
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from backend.models.database import Base

from backend.models.jisilu_account import JisiluAccount
from backend.services.jisilu_account_pool import JisiluAccountPool


def test_crud_serialization_never_exposes_secrets(db):
    pool = JisiluAccountPool(db)
    account = pool.create(name="主账号", username="u", password="secret", cookie="cookie-secret")
    assert account["username"] == "u"
    assert "password" not in account and "cookie" not in account
    listed = pool.list()
    assert listed[0]["id"] == account["id"]
    updated = pool.update(account["id"], name="新名", password="")
    assert updated["name"] == "新名"
    row = db.get(JisiluAccount, account["id"])
    assert row.password == "secret"
    assert pool.delete(account["id"]) is True


def test_select_available_prefers_daily_requests_then_oldest_request(db):
    pool = JisiluAccountPool(db)
    a = pool.create(name="a", username="a", password="a")
    b = pool.create(name="b", username="b", password="b")
    pool.mark_request(a["id"])
    row_b = db.get(JisiluAccount, b["id"])
    row_b.last_request_at = datetime(2020, 1, 1)
    db.commit()
    assert pool.select_available()["id"] == b["id"]
    pool.mark_cooldown(b["id"], timedelta(minutes=10))
    assert pool.select_available()["id"] == a["id"]


def test_daily_request_and_login_counters_reset_by_shanghai_business_date(db):
    pool = JisiluAccountPool(db)
    account = pool.create(name="a", username="a", password="a")
    row = db.get(JisiluAccount, account["id"])
    row.daily_request_date = date(2000, 1, 1)
    row.daily_request_count = 8
    row.daily_login_date = date(2000, 1, 1)
    row.daily_login_count = 8
    db.commit()
    pool.mark_request(account["id"])
    assert db.get(JisiluAccount, account["id"]).daily_request_count == 1
    assert pool.can_login(account["id"], daily_limit=2) is True
    pool.mark_login(account["id"])
    assert pool.can_login(account["id"], daily_limit=2) is True
    pool.mark_login(account["id"])
    assert pool.can_login(account["id"], daily_limit=2) is False


def test_failure_marks_invalid_after_threshold_and_cooldown(db):
    pool = JisiluAccountPool(db, failure_threshold=2)
    account = pool.create(name="a", username="a", password="a")
    pool.mark_failure(account["id"], "bad login")
    assert db.get(JisiluAccount, account["id"]).status == "cooldown"
    pool.mark_failure(account["id"], "bad login")
    assert db.get(JisiluAccount, account["id"]).status == "invalid"


def test_success_clears_failure_and_cooldown(db):
    pool = JisiluAccountPool(db)
    account = pool.create(name="a", username="a", password="a")
    pool.mark_failure(account["id"], "temporary")
    pool.mark_success(account["id"], cookie="new-cookie")
    row = db.get(JisiluAccount, account["id"])
    assert row.status == "ready" and row.consecutive_failures == 0
    assert row.cookie == "new-cookie"


def test_acquire_is_atomic_across_pool_instances_and_counts(db):
    first, second = JisiluAccountPool(db), JisiluAccountPool(db)
    a = first.create(name="a", username="a", password="a")
    b = first.create(name="b", username="b", password="b")
    got1 = first.acquire_account()
    got2 = second.acquire_account()
    assert {got1["id"], got2["id"]} == {a["id"], b["id"]}
    assert db.get(JisiluAccount, a["id"]).daily_request_count == 1
    assert db.get(JisiluAccount, b["id"]).daily_request_count == 1


def test_reserve_login_enforces_daily_limit(db):
    pool = JisiluAccountPool(db)
    account = pool.create(name="a", username="a", password="a")
    assert pool.reserve_login(account["id"], daily_limit=2) is True
    assert pool.reserve_login(account["id"], daily_limit=2) is True
    assert pool.reserve_login(account["id"], daily_limit=2) is False


def test_validation_duplicate_and_configured_flags(db):
    pool = JisiluAccountPool(db)
    account = pool.create(name="a", username="a", password="secret", cookie="c")
    assert account["password_configured"] is True and account["cookie_configured"] is True
    for kwargs in ({"name": "", "username": "x", "password": "p"}, {"name": "x", "username": "", "password": "p"}, {"name": "x", "username": "x", "password": ""}, {"name": "x", "username": "x2", "password": "p", "status": "bogus"}, {"name": "x", "username": "x3", "password": "p", "extra": 1}):
        try:
            pool.create(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid account input accepted")
    try:
        pool.create(name="dup", username="a", password="p")
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate username accepted")


def test_expired_cooldown_becomes_available(db):
    pool = JisiluAccountPool(db)
    account = pool.create(name="a", username="a", password="a")
    pool.mark_cooldown(account["id"], timedelta(0))
    selected = pool.select_available()
    assert selected["id"] == account["id"] and selected["status"] == "ready"


def test_update_duplicate_rolls_back_and_session_remains_usable(db):
    pool = JisiluAccountPool(db)
    a = pool.create(name="a", username="a", password="a")
    b = pool.create(name="b", username="b", password="b")
    try:
        pool.update(b["id"], username="a")
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate username update accepted")
    assert pool.get(a["id"])["username"] == "a"
    assert pool.get(b["id"])["username"] == "b"


def test_acquire_filters_disabled_invalid_and_active_cooldown(db):
    pool = JisiluAccountPool(db)
    disabled = pool.create(name="d", username="d", password="d", enabled=False)
    invalid = pool.create(name="i", username="i", password="i", status="invalid")
    active = pool.create(name="c", username="c", password="c")
    pool.mark_cooldown(active["id"], timedelta(minutes=10))
    assert pool.acquire_account() is None
    pool.mark_cooldown(active["id"], timedelta(0))
    assert pool.acquire_account()["id"] == active["id"]
    assert pool.get(disabled["id"])["status"] == "disabled"
    assert pool.get(invalid["id"])["status"] == "invalid"


def test_mark_login_is_budgeted_and_checks_state(db):
    pool = JisiluAccountPool(db)
    account = pool.create(name="a", username="a", password="a")
    assert pool.mark_login(account["id"], daily_limit=1) is True
    assert pool.mark_login(account["id"], daily_limit=1) is False
    pool.update(account["id"], enabled=False)
    assert pool.mark_login(account["id"], daily_limit=10) is False


def test_two_threads_acquire_distinct_accounts_with_shared_lock():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    JisiluAccountPool(seed).create(name="a", username="a", password="a")
    JisiluAccountPool(seed).create(name="b", username="b", password="b")
    seed.close()
    results, errors = [], []
    def worker():
        session = Session()
        try:
            results.append(JisiluAccountPool(session).acquire_account())
        except Exception as exc:
            errors.append(exc)
        finally:
            session.close()
    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert not errors and {item["id"] for item in results} == {1, 2}
    engine.dispose()


def test_mark_request_rejects_unavailable_accounts(db):
    pool = JisiluAccountPool(db)
    disabled = pool.create(name="d", username="d", password="d", enabled=False)
    invalid = pool.create(name="i", username="i", password="i", status="invalid")
    cooldown = pool.create(name="c", username="c", password="c")
    pool.mark_cooldown(cooldown["id"], timedelta(minutes=5))
    for account in (disabled, invalid, cooldown):
        assert pool.mark_request(account["id"]) is None
        assert db.get(JisiluAccount, account["id"]).daily_request_count == 0


def test_can_login_is_read_only_and_late_success_does_not_restore_status(db):
    pool = JisiluAccountPool(db)
    account = pool.create(name="a", username="a", password="a", status="invalid")
    before = db.get(JisiluAccount, account["id"]).daily_login_count
    assert pool.can_login(account["id"], daily_limit=5) is False
    assert db.get(JisiluAccount, account["id"]).daily_login_count == before
    pool.mark_success(account["id"], cookie="late")
    row = db.get(JisiluAccount, account["id"])
    assert row.status == "invalid" and row.consecutive_failures == 0
    disabled = pool.create(name="d", username="d", password="d", enabled=False)
    pool.mark_success(disabled["id"], cookie="late")
    assert db.get(JisiluAccount, disabled["id"]).status == "disabled"


def test_two_threads_acquire_same_account_preserve_both_count_updates():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    JisiluAccountPool(seed).create(name="a", username="a", password="a")
    seed.close()
    results, errors = [], []
    def worker():
        session = Session()
        try: results.append(JisiluAccountPool(session).acquire_account())
        except Exception as exc: errors.append(exc)
        finally: session.close()
    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    check = Session()
    assert not errors and all(results)
    assert check.get(JisiluAccount, 1).daily_request_count == 2
    check.close(); engine.dispose()


def test_failure_error_is_sanitized_and_truncated(db):
    pool = JisiluAccountPool(db)
    account = pool.create(name="a", username="a", password="secret")
    raw = "cookie=COOKIE; password=PASS authorization: Bearer TOKEN set-cookie: SID; " + "x" * 600
    pool.mark_failure(account["id"], raw)
    error = pool.get(account["id"])["last_error"]
    assert len(error) <= 500
    assert all(secret not in error for secret in ("COOKIE", "PASS", "TOKEN", "SID"))


def test_login_budget_check_is_read_only_across_days(db):
    pool = JisiluAccountPool(db)
    account = pool.create(name="a", username="a", password="a")
    row = db.get(JisiluAccount, account["id"])
    row.daily_login_date = date(2000, 1, 1); row.daily_login_count = 99; db.commit()
    assert pool.can_login(account["id"], daily_limit=1) is True
    assert row.daily_login_date == date(2000, 1, 1) and row.daily_login_count == 99


def test_commit_failure_rolls_back_and_session_remains_usable(db, monkeypatch):
    pool = JisiluAccountPool(db)
    account = pool.create(name="a", username="a", password="a")
    real_commit = db.commit
    state = {"failed": False}
    def flaky_commit():
        if not state["failed"]:
            state["failed"] = True
            raise RuntimeError("boom")
        return real_commit()
    monkeypatch.setattr(db, "commit", flaky_commit)
    try:
        pool.mark_request(account["id"])
    except RuntimeError:
        pass
    else:
        raise AssertionError("commit failure swallowed")
    monkeypatch.setattr(db, "commit", real_commit)
    assert pool.get(account["id"])["daily_request_count"] == 0


def test_parameter_boundaries_are_rejected(db):
    for kwargs in ({"name": "a" * 129, "username": "a", "password": "p"}, {"name": "a", "username": "u" * 129, "password": "p"}, {"name": "a", "username": "a", "password": 1}, {"name": "a", "username": "a", "password": "p", "cookie": 1}):
        try: JisiluAccountPool(db).create(**kwargs)
        except ValueError: pass
        else: raise AssertionError("invalid boundary accepted")
    for kwargs in ({"failure_threshold": 0}, {"cooldown_seconds": -1}):
        try: JisiluAccountPool(db, **kwargs)
        except ValueError: pass
        else: raise AssertionError("invalid pool option accepted")
    pool = JisiluAccountPool(db)
    account = pool.create(name="a", username="a", password="p")
    for limit in (0, -1):
        assert pool.reserve_login(account["id"], daily_limit=limit) is False


def test_error_sanitizer_covers_tokens_json_and_query_secrets(db):
    pool = JisiluAccountPool(db)
    account = pool.create(name="a", username="a", password="p")
    raw = 'Bearer TOPSECRET {"password":"P","access_token":"A","session":"S"} https://x.test?a=1&token=URLSECRET'
    pool.mark_failure(account["id"], raw)
    error = pool.get(account["id"])["last_error"]
    assert all(secret not in error for secret in ("TOPSECRET", '"P"', '"A"', '"S"', "URLSECRET"))
    assert pool.serialize(db.get(JisiluAccount, account["id"]))["last_error"] == error


def test_secret_types_and_column_lengths_are_validated(db):
    pool = JisiluAccountPool(db)
    for kwargs in ({"name": "a", "username": "a", "password": "p" * 513}, {"name": "b", "username": "b", "password": "p", "cookie": "c" * 4097}, {"name": "c", "username": "c", "password": 1}, {"name": "d", "username": "d", "password": "p", "cookie": 1}):
        try: pool.create(**kwargs)
        except ValueError: pass
        else: raise AssertionError("invalid secret accepted")
    account = pool.create(name="e", username="e", password="p")
    for cookie in (1, "c" * 4097):
        try: pool.mark_success(account["id"], cookie=cookie)
        except ValueError: pass
        else: raise AssertionError("invalid success cookie accepted")
