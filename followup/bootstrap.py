"""Builds a ready Engine from the seed folder. Shared by the CLI, the server and the tests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .api import Engine

SEED_DIR = Path(__file__).resolve().parents[1] / "seed"


def read_seed(seed_dir: Path | str = SEED_DIR) -> tuple[list[dict], list[Any]]:
    seed_dir = Path(seed_dir)
    quotes = json.loads((seed_dir / "quotes.json").read_text(encoding="utf-8"))
    events: list[Any] = []
    for line in (seed_dir / "events.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append(None)       # kept, so ingestion reports it as malformed
    return quotes, events


def demo_engine(db_url: str | None = None, seed_dir: Path | str = SEED_DIR, **kwargs) -> Engine:
    if "writer" not in kwargs:
        from .agents.model import model_writer_from_env

        writer = model_writer_from_env()      # None unless FOLLOWUP_WRITER=model is set
        if writer is not None:
            kwargs["writer"] = writer
    engine = Engine(db_url, **kwargs)
    quotes, events = read_seed(seed_dir)
    engine.load_demo(quotes, events)
    return engine
