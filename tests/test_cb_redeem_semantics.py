from backend.services.cb_redeem_semantics import normalize_redeem_state
import pytest


def test_missing_redeem_is_unknown_not_monitoring():
    assert normalize_redeem_state(None)["status_code"] == "UNKNOWN"


@pytest.mark.parametrize("value", [True, float('inf'), float('nan'), "-"])
def test_invalid_count_does_not_crash(value):
    assert normalize_redeem_state({"redeem_remain_days": value})["trigger_days_remaining"] is None


def test_no_redeem_announcement_takes_precedence_over_met_count():
    assert normalize_redeem_state({"redeem_flag": "N", "redeem_remain_days": 0, "redeem_real_days": 15, "redeem_count_days": 15})["status_code"] == "NO_REDEEM_ANNOUNCED"


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


def test_business_filter_excludes_announced_but_keeps_near_maturity():
    from backend.services.cb_screen import screen_bonds_live
    records = [{"bond_id": bid, "price": 100, "dblow": 100, "icons": {"R": ""}} for bid in ("110001", "110002")]
    redeem = [{"bond_id": "110001", "redeem_icon": "R", "redeem_flag": "Y", "redeem_price": 110},
              {"bond_id": "110002", "redeem_icon": "R", "redeem_flag": "X", "delist_dt": "2026-10-09", "redeem_price": 110}]
    template = {"conditions": [{"id": "r", "field": "redeem_status_code", "op": "not_in", "value": ["ANNOUNCED_REDEEM"], "enabled": True}], "strategy_factors": []}
    result = screen_bonds_live(records, template, redeem_cells=redeem)
    assert [r["code"] for r in result["rows"]] == ["110002"]
    assert result["rows"][0]["redeem"].startswith("临近到期")


def test_v3_icons_migrate_once_and_require_confirmation():
    from backend.services.cb_template_migration import migrate_config_to_v3
    source = {"version": 3, "templates": [{"id": "a", "conditions": [{"id": "r", "field": "redeem_icons", "op": "not_any", "value": ["R"]}]}]}
    migrated = migrate_config_to_v3(source)
    assert migrated["templates"][0]["conditions"] == []
    assert migrated["templates"][0]["migration_issues"][0]["status"] == "pending"
    assert migrate_config_to_v3(migrated) == migrated
    assert source["templates"][0]["conditions"]
