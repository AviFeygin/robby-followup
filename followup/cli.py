"""Command line.

    python -m followup.cli queue            who to follow up with, and why
    python -m followup.cli demo             run, approve, send, then run and send again
    python -m followup.cli serve            the page on http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import sys

from .api import DEFAULT_NOW
from .bootstrap import demo_engine


def _money(cents: int) -> str:
    return f"${cents / 100:,.0f}"


def cmd_queue(args) -> None:
    engine = demo_engine(args.db)
    view = engine.overview(args.shop, args.now)
    print(f"{view['shop_name']} at {view['now']}: {view['counts']['open']} open quotes, "
          f"{len(view['candidates'])} to follow up\n")
    print("Baselines")
    for b in view["baselines"]:
        unit = "cents" if b["metric"] == "amount" else "hours"
        print(f"  {b['metric']:<15} {b['value']:>10.1f} {unit:<6} n={b['sample_n']:<3} from {b['source']}")
    print("\nQueue")
    for c in view["candidates"]:
        offer = f" | offer {_money(c['offer']['value_cents'])}" if c["offer"] and c["offer"]["value_cents"] else ""
        framing = f" | {c['framing']['kind']}" if c["framing"] else ""
        print(f"  {c['rank']:>2}. tier {c['tier']} {c['quote_id']} {c['customer_name']:<16} "
              f"{_money(c['amount_cents']):>8}  {c['strategy']}{framing}{offer}")
        print(f"      {c['reason']}")
    print("\nNot contacted")
    counts: dict[str, int] = {}
    for e in view["excluded"]:
        counts[e["why"]] = counts.get(e["why"], 0) + 1
    for why, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>2}  {why}")


def cmd_demo(args) -> None:
    engine = demo_engine(args.db)
    first = engine.run(args.shop, args.now)
    print(f"Run 1 created {len(first['created'])} drafts")
    for item in first["created"]:
        engine.approve(args.shop, item["draft_id"], "user:owner", "owner", args.now)
    results = [engine.send(args.shop, item["draft_id"], args.now)["result"] for item in first["created"]]
    print("Send 1:", {r: results.count(r) for r in sorted(set(results))})
    again = [engine.send(args.shop, item["draft_id"], args.now)["result"] for item in first["created"]]
    print("Send 2:", {r: again.count(r) for r in sorted(set(again))})
    second = engine.run(args.shop, args.now)
    print(f"Run 2 created {len(second['created'])} drafts")
    view = engine.overview(args.shop, args.now)
    sent = [d for d in view["drafts"] if d["status"] == "sent"]
    print(f"\nOutbox: {len(engine.sender.delivered)} messages delivered, {len(sent)} drafts sent\n")
    for d in sent:
        print(f"  {d['quote_id']} [{d['strategy']}] {d['body']}")


def cmd_serve(args) -> None:
    import uvicorn

    uvicorn.run("followup.app:app", host="127.0.0.1", port=args.port, log_level="info")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="followup")
    parser.add_argument("--db", default=None, help="database URL, default is SQLite in memory")
    parser.add_argument("--shop", default="shop-a")
    parser.add_argument("--now", default=DEFAULT_NOW, help="the time to evaluate at, ISO format")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("queue").set_defaults(fn=cmd_queue)
    sub.add_parser("demo").set_defaults(fn=cmd_demo)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(fn=cmd_serve)
    args = parser.parse_args(argv)
    args.fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
