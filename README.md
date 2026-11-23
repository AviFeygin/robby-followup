# Follow-up engine

Selects open quotes for follow-up, sets timing, drafts the message, and sends after human approval. Each approved draft is sent at most once.

Full design: [docs/DESIGN.md](docs/DESIGN.md). This file covers running it, the policy and its rationale, scaling to 50 shops, scope, and next steps.

Live build: https://afeygin.com/followup. The Python engine runs client-side under Pyodide; no install.

Agent transcript: [conversation/conversation.html](conversation/conversation.html) (messages, with tool calls folded) and [conversation/conversation.md](conversation/conversation.md) (plain text). Removed: the app's injected context about other projects and the account, model thinking, steps on unrelated work, and long tool output. No other edits.

## Running it

Python 3.10 or later.

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS or Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Candidate queue with reasons:

```bash
python -m followup.cli queue
```

Full flow, run twice, to show no duplicate sends:

```bash
python -m followup.cli demo
```

Web UI with live diagrams and the simulation, at http://127.0.0.1:8000:

```bash
python -m followup.cli serve
```

Tests:

```bash
pytest
```

Single-file website build. Runs the tests first and aborts on failure:

```bash
python tools/build_site.py
```

Default evaluation time: 2026-08-17 14:00 UTC, after the last seed event. Override with `--now` on the CLI or the shop clock in the UI.

### Optional components

- Postgres with row level security: `pip install "psycopg[binary]"`, set `DATABASE_URL` to a Postgres instance where roles can be created, and see the header of [tests/test_rls_postgres.py](tests/test_rls_postgres.py). The tests drop and recreate their tables; use a disposable database.
- Claude as the writer: `pip install anthropic`, set credentials and `FOLLOWUP_WRITER=model`. Without it, templates are used.

## Seed data findings

These constrained the design more than the brief.

- 88 event lines, 82 unique. Six ids appear twice.
- A `quote_sent` event type not listed in the brief.
- One customer (Karen Nguyen) with two open quotes on one phone number.
- Views after acceptance, and after dismissal.
- A quote accepted in `quotes.json` with no `quote_accepted` event.
- `last_contact_at` disagrees with `message_sent` events, in both directions.
- Three unanswered customer replies.
- Data ends in August. Evaluation time is a parameter throughout; wall-clock time would mark every quote stale.

## Follow-up policy

Filters first, then tiers. Within a tier, larger quotes rank first.

Quote-level filters: closed, under 24 hours old, live conversation with the tech, three unanswered attempts. Customer-level filters: contacted within 72 hours on any quote, or a draft pending review.

| Tier | Signal | Rationale |
|---|---|---|
| 1 | Unanswered customer reply | Customer is blocked on the shop |
| 2 | View since last contact, inside the hot window | Current intent |
| 3 | Large quote, inactive | Highest revenue at risk |
| 4 | Never contacted, past the shop's median time to first contact | Shop is behind its own baseline |
| 5 | No contact for longer than the median time to accept | Overdue |

One candidate per customer. Each candidate carries a reason code and a reason string.

### Tiers, not a score

A tier and its reason ("tier 2, 3 views yesterday, 3.5 times the median amount") can be audited by an owner. A scalar score cannot. Tiers are also the starting rule set for the learning system.

### Thresholds from shop medians

No fixed amounts or durations in the policy. Big quote: 2 times the shop's median amount. Hot window: one median view gap. First follow-up: past the shop's median time to first contact.

Seed values: median quote $5,200, so the big quote threshold is $10,400 (11 of 30 quotes). Minimum sample size is 15. Two metrics are below it (time to accept has one sample) and use configured defaults. The UI shows the source of each value.

The multipliers are configured values, shared across shops. The scale comes from each shop's data.

### Limits are fixed

Cooldown, attempt cap, quiet hours and offer caps are fixed per-shop values. A guardrail has to be stable and statable to a customer; a data-derived value drifts.

### Decisions

- A customer reply clears the cooldown. The cooldown limits unsolicited contact; a response to inbound contact is not unsolicited.
- Reply drafts are marked for a person to write. The seed has no reply text.
- Cooldown is per customer, not per quote.
- A quote is closed if either the quotes file or an event says so. Closed is terminal.
- `quote_sent` is the quote delivery. It is not a follow-up and does not start the cooldown.
- A view on a dismissed quote generates no message. It is surfaced to the owner as a manual lead.
- A rejected draft snoozes the quote for the cooldown, so it does not recur on the next run.

### Signals not in the seed

Reply text and intent, quote expiry, job type and urgency, line items and financing, view duration, delivery failures, repeat customers, and outcomes (which follow-ups led to acceptance).

## Guardrails

| Guardrail | Draft time | Send time | Schema |
|---|---|---|---|
| No second message inside the cooldown | Policy filter | Rechecked in the send transaction | Cooldown keyed on customer |
| No message about a closed quote | Policy filter | Rechecked in the send transaction | Closed is terminal |
| No duplicate send on rerun or retry | One live draft per customer | Conditional UPDATE claims the draft | One outbox row and one idempotency key per draft |

