"""Build the website version: one self-contained HTML file.

    python tools/build_site.py

It runs the tests first and refuses to build if any fail. The output,
dist/followup.html, holds the page, the engine source, the seed, the design document
and the test report. In a browser it runs the same Python packages through Pyodide.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
sys.path.insert(0, str(ROOT))

TITLES = {
    "test_ingest_messy": "A. Messy input",
    "test_baselines": "B. Baselines and thresholds",
    "test_policy": "Policy tiers and filters",
    "test_send_guardrails": "C. Send guardrails",
    "test_capacity_offers": "Capacity and anchoring",
    "test_tenant_isolation": "D. Tenant isolation and data leakage",
    "test_rls_postgres": "D. Postgres row level security",
    "test_agent_containment": "E. Agent containment",
    "test_invariants": "F. Invariants under random sequences",
    "test_site_build": "Website build",
}

# Nothing from this machine or its secrets may end up in a public file.
FORBIDDEN = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"),
    re.compile(r"ANTHROPIC_API_KEY\s*=\s*\S+"),
    re.compile(r"[A-Za-z]:\\\\?Users\\\\?[A-Za-z]"),
    re.compile(r"/(?:home|Users)/[a-z][a-z0-9_-]+/", re.I),
    re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^\s\"']+:[^\s\"'@]+@(?!localhost)"),
]


def run_tests() -> dict:
    """Run pytest, return a report. Raises SystemExit if any test fails."""
    with tempfile.TemporaryDirectory() as tmp:
        xml_path = Path(tmp) / "junit.xml"
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests", "-q", "--no-header", "-p", "no:cacheprovider",
             "--deselect", "tests/test_site_build.py::test_the_real_build_is_clean", f"--junitxml={xml_path}"],
            cwd=ROOT, capture_output=True, text=True,
        )
        report = parse_junit(xml_path.read_text(encoding="utf-8")) if xml_path.exists() else None
    if proc.returncode != 0 or report is None or report["failed"]:
        sys.stderr.write(proc.stdout[-3000:] + proc.stderr[-2000:])
        raise SystemExit("Tests failed. The site was not built.")
    return report


def parse_junit(xml_text: str) -> dict:
    files: dict[str, dict] = {}
    totals = {"passed": 0, "failed": 0, "skipped": 0}
    for case in ET.fromstring(xml_text).iter("testcase"):
        module = case.get("classname", "").split(".")[-1]
        outcome = "passed"
        if case.find("failure") is not None or case.find("error") is not None:
            outcome = "failed"
        elif case.find("skipped") is not None:
            outcome = "skipped"
        entry = files.setdefault(module, {"file": f"tests/{module}.py", "title": TITLES.get(module, module),
                                          "passed": 0, "failed": 0, "skipped": 0, "tests": []})
        entry[outcome] += 1
        totals[outcome] += 1
        name = case.get("name", "").replace("test_", "", 1).replace("_", " ")
        entry["tests"].append({"name": name, "outcome": outcome})
    order = list(TITLES)
    ordered = sorted(files.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99)
    return {**totals, "total": sum(totals.values()), "files": [v for _, v in ordered]}


def engine_files() -> dict[str, str]:
    """The files the browser needs to run the engine. app.py is left out: it needs FastAPI."""
    files: dict[str, str] = {}
    for path in sorted((ROOT / "followup").rglob("*.py")):
        if path.name == "app.py" or "__pycache__" in path.parts:
            continue
        files[path.relative_to(ROOT).as_posix()] = path.read_text(encoding="utf-8")
    files["db/schema.sql"] = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
    for name in ("quotes.json", "events.jsonl"):
        files[f"seed/{name}"] = (ROOT / "seed" / name).read_text(encoding="utf-8")
    return files


def recorded_run() -> dict:
    """What the page shows if Python cannot load in the visitor's browser."""
    from followup.api import DEFAULT_NOW
    from followup.bootstrap import demo_engine

    engine = demo_engine("sqlite://")          # never DATABASE_URL: the record must match what the browser runs
    return {
        "shops": engine.shops(),
        "hostile_cases": engine.hostile_cases(),
        "overview:shop-a": engine.overview("shop-a", DEFAULT_NOW),
        "overview:shop-b": engine.overview("shop-b", DEFAULT_NOW),
    }


def check_clean(html: str) -> None:
    for pattern in FORBIDDEN:
        found = pattern.search(html)
        if found:
            raise SystemExit(f"Refusing to build: output contains {found.group(0)[:40]!r}")


def assemble(report: dict) -> str:
    page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    bundle = {
        "files": engine_files(),
        "design": (ROOT / "docs" / "DESIGN.md").read_text(encoding="utf-8"),
        "tests": report,
        "recorded": recorded_run(),
    }
    # "</" must not appear inside an inline script.
    data = json.dumps(bundle, ensure_ascii=False).replace("</", "<\\/")
    scripts = ["<script>window.FOLLOWUP_BUNDLE = " + data + ";</script>"]
    for name in ("transport.js", "app.js"):
        code = (ROOT / "web" / name).read_text(encoding="utf-8").replace("</script", "<\\/script")
        scripts.append("<script>\n" + code + "\n</script>")
    start = page.index('<script src="/web/transport.js">')
    end = page.index("</body>")
    html = page[:start] + "\n".join(scripts) + "\n" + page[end:]
    check_clean(html)
    return html


def check_scripts() -> None:
    """Refuse to build if a page script does not parse. The Python tests never load the page."""
    import shutil

    node = shutil.which("node")
    if not node:
        print("node not found, page scripts not syntax checked")
        return
    for name in ("transport.js", "app.js"):
        proc = subprocess.run([node, "--check", str(ROOT / "web" / name)], capture_output=True, text=True)
        if proc.returncode != 0:
            raise SystemExit(f"web/{name} does not parse. The site was not built.\n{proc.stderr}")


def main() -> int:
    check_scripts()
    report = run_tests()
    DIST.mkdir(exist_ok=True)
    (ROOT / "web" / "test-report.json").write_text(json.dumps(report), encoding="utf-8")
    html = assemble(report)
    out = DIST / "followup.html"
    out.write_text(html, encoding="utf-8")
    print(f"{report['passed']} passed, {report['skipped']} skipped")
    print(f"Wrote {out.relative_to(ROOT)} ({len(html) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
