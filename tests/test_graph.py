from pathlib import Path

from repoimpact.graph import CallGraph
from repoimpact.models import Confidence
from repoimpact.parser import parse_repository, resolve_references
from repoimpact.storage import Storage


def _write(root: Path, relpath: str, content: str) -> None:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build(tmp_path: Path) -> tuple[Storage, CallGraph]:
    repo = parse_repository(tmp_path)
    refs = resolve_references(repo)
    storage = Storage(":memory:")
    storage.reindex(repo, refs)
    return storage, CallGraph(storage)


def _id(storage: Storage, file_path: str, qualified_name: str) -> int:
    return storage.get_symbol(file_path, qualified_name)["id"]


def _names(neighbors) -> list[str]:
    return [n.symbol["qualified_name"] for n in neighbors]


def test_simple_chain_callers_and_callees(tmp_path):
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
    a_id, b_id, c_id = (_id(storage, "chain.py", n) for n in ("a", "b", "c"))

    assert _names(graph.get_callees(a_id)) == ["b"]
    assert _names(graph.get_callees(b_id)) == ["c"]
    assert _names(graph.get_callers(c_id)) == ["b"]
    assert _names(graph.get_callers(b_id)) == ["a"]
    assert graph.get_callers(a_id) == []
    assert graph.get_callees(c_id) == []


def test_transitive_callees_and_callers(tmp_path):
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
    a_id, c_id = _id(storage, "chain.py", "a"), _id(storage, "chain.py", "c")

    transitive = graph.get_transitive_callees(a_id)
    assert [(r.symbol["qualified_name"], r.depth) for r in transitive] == [("b", 1), ("c", 2)]
    assert all(r.confidence == Confidence.HIGH for r in transitive)

    transitive_callers = graph.get_transitive_callers(c_id)
    assert [(r.symbol["qualified_name"], r.depth) for r in transitive_callers] == [("b", 1), ("a", 2)]


def test_cycle_traversal_terminates_and_deduplicates(tmp_path):
    _write(
        tmp_path,
        "cycle.py",
        """
def a():
    b()


def b():
    c()


def c():
    a()
""",
    )
    storage, graph = _build(tmp_path)
    a_id = _id(storage, "cycle.py", "a")

    transitive = graph.get_transitive_callees(a_id, max_depth=10)
    names = {r.symbol["qualified_name"] for r in transitive}
    assert names == {"b", "c"}  # not "a" itself, no infinite loop, no duplicates
    assert len(transitive) == 2


def test_max_depth_limits_traversal(tmp_path):
    _write(
        tmp_path,
        "chain.py",
        """
def d():
    pass


def c():
    d()


def b():
    c()


def a():
    b()
""",
    )
    storage, graph = _build(tmp_path)
    a_id = _id(storage, "chain.py", "a")

    limited = graph.get_transitive_callees(a_id, max_depth=1)
    assert _names(limited) == ["b"]

    full = graph.get_transitive_callees(a_id, max_depth=10)
    assert [n.symbol["qualified_name"] for n in full] == ["b", "c", "d"]


def test_low_confidence_neighbor_shown_but_not_expanded_by_default(tmp_path):
    _write(
        tmp_path,
        "payments.py",
        """
def helper():
    pass


def process():
    helper()
""",
    )
    _write(
        tmp_path,
        "unrelated.py",
        """
def handler():
    process()
""",
    )
    storage, graph = _build(tmp_path)
    handler_id = _id(storage, "unrelated.py", "handler")

    direct = graph.get_callees(handler_id)
    assert _names(direct) == ["process"]
    assert direct[0].confidence == Confidence.LOW

    default_transitive = graph.get_transitive_callees(handler_id, max_depth=10)
    assert _names(default_transitive) == ["process"]  # helper not reached

    opted_in = graph.get_transitive_callees(handler_id, max_depth=10, include_low_confidence=True)
    assert _names(opted_in) == ["process", "helper"]


def test_deterministic_ordering_multiple_callees(tmp_path):
    _write(
        tmp_path,
        "fan.py",
        """
def zeta():
    pass


def alpha():
    pass


def middle():
    pass


def entry():
    zeta()
    alpha()
    middle()
""",
    )
    storage, graph = _build(tmp_path)
    entry_id = _id(storage, "fan.py", "entry")
    assert _names(graph.get_callees(entry_id)) == ["alpha", "middle", "zeta"]
