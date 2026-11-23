"""C. Send guardrails: cooldown, closed quotes, and never sending twice."""
from __future__ import annotations

import threading

import pytest

from conftest import A, NOW, by_quote, event, mini, quote, run_and_approve
from followup.api import call
from followup.bootstrap import demo_engine
from followup.platform.config import demo_config
from followup.review import WorkflowError

LATER = "2026-08-17T15:00:00Z"


def _status(engine, draft_id, shop=A):
    return next(d for d in engine.overview(shop, NOW)["drafts"] if d["id"] == draft_id)


def _outbox(engine, shop=A):
    return engine.session(shop).rows("SELECT * FROM outbox WHERE shop_id = :shop_id")


# ---------------------------------------------------------------- rerun and retry


def test_running_twice_and_sending_twice_sends_once(eng):
    drafts = run_and_approve(eng)
    assert eng.run(A, NOW)["created"] == []                        # second run creates nothing
    first = [eng.send(A, d, NOW)["result"] for d in drafts.values()]
    second = [eng.send(A, d, NOW)["result"] for d in drafts.values()]
    assert set(first) == {"sent"} and set(second) == {"noop"}
    assert len(_outbox(eng)) == len(drafts) == len(eng.sender.delivered)
    assert eng.run(A, LATER)["created"] == []                      # everyone is now in cooldown


def test_engine_called_100_times_creates_no_extra_drafts(eng):
    first = len(eng.run(A, NOW)["created"])
    for _ in range(100):
        assert eng.run(A, NOW)["created"] == []
    assert len(eng.overview(A, NOW)["drafts"]) == first


def test_two_open_quotes_on_one_phone_get_one_draft(eng):
    eng.run(A, NOW)
    karen = [d for d in eng.overview(A, NOW)["drafts"] if d["quote_id"] in ("Q-1003", "Q-1025")]
    assert len(karen) == 1


def test_a_failed_send_retried_keeps_the_key_and_sends_once(eng):
    drafts = run_and_approve(eng)
    d = drafts["Q-1012"]
    eng.script_sender(["reject"])
    assert eng.send(A, d, NOW)["result"] == "failed"
    assert _status(eng, d)["status"] == "failed"
    assert eng.send(A, d, NOW)["result"] == "sent"
    rows = [r for r in _outbox(eng) if r["draft_id"] == d]
    assert len(rows) == 1 and rows[0]["attempts"] == 2 and rows[0]["idempotency_key"] == f"{A}:{d}"
    assert list(eng.sender.delivered) == [f"{A}:{d}"]


def test_a_crash_after_the_claim_never_resends_without_reconcile(eng):
    drafts = run_and_approve(eng)
    d = drafts["Q-1012"]
    eng.script_sender(["crash"])
    with pytest.raises(RuntimeError):
        eng.send(A, d, NOW)
    assert _status(eng, d)["status"] == "sending"
    assert eng.send(A, d, NOW)["result"] == "noop"                 # a blind retry does nothing
    assert eng.sender.delivered == {}
    assert eng.reconcile(A, d, NOW)["result"] == "failed"          # provider has no record, so it is safe
    assert eng.send(A, d, NOW)["result"] == "sent"
    assert len(eng.sender.delivered) == 1


def test_a_timeout_that_did_deliver_is_settled_by_reconcile_not_resent(eng):
    drafts = run_and_approve(eng)
    d = drafts["Q-1012"]
    eng.script_sender(["timeout_but_delivered"])
    assert eng.send(A, d, NOW)["result"] == "unknown"
    assert _status(eng, d)["status"] == "sending"
    assert eng.send(A, d, NOW)["result"] == "noop"
    assert eng.reconcile(A, d, NOW)["result"] == "sent"
    assert len(eng.sender.delivered) == 1 and _status(eng, d)["status"] == "sent"


