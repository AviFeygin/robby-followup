"""Capacity and anchoring: offers scale with the schedule, inside the shop's limits."""
from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from conftest import A, B, NOW, by_quote, event, mini, quote, run_and_approve
from followup.capacity import CapacitySnapshot, NullCapacity, Opening
from followup.bootstrap import demo_engine
from followup.platform.config import AddOn, ShopConfig, demo_config
from followup.platform.timeutil import parse
from followup.policy.framing import choose_premium, package_price
from followup.policy.offers import decide_close, percent_value_cents

CFG = demo_config()


def _queue(engine, shop=A):
    return [(c["quote_id"], c["tier"], c["strategy"], c["framing"], c["offer"])
            for c in engine.overview(shop, NOW)["candidates"]]


def _offer(engine, qid, shop=A):
    return by_quote(engine.overview(shop, NOW))[qid][1]["offer"]


def test_no_schedule_connected_changes_nothing(eng):
    assert _queue(eng) == _queue(demo_engine(capacity=NullCapacity()))
    assert eng.overview(A, NOW)["capacity"]["connected"] is False


@given(amount=st.integers(min_value=1, max_value=10_000_000), a=st.floats(0, 1), b=st.floats(0, 1))
def test_offer_never_falls_as_slack_rises_and_never_passes_the_cap(amount, a, b):
    low, high = sorted((a, b))
    v_low, _ = percent_value_cents(amount, low, CFG)
    v_high, p_high = percent_value_cents(amount, high, CFG)
    assert v_low <= v_high <= CFG.percent_cap_cents
    assert v_high <= amount * CFG.percent_max
    assert CFG.percent_base <= p_high <= CFG.percent_max


def test_a_light_week_makes_the_offer_larger_and_earlier(eng):
    # Carl, $18,000, one unanswered message. Normally a free framing at this touch.
    assert _offer(eng, "Q-1012") is None
    eng.set_capacity(A, booked_hours=40, median_booked_hours=160, available_hours=200)     # slack 0.75
    offer = _offer(eng, "Q-1012")
    assert offer["kind"] == "percent_off" and offer["value_cents"] == 100000               # capped at $1,000
    # $12,500 quote: 3 percent at a median week, 6 percent at an empty one, along the S curve, in $25 steps.
    assert [percent_value_cents(1250000, s, CFG)[0] for s in (0.0, 0.25, 0.5, 0.75, 1.0)] == [37500, 40000, 55000, 70000, 75000]
    # The $4,000 monthly budget runs out four offers in, so Ned further down the queue gets a free framing.
    costed = [c for c in eng.overview(A, NOW)["candidates"] if c["offer"] and c["offer"]["value_cents"]]
    assert len(costed) == 4 and _offer(eng, "Q-1022") is None


def test_a_fully_booked_shop_makes_no_costed_offer(eng):
    assert _offer(eng, "Q-1027")["value_cents"] == 52500              # Beth, $18,000, final touch: 3 percent, rounded down
    eng.set_capacity(A, booked_hours=200, median_booked_hours=160, available_hours=200)
    q = by_quote(eng.overview(A, NOW))["Q-1027"][1]
    assert not (q["offer"] and q["offer"]["value_cents"])
    assert q["requires_owner"] is False


def test_when_the_monthly_budget_is_spent_only_free_offers_remain(seed):
    quotes, events = seed
    engine = mini(quotes, events, demo_config().with_(monthly_offer_budget_cents=0))
    assert all(not (c["offer"] and c["offer"]["value_cents"]) for c in engine.overview("s1", NOW)["candidates"])


def test_the_budget_is_shared_across_one_run(seed):
    quotes, events = seed
    engine = mini(quotes, events, demo_config().with_(monthly_offer_budget_cents=80000))
    engine.set_capacity("s1", booked_hours=0, median_booked_hours=160, available_hours=200)
    costed = [c["offer"]["value_cents"] for c in engine.overview("s1", NOW)["candidates"]
              if c["offer"] and c["offer"]["value_cents"]]
    assert costed and sum(costed) <= 80000
    engine.run("s1", NOW)
    assert engine.overview("s1", NOW)["offers"]["spent_cents"] == sum(costed)


def test_one_costed_offer_per_quote():
    snap = CapacitySnapshot(booked_hours=0, median_booked_hours=100, available_hours=100)
    now = parse(NOW)
    first = decide_close(1800000, 2, "C-1", False, frozenset(), snap, 10**9, CFG, now)
    again = decide_close(1800000, 2, "C-1", True, frozenset(), snap, 10**9, CFG, now)
    assert first.offer.costed and not (again.offer and again.offer.costed)


def test_a_nearby_opening_ties_the_offer_to_the_slot(eng):
    carl = "C-19175552012"
    eng.set_capacity(A, booked_hours=160, median_booked_hours=160, available_hours=200, openings=[
        {"slot_id": "S-1", "tech_name": "Jaden", "label": "Thursday afternoon",
         "ends_at": "2026-08-20T20:00:00Z", "near_customer_ids": [carl]}])
    offer = _offer(eng, "Q-1012")
    assert offer["slot_ref"] == "S-1" and offer["value_cents"] > 0
    assert offer["expires_at"] == "2026-08-20T20:00:00Z"              # the offer ends when the slot does
    d = run_and_approve(eng)["Q-1012"]
    body = next(x for x in eng.overview(A, NOW)["drafts"] if x["id"] == d)["body"]
    assert "Thursday afternoon" in body and "in your area" in body
    # Someone else books the slot before the message goes out.
    eng.fill_slot(A, "S-1")
    out = eng.send(A, d, NOW)
    assert (out["result"], out["rule"]) == ("skip", "slot_filled")
    assert eng.sender.delivered == {}


