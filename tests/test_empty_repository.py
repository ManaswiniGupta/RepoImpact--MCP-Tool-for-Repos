"""plan.md §41 explicitly calls out the empty-repository case — every layer
must degrade to an empty result, never crash.
"""

from repoimpact.graph import CallGraph
from repoimpact.impact import analyze_impact_by_name
from repoimpact.parser import parse_repository, resolve_references
from repoimpact.search import find_symbol, search_code
from repoimpact.storage import Storage
from repoimpact.workflow import trace_workflow


def test_empty_repository_produces_empty_results_everywhere(tmp_path):
    repo = parse_repository(tmp_path)
    assert repo.files == []
    assert repo.errors == []

    refs = resolve_references(repo)
    assert refs == []

    storage = Storage(":memory:")
    storage.reindex(repo, refs)
    graph = CallGraph(storage)

    assert storage.list_files() == []
    assert storage.list_symbols() == []
    assert search_code(storage, tmp_path, "anything") == []
    assert find_symbol(storage, graph, "anything") == []
    assert analyze_impact_by_name(storage, graph, "anything") == []


def test_reindexing_empty_repository_twice_does_not_crash(tmp_path):
    repo = parse_repository(tmp_path)
    refs = resolve_references(repo)
    storage = Storage(":memory:")
    storage.reindex(repo, refs)
    storage.reindex(repo, refs)  # must not raise
    assert storage.list_files() == []