def test_two_threads_sending_the_same_draft_send_once(tmp_path):
    eng = demo_engine(f"sqlite:///{(tmp_path / 'race.db').as_posix()}")
    d = run_and_approve(eng)["Q-1012"]
    barrier, results = threading.Barrier(8), []

    def worker():
        barrier.wait()
        results.append(eng.send(A, d, NOW)["result"])

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["noop"] * 7 + ["sent"]
    assert eng.sender.calls == 1 and len(_outbox(eng)) == 1


# -------------------------------------------------------------- closed and cooldown


def test_quote_accepted_after_approval_is_skipped_at_send(eng):
    d = run_and_approve(eng)["Q-1012"]
    eng.accept_quote(A, "Q-1012", NOW)
    out = eng.send(A, d, LATER)
    assert (out["result"], out["rule"]) == ("skip", "closed_quote")
    assert _status(eng, d)["status"] == "skipped" and eng.sender.delivered == {}


def test_a_draft_for_a_quote_that_closed_is_expired_on_the_next_run(eng):
    d = run_and_approve(eng)["Q-1012"]
    eng.accept_quote(A, "Q-1012", NOW)
    assert d in eng.run(A, LATER)["expired"]
    assert _status(eng, d)["status"] == "expired"
    assert eng.send(A, d, LATER)["result"] == "noop"


def test_cooldown_from_the_customers_other_quote_blocks_the_send(eng):
    d = run_and_approve(eng)["Q-1025"]                              # Karen's second quote
    # The tech texts her about her first quote while the draft waits.
    eng.ingest(A, [event("tech-1", "message_sent", "Q-1003", "2026-08-17T14:30:00Z", direction="outbound")])
    out = eng.send(A, d, LATER)
    assert (out["result"], out["rule"]) == ("skip", "cooldown")
    assert eng.sender.delivered == {}


def test_our_own_send_starts_the_cooldown_through_the_event_log(eng):
    d = run_and_approve(eng)["Q-1025"]
    assert eng.send(A, d, NOW)["result"] == "sent"
    assert eng.quote_detail(A, "Q-1025", LATER)["quote"]["last_contact"] == NOW
    q = by_quote(eng.overview(A, LATER))
    assert q["Q-1025"][1]["why"] == "cooldown" and q["Q-1003"][1]["why"] == "cooldown"


def test_send_inside_cooldown_is_refused_even_if_a_draft_was_approved_earlier():
    engine = mini([quote("Q-1", "+19175550001", 900, "2026-08-01T00:00:00Z")], [])
    d = run_and_approve(engine, "s1")["Q-1"]
    engine.ingest("s1", [event("m1", "message_sent", "Q-1", "2026-08-17T14:10:00Z", direction="outbound")])
    assert engine.send("s1", d, LATER)["rule"] == "cooldown"
    # After the window it would be allowed, but this draft is finished. The engine decides afresh.
    assert _status(engine, d, "s1")["status"] == "skipped"


# ---------------------------------------------------------------------- approval


def test_a_draft_that_was_never_approved_cannot_be_sent(eng):
    d = eng.run(A, NOW)["created"][0]["draft_id"]
    assert eng.send(A, d, NOW)["result"] == "noop"
    assert eng.sender.delivered == {}


def test_editing_after_approval_clears_the_approval(eng):
    d = run_and_approve(eng)["Q-1000"]
    edited = eng.edit(A, d, "Hi Mike, it's Brad. Still keen on the $850 job?", "user:owner", NOW)
    assert edited["status"] == "draft" and edited["approved_body_hash"] is None
    assert eng.send(A, d, NOW)["result"] == "noop"


def test_text_changed_behind_the_workflow_is_refused_by_the_hash(eng):
    d = run_and_approve(eng)["Q-1000"]
    eng.session(A).run("UPDATE drafts SET body = 'Hi Mike, 90% off today only' WHERE shop_id = :shop_id AND id = :id",
                       id=d)
    out = eng.send(A, d, NOW)
    assert (out["result"], out["rule"]) == ("refuse", "hash_mismatch")
    assert _status(eng, d)["status"] == "draft" and eng.sender.delivered == {}


