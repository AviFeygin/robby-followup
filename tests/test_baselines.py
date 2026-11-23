"""B. Baselines and thresholds: medians per shop, with fallback when history is thin."""
from __future__ import annotations

import pytest

from conftest import A, B, NOW, by_quote, mini, quote
from followup.platform.config import PLATFORM_BASELINES, ShopConfig
from followup.platform.timeutil import parse
from followup.state import Baseline, compute_baselines, project


def _baselines(view: dict) -> dict:
    return {b["metric"]: b for b in view["baselines"]}


def test_seed_medians_match_the_design_doc(eng):
    b = _baselines(eng.overview(A, NOW))
    assert (b["amount"]["value"], b["amount"]["sample_n"], b["amount"]["source"]) == (520000.0, 30, "shop")
    assert (b["first_view"]["value"], b["first_view"]["sample_n"]) == (56.0, 20)
    assert (b["first_contact"]["value"], b["first_contact"]["sample_n"]) == (35.0, 22)
    assert (b["view_gap"]["value"], b["view_gap"]["sample_n"]) == (46.5, 16)
    # Too few samples. The shop's own value is kept for display but not used.
    assert (b["view_to_reply"]["shop_value"], b["view_to_reply"]["sample_n"]) == (11.5, 4)
    assert b["view_to_reply"]["source"] == "platform"
    assert (b["time_to_accept"]["shop_value"], b["time_to_accept"]["sample_n"]) == (72.0, 1)
    assert b["time_to_accept"]["value"] == PLATFORM_BASELINES["time_to_accept"]


def test_thresholds_are_derived_from_the_medians(eng):
    th = eng.overview(A, NOW)["thresholds"]
    assert th["big_amount_cents"] == 2 * 520000
    assert th["hot_window_h"] == 46.5
    assert th["returned_gap_h"] == 93.0
    assert th["never_viewed_h"] == 56.0
    assert th["first_followup_h"] == 35.0
    assert th["stale_h"] == 168.0 and th["big_quiet_h"] == 84.0
    assert th["backoff_h"] == [118.5, 165.0]            # cooldown plus one view gap per attempt


def test_thin_history_falls_back_to_parent_then_platform():
    now = parse(NOW)
    states = project(
        [{"id": "Q-1", "customer_id": "C-1", "tech_name": "T", "amount_cents": 100000, "status": "open",
          "created_at": "2026-08-01T00:00:00Z", "last_contact_at": None}], [], now)
    parent = {"amount": Baseline("amount", 300000.0, 400, "shop"),
              "view_gap": Baseline("view_gap", 30.0, 3, "shop")}        # the parent is thin here too
    out = compute_baselines(states.values(), now, 15, 90, PLATFORM_BASELINES, parent)
    assert (out["amount"].value, out["amount"].source, out["amount"].sample_n) == (300000.0, "parent", 1)
    assert (out["view_gap"].value, out["view_gap"].source) == (PLATFORM_BASELINES["view_gap"], "platform")
    assert out["amount"].shop_value == 100000.0


def test_one_extreme_quote_barely_moves_the_big_quote_line(eng, seed):
    quotes, _ = seed
    before = eng.overview(A, NOW)["thresholds"]["big_amount_cents"]
    eng.load_seed(A, [quote("Q-HUGE", "+19175559999", 600000, "2026-08-05T00:00:00Z")], [])
    after = eng.overview(A, NOW)["thresholds"]["big_amount_cents"]
    assert after == before        # a mean would have jumped by tens of thousands of dollars


def test_the_same_quote_is_big_in_one_shop_and_not_in_another(eng):
    # Shop B is the same seed with quarter size jobs, so its median is a quarter.
    a, b = eng.overview(A, NOW), eng.overview(B, NOW)
    assert _baselines(a)["amount"]["value"] == 520000.0
    assert _baselines(b)["amount"]["value"] == 130000.0
    six_thousand = 600000
    assert six_thousand < a["thresholds"]["big_amount_cents"]
    assert six_thousand >= b["thresholds"]["big_amount_cents"]


def test_a_shop_with_no_history_still_runs():
    engine = mini([quote("Q-1", "+19175550001", 900, "2026-08-10T00:00:00Z")], [])
    view = engine.overview("s1", NOW)
    assert {b["source"] for b in view["baselines"]} == {"platform"}
    kind, item = by_quote(view)["Q-1"]
    assert kind == "candidate" and item["tier"] == 4
    assert engine.run("s1", NOW)["created"]


def test_baselines_only_use_the_trailing_window():
    old = [quote(f"Q-old{i}", f"+1917555{i:04d}", 99999, "2026-01-01T00:00:00Z") for i in range(20)]
    recent = [quote(f"Q-new{i}", f"+1917556{i:04d}", 1000, "2026-08-01T00:00:00Z") for i in range(20)]
    engine = mini(old + recent, [], ShopConfig(shop_name="Test Shop"))
    amount = _baselines(engine.overview("s1", NOW))["amount"]
    assert amount["value"] == 100000.0 and amount["sample_n"] == 20


def test_baselines_are_stored_with_their_source_on_each_run(eng):
    eng.run(A, NOW)
    rows = eng.session(A).rows("SELECT metric, source, sample_n FROM baselines WHERE shop_id = :shop_id")
    assert {r["metric"]: r["source"] for r in rows}["time_to_accept"] == "platform"
    assert len(rows) == 6


@pytest.mark.parametrize("min_samples,expected", [(15, "shop"), (40, "platform")])
def test_min_samples_decides_the_source(seed, min_samples, expected):
    quotes, events = seed
    engine = mini(quotes, events, ShopConfig(shop_name="Test Shop", min_samples=min_samples))
    assert _baselines(engine.overview("s1", NOW))["amount"]["source"] == expected
