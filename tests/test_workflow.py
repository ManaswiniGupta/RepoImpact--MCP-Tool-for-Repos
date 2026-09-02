from pathlib import Path

from repoimpact.graph import CallGraph
from repoimpact.parser import parse_repository, resolve_references
from repoimpact.storage import Storage
from repoimpact.workflow import render_workflow_text, trace_workflow


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


def _shape(node):
    return (node.symbol["qualified_name"], [_shape(c) for c in node.children])


def _demo_repo(tmp_path: Path) -> None:
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


_EXPECTED_SHAPE = (
    "login_endpoint",
    [
        (
            "login",
            [
                ("create_token", []),
                ("validate_user", [("get_user", [])]),
            ],
        )
    ],
)


def test_trace_workflow_from_entry_point_itself(tmp_path):
    _demo_repo(tmp_path)
    storage, graph = _build(tmp_path)
    target = storage.get_symbol("routes.py", "login_endpoint")

    [root] = trace_workflow(graph, target)
    assert _shape(root) == _EXPECTED_SHAPE


def test_trace_workflow_from_deep_symbol_finds_entry_point(tmp_path):
    _demo_repo(tmp_path)
    storage, graph = _build(tmp_path)
    target = storage.get_symbol("auth.py", "create_token")

    [root] = trace_workflow(graph, target)
    assert _shape(root) == _EXPECTED_SHAPE


def test_trace_workflow_falls_back_to_symbol_when_no_entry_point(tmp_path):
    _write(tmp_path, "utils.py", "def utility():\n    pass\n")
    storage, graph = _build(tmp_path)
    target = storage.get_symbol("utils.py", "utility")

    [root] = trace_workflow(graph, target)
    assert _shape(root) == ("utility", [])


def test_max_depth_limits_workflow_tree(tmp_path):
    _write(
        tmp_path,
        "chain.py",
        """
def e():
    pass


def d():
    e()


def c():
    d()


def b():
    c()


def a():
    b()
""",
    )
    storage, graph = _build(tmp_path)
    target = storage.get_symbol("chain.py", "a")

    [root] = trace_workflow(graph, target, max_depth=2)
    assert _shape(root) == ("a", [("b", [("c", [])])])


def test_cycle_protection_terminates(tmp_path):
    _write(
        tmp_path,
        "mutual.py",
        """
def a():
    b()


def b():
    a()
""",
    )
    storage, graph = _build(tmp_path)
    target = storage.get_symbol("mutual.py", "a")

    [root] = trace_workflow(graph, target, max_depth=10)
    assert _shape(root) == ("a", [("b", [])])


def test_low_confidence_child_not_expanded_further(tmp_path):
    _write(
        tmp_path,
        "misc.py",
        """
def helper():
    pass


def fallback_target():
    helper()
""",
    )
    _write(
        tmp_path,
        "caller.py",
        """
def entry():
    fallback_target()
""",
    )
    storage, graph = _build(tmp_path)
    target = storage.get_symbol("caller.py", "entry")

    [root] = trace_workflow(graph, target)
    assert _shape(root) == ("entry", [("fallback_target", [])])  # helper() not reached


def test_render_workflow_text_format(tmp_path):
    _write(
        tmp_path,
        "routes.py",
        """
@app.post("/login")
def login_endpoint():
    login()


def login():
    pass
""",
    )
    storage, graph = _build(tmp_path)
    target = storage.get_symbol("routes.py", "login_endpoint")
    [root] = trace_workflow(graph, target)

    text = render_workflow_text(root)
    assert text == "POST /login\n    ↓\nlogin_endpoint()\n    ↓\nlogin()"


def test_render_workflow_text_marks_low_confidence(tmp_path):
    _write(tmp_path, "misc.py", "def fallback_target():\n    pass\n")
    # No import connecting other.py to misc.py: this call can only resolve
    # via the unique bare-name fallback (§15 step 6), at LOW confidence.
    _write(tmp_path, "other.py", "def another():\n    fallback_target()\n")
    storage, graph = _build(tmp_path)
    target = storage.get_symbol("other.py", "another")
    [root] = trace_workflow(graph, target)

    text = render_workflow_text(root)
    assert "fallback_target() [LOW confidence]" in text
