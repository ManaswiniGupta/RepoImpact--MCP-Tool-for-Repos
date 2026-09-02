from pathlib import Path

from repoimpact.graph import CallGraph
from repoimpact.impact import analyze_impact, analyze_impact_by_name
from repoimpact.parser import parse_repository, resolve_references
from repoimpact.storage import Storage


def _write(root: Path, relpath: str, content: str) -> None:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build(tmp_path: Path):
    repo = parse_repository(tmp_path)
    refs = resolve_references(repo)
    storage = Storage(":memory:")
    storage.reindex(repo, refs)
    return storage, CallGraph(storage)


def _target(storage: Storage, file_path: str, qualified_name: str):
    return storage.get_symbol(file_path, qualified_name)


def test_changing_b_identifies_a_as_affected(tmp_path):
    _write(
        tmp_path,
        "chain.py",
        """
def c():
    pass


def b():
    c()


def a():
    b()
""",
    )
    storage, graph = _build(tmp_path)
    result = analyze_impact(storage, graph, _target(storage, "chain.py", "c"))

    assert [r.symbol["qualified_name"] for r in result.direct_callers] == ["b"]
    assert [r.symbol["qualified_name"] for r in result.indirect_callers] == ["a"]


def _demo_repo(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "auth.py",
        """
def validate_user():
    pass


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


def test_authentication():
    login()
""",
    )


def test_full_impact_report_matches_expected_shape(tmp_path):
    _demo_repo(tmp_path)
    storage, graph = _build(tmp_path)
    result = analyze_impact(storage, graph, _target(storage, "auth.py", "create_token"))

    assert [r.symbol["qualified_name"] for r in result.direct_callers] == ["login"]
    assert {r.symbol["qualified_name"] for r in result.indirect_callers} == {
        "login_endpoint",
        "test_login",
        "test_authentication",
    }
    assert result.affected_files == ["auth.py", "routes.py", "tests/test_auth.py"]
    assert {r.symbol["qualified_name"] for r in result.affected_tests} == {"test_login", "test_authentication"}
    assert [e.route for e in result.entry_points] == ["/login"]
    assert result.workflows == ["POST /login"]
    assert result.impact_level == "HIGH"
    assert "4 downstream callers" in result.reason
    assert "public API entry point" in result.reason
    assert "2 related tests" in result.reason


def test_possible_references_excluded_from_score_and_direct_callers(tmp_path):
    _write(tmp_path, "auth.py", "def create_token():\n    pass\n")
    _write(
        tmp_path,
        "unrelated.py",
        """
def handler():
    create_token()
""",
    )
    storage, graph = _build(tmp_path)
    result = analyze_impact(storage, graph, _target(storage, "auth.py", "create_token"))

    assert result.direct_callers == []
    assert result.indirect_callers == []
    assert len(result.possible_references) == 1
    assert result.possible_references[0].symbol["qualified_name"] == "handler"
    assert result.impact_level == "LOW"


def test_impact_level_thresholds(tmp_path):
    callers = "\n".join(f"def caller_{i}():\n    target()\n" for i in range(5))
    _write(tmp_path, "mod.py", f"def target():\n    pass\n\n\n{callers}")
    storage, graph = _build(tmp_path)
    result = analyze_impact(storage, graph, _target(storage, "mod.py", "target"))

    assert len(result.direct_callers) == 5
    assert result.impact_level == "HIGH"


def test_impact_level_medium(tmp_path):
    callers = "\n".join(f"def caller_{i}():\n    target()\n" for i in range(2))
    _write(tmp_path, "mod.py", f"def target():\n    pass\n\n\n{callers}")
    storage, graph = _build(tmp_path)
    result = analyze_impact(storage, graph, _target(storage, "mod.py", "target"))

    assert len(result.direct_callers) == 2
    assert result.impact_level == "MEDIUM"


def test_impact_level_low_with_no_callers(tmp_path):
    _write(tmp_path, "mod.py", "def target():\n    pass\n")
    storage, graph = _build(tmp_path)
    result = analyze_impact(storage, graph, _target(storage, "mod.py", "target"))

    assert result.direct_callers == []
    assert result.indirect_callers == []
    assert result.impact_level == "LOW"
    assert result.affected_files == ["mod.py"]


def test_analyze_impact_by_name_handles_ambiguous_name(tmp_path):
    _write(tmp_path, "a.py", "def process():\n    pass\n")
    _write(tmp_path, "b.py", "def process():\n    pass\n")
    storage, graph = _build(tmp_path)

    results = analyze_impact_by_name(storage, graph, "process")
    assert len(results) == 2
    assert {r.target["file_id"] for r in results} == {
        storage.get_file_by_path("a.py")["id"],
        storage.get_file_by_path("b.py")["id"],
    }
