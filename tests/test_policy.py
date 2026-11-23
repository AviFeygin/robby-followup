"""Policy: tiers, filters, and the judgment calls in the design doc."""
from __future__ import annotations

from conftest import A, NOW, by_quote, event, mini, quote
from followup.platform.config import ShopConfig

T0 = "2026-08-01T00:00:00Z"


def test_seed_tiers_for_the_known_cases(eng):
    q = by_quote(eng.overview(A, NOW))
    # Customers who wrote to us and got no answer come first.
    for qid in ("Q-1016", "Q-1007", "Q-1015"):
        assert q[qid][0] == "candidate" and q[qid][1]["tier"] == 1 and q[qid][1]["strategy"] == "reply"
    # Viewed inside the hot window since our last contact.
    assert q["Q-1026"][1]["tier"] == 2 and q["Q-1000"][1]["tier"] == 2
    # $18,000, five views since one message ten days ago.
    assert q["Q-1012"][1]["tier"] == 3 and q["Q-1012"][1]["signals"]["views_since_contact"] == 5
    # Answered recently, still inside the backoff.
    for qid in ("Q-1002", "Q-1019", "Q-1020"):
        assert q[qid] == ("excluded", q[qid][1]) and q[qid][1]["why"] == "backoff"


def test_one_candidate_per_customer_across_their_quotes(eng):
    q = by_quote(eng.overview(A, NOW))
    assert q["Q-1025"][0] == "candidate"
    assert q["Q-1003"][0] == "excluded" and q["Q-1003"][1]["why"] == "other_quote_chosen"


def test_closed_quotes_are_never_candidates(eng):
    view = eng.overview(A, NOW)
    closed = {q["id"] for q in view["quotes"] if q["status"] != "open"}
    assert closed == {"Q-1004", "Q-1009", "Q-1017"}
    assert not closed & {c["quote_id"] for c in view["candidates"]}


def test_every_candidate_explains_itself(eng):
    for c in eng.overview(A, NOW)["candidates"]:
        assert c["reason"] and c["reason_code"] and c["path"][0][0] == "B" and c["path"][-1] == ["M", "chosen"]


def test_ranking_is_tier_first_then_size(eng):
    cands = eng.overview(A, NOW)["candidates"]
    assert [c["tier"] for c in cands] == sorted(c["tier"] for c in cands)
    tier1 = [c["amount_cents"] for c in cands if c["tier"] == 1]
    assert tier1 == sorted(tier1, reverse=True)


def test_cooldown_blocks_a_recently_contacted_customer():
    engine = mini([quote("Q-1", "+19175550001", 20000, T0)],
                  [event("m1", "message_sent", "Q-1", "2026-08-17T04:00:00Z", direction="outbound"),
                   event("v1", "quote_viewed", "Q-1", "2026-08-17T10:00:00Z")])
    kind, item = by_quote(engine.overview("s1", NOW))["Q-1"]
    assert kind == "excluded" and item["why"] == "cooldown"     # a hot view does not beat the cooldown


def test_cooldown_is_per_customer_not_per_quote():
    phone = "+19175550001"
    engine = mini([quote("Q-1", phone, 900, T0), quote("Q-2", phone, 20000, T0)],
                  [event("m1", "message_sent", "Q-1", "2026-08-16T20:00:00Z", direction="outbound")])
    q = by_quote(engine.overview("s1", NOW))
    assert q["Q-2"][0] == "excluded" and q["Q-2"][1]["why"] == "cooldown"


def test_a_customer_reply_clears_the_cooldown():
    engine = mini([quote("Q-1", "+19175550001", 900, T0)],
                  [event("m1", "message_sent", "Q-1", "2026-08-17T04:00:00Z", direction="outbound"),
                   event("r1", "customer_replied", "Q-1", "2026-08-17T09:00:00Z", channel="sms")])
    kind, item = by_quote(engine.overview("s1", NOW))["Q-1"]
    assert kind == "candidate" and item["tier"] == 1


def test_fresh_quotes_are_left_alone():
    engine = mini([quote("Q-1", "+19175550001", 900, "2026-08-17T00:00:00Z")],
                  [event("v1", "quote_viewed", "Q-1", "2026-08-17T06:00:00Z")])
    kind, item = by_quote(engine.overview("s1", NOW))["Q-1"]
    assert kind == "excluded" and item["why"] == "hold_fresh"


def test_live_conversation_is_left_to_the_tech():
    engine = mini([quote("Q-1", "+19175550001", 900, T0)],
                  [event("r1", "customer_replied", "Q-1", "2026-08-13T09:00:00Z"),
                   event("m1", "message_sent", "Q-1", "2026-08-16T04:00:00Z", direction="outbound")])
    kind, item = by_quote(engine.overview("s1", NOW))["Q-1"]
    assert kind == "excluded" and item["why"] == "hold_live_conversation"


def test_three_unanswered_messages_and_we_stop():
    sends = [event(f"m{i}", "message_sent", "Q-1", f"2026-08-0{i}T00:00:00Z", direction="outbound") for i in (2, 4, 6)]
    engine = mini([quote("Q-1", "+19175550001", 900, T0)], sends)
    kind, item = by_quote(engine.overview("s1", NOW))["Q-1"]
    assert kind == "excluded" and item["why"] == "gone_quiet"


def test_backoff_grows_with_each_unanswered_attempt():
    # One attempt 4 days ago: 96 hours served, 72 + 48 = 120 needed. Not yet.
    engine = mini([quote("Q-1", "+19175550001", 900, T0)],
                  [event("m1", "message_sent", "Q-1", "2026-08-13T14:00:00Z", direction="outbound")])
    assert by_quote(engine.overview("s1", NOW))["Q-1"][1]["why"] == "backoff"
    # A day and a half later it has been served, and the quote is stale.
    later = by_quote(engine.overview("s1", "2026-08-21T14:00:00Z"))["Q-1"]
    assert later[0] == "candidate" and later[1]["tier"] == 5


def test_strategy_ladder_by_attempt():
    cfg = ShopConfig(shop_name="Test Shop")
    never_viewed = mini([quote("Q-1", "+19175550001", 900, T0)], [], cfg)
    assert by_quote(never_viewed.overview("s1", NOW))["Q-1"][1]["strategy"] == "check_delivery"

    viewed = mini([quote("Q-1", "+19175550001", 900, T0)],
                  [event("v1", "quote_viewed", "Q-1", "2026-08-02T00:00:00Z")], cfg)
    assert by_quote(viewed.overview("s1", NOW))["Q-1"][1]["strategy"] == "nudge"

    second = mini([quote("Q-1", "+19175550001", 900, T0)],
                  [event("v1", "quote_viewed", "Q-1", "2026-08-02T00:00:00Z"),
                   event("m1", "message_sent", "Q-1", "2026-08-03T00:00:00Z", direction="outbound")], cfg)
    assert by_quote(second.overview("s1", NOW))["Q-1"][1]["strategy"] == "help"
