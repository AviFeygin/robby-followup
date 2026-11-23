"""Website build: the engine must run without FastAPI, and the public file must be clean."""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from conftest import A, NOW
from followup.api import PUBLIC, call

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_THIRD_PARTY = {"sqlalchemy", "anthropic"}      # anthropic is imported lazily, only by the model writer


def _build_module():
    spec = importlib.util.spec_from_file_location("build_site", ROOT / "tools" / "build_site.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_engine_packages_import_only_the_standard_library_and_sqlalchemy():
    stdlib = set(sys.stdlib_module_names)
    for path in (ROOT / "followup").rglob("*.py"):
        if path.name == "app.py":          # the one file allowed to import FastAPI
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            roots = []
            if isinstance(node, ast.Import):
                roots = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                roots = [(node.module or "").split(".")[0]]
            for root in roots:
                assert root in stdlib or root in ALLOWED_THIRD_PARTY or root == "followup" or root == "uvicorn" \
                    and path.name == "cli.py", f"{path.relative_to(ROOT)} imports {root}"


def test_only_the_model_writer_touches_the_anthropic_sdk():
    for path in (ROOT / "followup").rglob("*.py"):
        if "anthropic" in path.read_text(encoding="utf-8") and path.name != "model.py":
            assert path.name in ("bootstrap.py",), path      # bootstrap only calls model_writer_from_env


def test_every_api_result_survives_json(eng):
    eng.run(A, NOW)
    draft = eng.overview(A, NOW)["drafts"][0]["id"]
    calls = {
        "shops": {}, "overview": {"shop_id": A, "now": NOW}, "quote_detail": {"shop_id": A, "quote_id": "Q-1012", "now": NOW},
        "state_hash": {"shop_id": A, "now": NOW}, "messy_check": {"shop_id": A, "now": NOW},
        "run": {"shop_id": A, "now": NOW}, "approve": {"shop_id": A, "draft_id": draft, "now": NOW},
        "send": {"shop_id": A, "draft_id": draft, "now": NOW}, "reconcile": {"shop_id": A, "draft_id": draft, "now": NOW},
        "hostile_cases": {}, "guard_check": {"shop_id": A, "text": "Hi Carl", "quote_id": "Q-1012", "now": NOW},
        "decisions": {"shop_id": A}, "set_capacity": {"shop_id": A, "booked_hours": 10, "median_booked_hours": 100},
        "update_config": {"shop_id": A, "changes": {"big_multiple": 3}},
        "add_quotes": {"shop_id": A, "quotes": [{"id": "Q-9001", "customer_name": "Nia Brooks", "customer_phone": "+19175559001", "tech_name": "Brad", "amount": 4000, "status": "open", "created_at": NOW}]},
    }
    assert set(calls) <= PUBLIC
    for name, kwargs in calls.items():
        result = call(eng, name, kwargs)
        assert result["ok"], (name, result)
        assert json.loads(json.dumps(result)) == result, name


def test_http_and_direct_calls_agree():
    from fastapi.testclient import TestClient

    from followup import app as server
    from followup.bootstrap import demo_engine

    client = TestClient(server.app)
    client.post("/api/reset")
    direct = call(demo_engine(), "overview", {"shop_id": A, "now": NOW})
    over_http = client.post("/api/overview", json={"shop_id": A, "now": NOW}).json()
    assert over_http == json.loads(json.dumps(direct))
    assert client.post("/api/nope", json={}).json()["error"]["code"] == "unknown_action"
    assert client.get("/").status_code == 200


def test_the_junit_parser_counts_and_groups():
    build = _build_module()
    xml = ('<testsuites><testsuite><testcase classname="tests.test_policy" name="test_a_b"/>'
           '<testcase classname="tests.test_policy" name="test_c"><failure/></testcase>'
           '<testcase classname="tests.test_rls_postgres" name="test_d"><skipped/></testcase></testsuite></testsuites>')
    report = build.parse_junit(xml)
    assert (report["passed"], report["failed"], report["skipped"], report["total"]) == (1, 1, 1, 3)
    assert report["files"][0]["tests"][0] == {"name": "a b", "outcome": "passed"}


@pytest.mark.parametrize("leak", ["sk-ant-api03-abcdefgh12345678", "C:\\Users\\Someone\\secret", "/home/someone/code/x",
                                  "ANTHROPIC_API_KEY=abc123"],
                         ids=["api-key", "windows-path", "home-path", "env-assignment"])
def test_the_build_refuses_secrets_and_machine_paths(leak):
    build = _build_module()
    with pytest.raises(SystemExit):
        build.check_clean("<html>" + leak + "</html>")
    build.check_clean("<html>postgresql+psycopg://postgres:pg@localhost:5433/postgres is the documented example</html>")


def test_the_real_build_is_clean():
    """Assemble the page with a stub report and check what would be published."""
    build = _build_module()
    html = build.assemble({"passed": 0, "failed": 0, "skipped": 0, "total": 0, "files": []})
    assert "window.FOLLOWUP_BUNDLE" in html and '<script src="/web/' not in html
    assert "followup/api.py" in html and "followup/app.py" not in html      # no FastAPI in the browser
    assert "fastapi" not in json.dumps(list(build.engine_files()))
    assert 'name="robots" content="noindex"' in html
    assert ".env" not in build.engine_files()