def test_only_a_person_can_approve(eng):
    d = eng.run(A, NOW)["created"][0]["draft_id"]
    for actor in ("agent:writer", "system", ""):
        with pytest.raises(WorkflowError) as err:
            eng.approve(A, d, actor, "owner", NOW)
        assert err.value.code == "not_human"
    assert call(eng, "approve", {"shop_id": A, "draft_id": d, "actor": "agent:x"})["error"]["code"] == "not_human"


def test_a_costed_offer_needs_the_owner(eng):
    created = {c["quote_id"]: c["draft_id"] for c in eng.run(A, NOW)["created"]}
    beth = created["Q-1027"]                                        # carries a $500 offer
    assert _status(eng, beth)["requires_owner"] is True
    with pytest.raises(WorkflowError) as err:
        eng.approve(A, beth, "user:brad", "tech", NOW)
    assert err.value.code == "owner_required"
    assert eng.approve(A, beth, "user:owner", "owner", NOW)["status"] == "approved"


def test_a_rejected_draft_snoozes_the_quote(eng):
    created = {c["quote_id"]: c["draft_id"] for c in eng.run(A, NOW)["created"]}
    eng.reject(A, created["Q-1000"], "user:owner", NOW)
    assert "Q-1000" not in {c["quote_id"] for c in eng.run(A, LATER)["created"]}
    assert by_quote(eng.overview(A, LATER))["Q-1000"][1]["why"] == "snoozed"
    after = eng.run(A, "2026-08-20T15:00:00Z")["created"]          # past the 72 hour snooze
    assert "Q-1000" in {c["quote_id"] for c in after}


# ------------------------------------------------------------------------ limits


def test_offer_that_expired_before_sending_is_not_sent(eng):
    d = run_and_approve(eng)["Q-1027"]                              # offer good until Aug 24
    out = eng.send(A, d, "2026-08-25T15:00:00Z")
    assert (out["result"], out["rule"]) == ("skip", "offer_expired")


def test_kill_switch_stops_approved_drafts_and_releases_them(eng):
    d = run_and_approve(eng)["Q-1000"]
    eng.set_sending(A, False)
    assert eng.send(A, d, NOW)["rule"] == "kill_switch"
    assert _status(eng, d)["status"] == "approved" and eng.sender.delivered == {}
    eng.set_sending(A, True)
    eng.set_global_sending(False)
    assert eng.send(A, d, NOW)["rule"] == "kill_switch"
    eng.set_global_sending(True)
    assert eng.send(A, d, NOW)["result"] == "sent"


def test_daily_send_cap_defers_the_rest(seed):
    quotes, events = seed
    engine = mini(quotes, events, demo_config().with_(daily_send_cap=2))
    drafts = list(run_and_approve(engine, "s1").values())
    results = [engine.send("s1", d, NOW) for d in drafts]
    assert [r["result"] for r in results].count("sent") == 2
    assert {r.get("rule") for r in results[2:]} == {"daily_cap"}
    assert _status(engine, drafts[2], "s1")["status"] == "approved"     # still approved, sends tomorrow


def test_quiet_hours_defer_a_send(eng):
    d = run_and_approve(eng)["Q-1000"]
    night = "2026-08-18T03:00:00Z"                                  # 23:00 shop time
    out = eng.send(A, d, night)
    assert (out["result"], out["rule"]) == ("defer", "quiet_hours")
    assert eng.send(A, d, "2026-08-18T14:00:00Z")["result"] == "sent"


def test_recipient_comes_from_the_database(eng):
    d = run_and_approve(eng)["Q-1000"]
    eng.send(A, d, NOW)
    assert _outbox(eng)[0]["to_phone"] == "+19175552000"
    assert eng.sender.delivered[f"{A}:{d}"].to_phone == "+19175552000"
