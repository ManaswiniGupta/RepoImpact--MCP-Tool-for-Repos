"""Pins the shipped examples/demo_repo's analysis results against what the
README's demo section claims. If this test breaks, the README needs an
update — not the other way around.
"""

from pathlib import Path

from repoimpact.graph import CallGraph
from repoimpact.impact import analyze_impact_by_name
from repoimpact.parser import parse_repository, resolve_references
from repoimpact.search import find_symbol, search_code
from repoimpact.storage import Storage
from repoimpact.workflow import trace_workflow

DEMO_REPO = Path(__file__).parent.parent / "examples" / "demo_repo"


def _build():
    repo = parse_repository(DEMO_REPO)
    refs = resolve_references(repo)
    storage = Storage(":memory:")
    storage.reindex(repo, refs)
    return storage, CallGraph(storage)


def test_demo_repo_has_no_parse_errors():
    repo = parse_repository(DEMO_REPO)
    assert repo.errors == []
    assert len(repo.files) == 9  # app, auth, database, payments, routes, users + 3 tests


def test_search_authentication_related_symbols():
    storage, _ = _build()
    results = search_code(storage, DEMO_REPO, "login")
    names = {r.qualified_name or r.name for r in results}
    assert "login" in names
    assert "login_endpoint" in names


def test_who_calls_create_token():
    storage, graph = _build()
    [detail] = find_symbol(storage, graph, "create_token")
    assert {c.symbol["qualified_name"] for c in detail.callers} == {"login", "test_login_success"}


def test_impact_of_removing_create_token():
    storage, graph = _build()
    [result] = analyze_impact_by_name(storage, graph, "create_token")

    assert {r.symbol["qualified_name"] for r in result.direct_callers} == {"login", "test_login_success"}
    assert {r.symbol["qualified_name"] for r in result.indirect_callers} == {
        "login_endpoint",
        "test_login_invalid_password",
    }
    assert result.workflows == ["POST /login"]
    assert result.impact_level == "HIGH"
    assert {r.symbol["qualified_name"] for r in result.affected_tests} == {
        "test_login_success",
        "test_login_invalid_password",
    }


def test_trace_login_workflow():
    storage, graph = _build()
    target = storage.get_symbol("routes.py", "login_endpoint")
    [root] = trace_workflow(graph, target)

    def shape(node):
        return (node.symbol["qualified_name"], [shape(c) for c in node.children])

    assert shape(root) == (
        "login_endpoint",
        [
            (
                "login",
                [
                    ("create_token", []),
                    (
                        "validate_user",
                        [
                            ("UserRepository", []),  # instantiation call
                            ("UserRepository.get_user", []),  # db.get_user() is ambiguous -> unresolved
                        ],
                    ),
                ],
            )
        ],
    )


def test_trace_checkout_workflow():
    storage, graph = _build()
    target = storage.get_symbol("routes.py", "checkout_endpoint")
    [root] = trace_workflow(graph, target)

    def shape(node):
        return (node.symbol["qualified_name"], [shape(c) for c in node.children])

    assert shape(root) == (
        "checkout_endpoint",
        [
            (
                "checkout",
                [
                    (
                        "process_payment",
                        [
                            ("PaymentRepository", []),  # instantiation call
                            (
                                "PaymentRepository.save",
                                # db.save_payment() -> uniquely named -> LOW-confidence fallback
                                [("Database.save_payment", [])],
                            ),
                        ],
                    )
                ],
            )
        ],
    )