def test_the_capacity_interface_carries_no_address_or_customer_name():
    assert set(Opening.__dataclass_fields__) == {"slot_id", "tech_name", "label", "ends_at", "near_customer_ids"}


def test_one_shops_schedule_is_not_visible_to_another(eng):
    eng.set_capacity(A, booked_hours=10, median_booked_hours=160, available_hours=200)
    assert eng.overview(A, NOW)["capacity"]["shop_slack"] > 0.9
    b = eng.overview(B, NOW)["capacity"]
    assert b["connected"] is False and b["shop_slack"] == 0.0


def test_schedule_service_down_leaves_the_policy_running(eng):
    normal = _queue(eng)
    eng.set_capacity(A, booked_hours=10, median_booked_hours=160, available_hours=200, fail=True)
    view = eng.overview(A, NOW)
    assert view["capacity"]["error"] is True
    assert _queue(eng) == normal
    assert len(eng.run(A, NOW)["created"]) == len(normal)


def test_slack_is_measured_against_the_shops_own_median():
    usual = CapacitySnapshot(booked_hours=80, median_booked_hours=80, available_hours=200)
    light = CapacitySnapshot(booked_hours=40, median_booked_hours=80, available_hours=200)
    assert usual.shop_slack == 0.0          # always half empty is not "light" for this shop
    assert light.shop_slack == 0.5


def test_capacity_pricing_can_be_switched_off(seed):
    quotes, events = seed
    engine = mini(quotes, events, demo_config().with_(capacity_pricing_enabled=False))
    before = _queue(engine, "s1")
    engine.set_capacity("s1", booked_hours=0, median_booked_hours=160, available_hours=200)
    assert _queue(engine, "s1") == before


# ----------------------------------------------------------------- premium option


def test_premium_option_price_is_quote_plus_the_catalogue_price(eng):
    q = by_quote(eng.overview(A, NOW))["Q-1012"][1]
    assert q["framing"]["kind"] == "premium_option" and q["offer"] is None
    n = q["framing"]["numbers_cents"]
    assert n == {"quote": 1800000, "addon": 650000, "package": 2450000}
    assert 1.2 <= n["package"] / n["quote"] <= 1.5


def test_a_bundle_saving_is_real_arithmetic():
    addon = AddOn("p", "plan", 400000, bundle_saving_cents=50000)
    assert package_price(1000000, addon) == 1350000


@pytest.mark.parametrize("amount,expected", [(1250000, "plan5"), (1800000, "upgrade"), (10000000, None),
                                             (100000, None)])
def test_premium_is_only_chosen_inside_the_anchoring_range(amount, expected):
    addon = choose_premium(amount, CFG)
    assert (addon.id if addon else None) == expected


def test_an_add_on_that_does_not_fit_the_job_is_not_offered():
    cfg = ShopConfig(addons=(AddOn("roof", "roof warranty", 400000, job_types=("roofing",)),))
    assert choose_premium(1000000, cfg, job_type="plumbing") is None
    assert choose_premium(1000000, cfg, job_type="roofing").id == "roof"


def test_a_writer_that_changes_the_package_price_is_rejected(eng):
    eng.run(A, NOW)
    d = next(x for x in eng.overview(A, NOW)["drafts"] if x["quote_id"] == "Q-1012")
    tampered = d["body"].replace("$24,500", "$19,000")
    result = eng.guard_check(A, tampered, draft_id=d["id"])
    assert result["ok"] is False and result["violations"][0]["rule"] == "money"
    assert eng.guard_check(A, d["body"], draft_id=d["id"])["ok"] is True


def test_the_same_premium_framing_is_not_repeated_on_a_quote(eng):
    d = run_and_approve(eng)["Q-1012"]
    eng.send(A, d, NOW)
    later = "2026-08-27T14:00:00Z"                                   # past the backoff
    q = by_quote(eng.overview(A, later))["Q-1012"]
    assert q[0] == "candidate" and q[1]["framing"]["kind"] != "premium_option"


def test_offer_follows_an_s_curve_in_slack():
    """Flat near a median week and near an empty week, steepest in the middle."""
    from followup.policy.offers import slack_curve
    pts = [slack_curve(i / 20, CFG) for i in range(21)]
    assert pts[0] == 0.0 and pts[-1] == 1.0
    assert all(a <= b for a, b in zip(pts, pts[1:]))
    steps = [b - a for a, b in zip(pts, pts[1:])]
    assert steps[9] > 3 * steps[0] and steps[10] > 3 * steps[-1]
    assert abs(slack_curve(0.5, CFG) - 0.5) < 1e-9
    assert slack_curve(0.3, CFG.with_(slack_curve_steepness=0)) == 0.3     # steepness 0 is a straight line
