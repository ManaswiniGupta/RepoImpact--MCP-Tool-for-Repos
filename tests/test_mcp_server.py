from pathlib import Path

import pytest

from repoimpact import mcp_server
from repoimpact.repository import open_repository


def _write(root: Path, relpath: str, content: str) -> None:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def demo_session(tmp_path, monkeypatch):
    _write(tmp_path, "database.py", "def get_user():\n    pass\n")
    _write(
        tmp_path,
        "auth.py",
        """
from database import get_user


def validate_user():
    get_user()


def create_token():
    pass


def login():
    validate_user()
    create_token()
""",
    )
    _write(
        tmp_path,
        "routes.py",
        """
from auth import login


@app.post("/login")
def login_endpoint():
    login()
""",
    )
    _write(
        tmp_path,
        "tests/test_auth.py",
        """
from auth import login


def test_login():
    login()
""",
    )

    session = open_repository(str(tmp_path), data_dir=tmp_path.parent / f"{tmp_path.name}_data")
    monkeypatch.setattr(mcp_server, "_session", session)
    yield session
    session.close()


def test_search_code_tool_returns_plain_dicts(demo_session):
    results = mcp_server.search_code("create_token")
    assert isinstance(results, list)
    assert results[0]["match_type"] == "exact_symbol"
    assert results[0]["file"] == "auth.py"


def test_find_symbol_tool_returns_callers_and_entry_points(demo_session):
    [detail] = mcp_server.find_symbol("create_token")
    assert detail["symbol"]["qualified_name"] == "create_token"
    caller_names = {c["qualified_name"] for c in detail["callers"]}
    assert caller_names == {"login"}


def test_analyze_impact_tool_matches_expected_shape(demo_session):
    [result] = mcp_server.analyze_impact("create_token")
    assert result["impact_level"] == "HIGH"
    assert result["workflows"] == ["POST /login"]
    assert {c["qualified_name"] for c in result["indirect_callers"]} == {"login_endpoint", "test_login"}
    assert result["affected_files"] == ["auth.py", "routes.py", "tests/test_auth.py"]


def test_trace_workflow_tool_returns_tree(demo_session):
    [tree] = mcp_server.trace_workflow("login_endpoint")
    assert tree["symbol"]["qualified_name"] == "login_endpoint"
    assert tree["symbol"]["entry_point"] == {"method": "POST", "route": "/login"}
    assert tree["children"][0]["symbol"]["qualified_name"] == "login"


def test_explain_tool_returns_deterministic_bundle(demo_session):
    result = mcp_server.explain("What happens if I remove create_token?")
    assert result["status"] == "ok"
    assert result["symbol"]["qualified_name"] == "create_token"
    assert result["impact"]["impact_level"] == "HIGH"
    assert result["workflow"][0]["symbol"]["qualified_name"] == "login_endpoint"


def test_explain_tool_reports_insufficient_evidence(demo_session):
    result = mcp_server.explain("asdkjaslkdjaslkdj nonexistent thing zzz")
    assert result["status"] == "insufficient_evidence"


def test_explain_tool_includes_llm_answer_when_provider_configured(demo_session, monkeypatch):
    class _FakeProvider:
        def explain(self, question, context):
            assert context["symbol"]["qualified_name"] == "create_token"
            return "Removing create_token would break the login flow."

    monkeypatch.setattr(mcp_server, "_llm", _FakeProvider())
    result = mcp_server.explain("What happens if I remove create_token?")
    assert result["answer"] == "Removing create_token would break the login flow."


def test_explain_tool_omits_answer_when_no_provider_configured(demo_session):
    assert mcp_server._llm is None
    result = mcp_server.explain("What happens if I remove create_token?")
    assert "answer" not in result


def test_explain_tool_survives_llm_failure_without_crashing(demo_session, monkeypatch):
    class _FailingProvider:
        def explain(self, question, context):
            raise RuntimeError("400 INVALID_ARGUMENT: API key not valid.")

    monkeypatch.setattr(mcp_server, "_llm", _FailingProvider())
    result = mcp_server.explain("What happens if I remove create_token?")

    assert result["status"] == "ok"
    assert "answer" not in result
    assert "API key not valid" in result["llm_error"]
    # The deterministic facts must still be present despite the LLM failure.
    assert result["impact"]["impact_level"] == "HIGH"