The send-time check is the binding one. A draft can wait for hours, during which the quote can be accepted or the tech can contact the customer.

A provider timeout leaves the draft in `sending`. No automatic retry from that state: the provider is queried first, since the message may have been delivered.

Sends append a `message_sent` event to the same log as the webhooks. Last contact has a single source, whether the tech or the engine sent the message.

## Production sending

Sending writes to an outbox behind a `Sender` interface with two methods: send with an idempotency key, and look up by that key. In production these map to the SMS provider's send API and a lookup by reference or delivery webhook. Delivery and reply webhooks enter through ingestion like any other event.

Not yet implemented for production: opt-out handling, carrier registration per shop number, and a time zone database in place of the fixed UTC offset used for quiet hours.

## Offers, anchoring and capacity

Outside the brief. Implemented as mechanisms with demo parameter values, since the seed has no margins, job types, add-ons, schedule or addresses.

- Offer values come only from the shop's catalogue, bounded by its cap and a monthly budget. Costed offers require owner approval.
- A framing changes the presentation of the price, not the price. Zero-cost framings are tried before costed offers.
- The premium option shows a larger package beside the quote. It must be a package the shop sells at that price.
- The capacity reader scales the discount by slack against the shop's median week, on an S curve, and by a tech's opening near the customer. An offer tied to an opening is refused at send if the slot is filled.

Demo values (3 to 6 percent off, at most $1,000 off per quote, $4,000 monthly budget, three add-ons) are assumptions, not Robby's figures.

## Writer and guard

The writer renders a decision as text. Input: first name, amount, reason, offer. No phone number, other customers or other shops. Only the returned text is read.

The guard is deterministic code. Every amount, percent and date in a message must be one the policy attached to the draft. Links, phone numbers, other customers' names and blocked promise terms are rejected. A rejected model draft falls back to the template. Approval stores a SHA-256 of the body; a send is refused if the body has changed.

## Scaling to 50 shops

Volume is not the constraint. Fifty shops with sixty open quotes each is three thousand quotes, well within one small database. The changes are tenancy and configuration. Each shop is a tenant whose data must not reach another, and each needs its own thresholds, cooldown, tone, quiet hours, sending number and offer limits, set as overrides on parent-company defaults. Thresholds here are already medians of each shop's own history, so a shop selling $800 repairs and one selling $20,000 installs get different thresholds without tuning, and a new shop uses the parent's medians until it has enough history. Isolation is enforced in the database as well as in application code, because application checks fail when a filter is omitted. Every table here is keyed by shop first, foreign keys include the shop so a row cannot reference another shop's row, and on Postgres row level security enforces the same rule for a role that cannot bypass it.

The open questions are above the shop level. A household can hold quotes from two sister shops, so the parent must decide whether cooldown and opt-out apply per household across the company. Proposed: opt-outs company-wide, cooldown per shop by default. Approval needs routing: an owner of one shop must not approve another shop's offers, and a costed offer should go to whoever owns the margin. Each shop sends from its own registered number, so a block at one shop does not affect the others. The parent needs cross-shop reporting, which means rollups it can read without access to customer rows. Fifty shops also provide enough data to train on, where one shop's sixty quotes do not: outcomes can be pooled to estimate which message or offer has an effect, provided only signals and outcomes are pooled and no names, numbers or message text leave a shop.

## Scope

Implemented and tested: ingestion, state and baselines, policy, template writer, guard, review, delivery, capacity interface, decision log, web UI, website build, and row level security on Postgres 18. With `DATABASE_URL` set, all 183 tests pass.

The Postgres tests found one defect on first run: the driver parsed `%I` in the RLS policy script as a parameter placeholder, so no policies were created. Fixed. SQLite could not have exposed it.

Implemented, not run against the real API: the model writer. Tested with a stub client only.

Designed only: learning system, reply classification, opt-out, production provider, production scheduling integration.

Known simplifications: quiet hours use a fixed UTC offset per shop. The capacity fixture has no travel time. Reply drafts are a holding line, since the seed has no reply text.

## Next steps

1. Run the full engine suite on Postgres in CI, not only the isolation tests, so the two databases cannot diverge.
2. Outcome tracking: join each draft to the next outcome (reply, acceptance, loss). Required to measure whether the policy has any effect.
3. Reply classification: question, price objection, deferral, stop. Enables routing and drafting for tier 1.
4. Opt-out as a hard guard filter, company-wide.
5. Production provider behind the sender interface, with delivery webhooks into ingestion.
6. A no-offer holdout group, to measure offer cost against deals that would have closed without one.

## Questions for Robby

1. Does `last_contact_at` include calls and visits? It disagrees with `message_sent`.
2. Should an unanswered customer reply get an automated draft, or go straight to the tech?
3. Is the cooldown per customer or per quote when one customer has two open quotes?
4. May the solution and the seed data be shown on a public page?

Questions 1 to 3 are resolved by the assumptions stated above.
