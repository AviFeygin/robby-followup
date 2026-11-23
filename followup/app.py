"""Local server. Exposes api.py over HTTP and serves the page.

This is the only file that imports FastAPI, so the engine itself runs anywhere
Python and SQLAlchemy do, including in a browser.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import Body, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import call
from .bootstrap import demo_engine

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

app = FastAPI(title="Follow-up engine")
engine = demo_engine(os.environ.get("DATABASE_URL"))


@app.post("/api/reset")
def reset() -> JSONResponse:
    global engine
    engine = demo_engine(os.environ.get("DATABASE_URL"))
    return JSONResponse({"ok": True, "data": {"reset": True}})


@app.post("/api/{action}")
def api(action: str, kwargs: dict = Body(default_factory=dict)) -> JSONResponse:
    return JSONResponse(call(engine, action, kwargs))


@app.get("/design.md")
def design() -> FileResponse:
    return FileResponse(ROOT / "docs" / "DESIGN.md", media_type="text/markdown")


@app.get("/site")
def site() -> FileResponse:
    """The built website file, for checking the in-browser engine locally."""
    return FileResponse(ROOT / "dist" / "followup.html", media_type="text/html")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


if WEB.exists():
    app.mount("/web", StaticFiles(directory=WEB), name="web")
