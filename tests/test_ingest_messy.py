"""A. Messy input: duplicates, out of order events, unknown types, bad lines."""
from __future__ import annotations

import random

from hypothesis import given, settings
from hypothesis import strategies as st

from conftest import A, NOW, by_quote, event, mini, quote
from followup.api import Engine
from followup.platform.config import demo_config


def _load(quotes, events) -> Engine:
    engine = Engine()
    engine.create_shop(A, "Robby Home Services", demo_config())
    engine.load_seed(A, quotes, events)
    return engine


def _queue(engine: Engine):
    return [(c["quote_id"], c["tier"], c["strategy"]) for c in engine.overview(A, NOW)["candidates"]]


def test_seed_duplicates_are_stored_once(seed):
    quotes, events = seed
    engine = Engine()
    engine.create_shop(A, "x", demo_config())
    report = engine.load_seed(A, quotes, events)["events"]
    assert report["received"] == 88
    assert report["stored"] == 82
    assert report["duplicates"] == 6
    assert report["unknown_types"] == []      # quote_sent is known and simply not a follow-up


@settings(max_examples=25, deadline=None)
@given(order=st.randoms(use_true_random=False), copies=st.integers(min_value=0, max_value=60))
def test_shuffled_and_duplicated_events_give_the_same_result(order, copies):
    from followup.bootstrap import read_seed
    quotes, events = read_seed()
    baseline = _load(quotes, events)

    messy = list(events) + [order.choice(events) for _ in range(copies)]
    order.shuffle(messy)
    other = _load(quotes, messy)

    assert other.state_hash(A, NOW)["hash"] == baseline.state_hash(A, NOW)["hash"]
    assert _queue(other) == _queue(baseline)


def test_ingesting_the_same_file_twice_changes_nothing(seed):
    quotes, events = seed
    engine = _load(quotes, events)
    before = engine.state_hash(A, NOW)
    report = engine.ingest(A, events)
    assert report["stored"] == 0 and report["duplicates"] == 88
    assert engine.state_hash(A, NOW) == before


def test_views_after_acceptance_do_not_reopen_a_quote(eng):
    detail = eng.quote_detail(A, "Q-1004", NOW)
    viewed_after = [t for t in detail["timeline"] if t["type"] == "quote_viewed" and t["at"] > "2026-08-10T07:00:00Z"]
    assert len(viewed_after) == 2                       # the seed really does contain them
    assert detail["quote"]["status"] == "accepted"
    assert detail["candidate"] is None
    assert detail["excluded"]["why"] == "closed"


def test_accepted_in_quotes_file_without_an_event_is_closed(eng):
    detail = eng.quote_detail(A, "Q-1017", NOW)
    assert not [t for t in detail["timeline"] if t["type"] == "quote_accepted"]
    assert detail["excluded"]["why"] == "closed"


def test_dismissed_quote_viewed_again_is_a_manual_lead_not_a_message(eng):
    # Q-1009 was dismissed and then viewed on 08-15. Shortly after, it is flagged for the owner.
    view = eng.overview(A, "2026-08-15T12:00:00Z")
    kind, item = by_quote(view)["Q-1009"]
    assert kind == "excluded" and item["why"] == "closed" and item["manual_lead"] is True


def test_closed_quotes_never_reopen_on_reload(eng, seed):
    quotes, _ = seed
    reopened = [dict(q, status="open") for q in quotes]
    eng.load_seed(A, reopened, [])
    assert eng.quote_detail(A, "Q-1004", NOW)["quote"]["status"] == "accepted"
    assert eng.quote_detail(A, "Q-1009", NOW)["quote"]["status"] == "dismissed"


def test_last_contact_uses_events_when_the_field_is_null(eng):
    # Q-1012 has last_contact_at null but an outbound message on 08-07.
    assert eng.quote_detail(A, "Q-1012", NOW)["quote"]["last_contact"] == "2026-08-07T01:00:00Z"
    # Q-1020 has a field value later than any event. The later one wins.
    assert eng.quote_detail(A, "Q-1020", NOW)["quote"]["last_contact"] == "2026-08-13T17:00:00Z"


def test_quote_sent_is_not_a_follow_up(eng):
    assert eng.quote_detail(A, "Q-1010", NOW)["signals"]["never_contacted"] is True


def test_events_later_than_now_are_ignored(eng):
    early = eng.overview(A, "2026-08-12T00:00:00Z")
    q = next(q for q in early["quotes"] if q["id"] == "Q-1026")
    assert q["views"] == []                              # its only view is on 08-16
    assert all(v <= "2026-08-12T00:00:00Z" for quote_ in early["quotes"] for v in quote_["views"])


def test_bad_lines_unknown_types_and_unknown_quotes_do_not_stop_the_load():
    quotes = [quote("Q-1", "+19175550001", 1000, "2026-08-01T00:00:00Z")]
    events = [
        event("e1", "quote_viewed", "Q-1", "2026-08-02T00:00:00Z"),
        None,                                                        # a line that was not JSON
        {"type": "quote_viewed", "quote_id": "Q-1"},                 # no id, no time
        event("e2", "quote_viewed", "Q-1", "not a time"),
        event("e3", "something_new", "Q-1", "2026-08-02T01:00:00Z"),
        event("e4", "quote_viewed", "Q-404", "2026-08-02T02:00:00Z"),
        event("e5", "quote_viewed", "Q-1", "2026-08-03T00:00:00+02:00"),   # offset time, normalised to UTC
    ]
    engine = mini([], [])
    report = engine.load_seed("s1", quotes, events)["events"]
    assert report["stored"] == 4
    assert [m["line"] for m in report["malformed"]] == [2, 3, 4]
    assert report["unknown_types"] == ["something_new"]
    assert report["unknown_quotes"] == ["Q-404"]
    q = engine.overview("s1", "2026-08-10T00:00:00Z")["quotes"][0]
    assert q["views"] == ["2026-08-02T00:00:00Z", "2026-08-02T22:00:00Z"]


def test_messy_check_from_the_api_agrees_for_several_shuffles(eng):
    for seed_ in range(5):
        result = eng.messy_check(A, shuffle_seed=seed_, duplicate_fraction=0.5, now=NOW)
        assert result["same"], result
        assert result["duplicates_ignored"] == result["sent"] - result["stored"]


def test_a_quote_added_later_joins_the_queue_and_a_dismissal_sticks():
    """The live simulation feeds quotes in one at a time through add_quotes."""
    engine = mini([quote("Q-1", "+19175550001", 900, "2026-08-01T00:00:00Z")], [])
    new = quote("Q-2", "+19175550002", 4000, "2026-08-14T00:00:00Z", name="Nia Brooks")
    assert engine.add_quotes("s1", [new]) == {"quotes": 1}
    engine.ingest("s1", [event("v2", "quote_viewed", "Q-2", "2026-08-16T12:00:00Z")])
    assert "Q-2" in {c["quote_id"] for c in engine.overview("s1", NOW)["candidates"]}
    engine.add_quotes("s1", [dict(new, status="dismissed")])
    engine.add_quotes("s1", [dict(new, status="open")])            # a stale feed cannot reopen it
    states = {q["id"]: q["status"] for q in engine.overview("s1", NOW)["quotes"]}
    assert states["Q-2"] == "dismissed"
    assert "Q-2" not in {c["quote_id"] for c in engine.overview("s1", NOW)["candidates"]}
