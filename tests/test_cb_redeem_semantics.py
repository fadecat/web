from backend.services.cb_redeem_semantics import normalize_redeem_state


def test_r_with_announcement_flag_is_announced_redeem():
    state = normalize_redeem_state({
        "redeem_icon": "R", "redeem_flag": "Y",
        "redeem_dt": "2026-09-11", "delist_dt": "2026-09-11",
        "redeem_price": "118.00", "real_force_redeem_price": "101.332",
        "redeem_remain_days": 0, "redeem_real_days": 21,
        "redeem_count_days": 15, "redeem_total_days": 30,
    })
    assert state["status_code"] == "ANNOUNCED_REDEEM"
    assert state["announced_redeem_price"] == 101.332
    assert state["maturity_redeem_price"] == 118.0


def test_r_without_announcement_is_near_maturity():
    state = normalize_redeem_state({
        "redeem_icon": "R", "redeem_flag": "X",
        "redeem_dt": None, "delist_dt": "2026-10-09",
        "redeem_remain_days": -1,
    })
    assert state["status_code"] == "NEAR_MATURITY"
    assert state["trigger_days_remaining"] is None


def test_negative_remain_is_not_a_countdown():
    state = normalize_redeem_state({
        "redeem_icon": "", "redeem_flag": "X",
        "redeem_remain_days": -1, "redeem_real_days": 0,
        "redeem_count_days": 15, "redeem_total_days": 30,
    })
    assert state["status_code"] == "MONITORING"
    assert state["trigger_days_remaining"] is None

