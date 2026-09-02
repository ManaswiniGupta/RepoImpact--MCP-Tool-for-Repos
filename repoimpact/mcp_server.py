"""MCP server exposing RepoImpact's five tools. plan.md §25–§30.

Single repository per process: which repository to serve is chosen once
at startup (CLI argument or REPOIMPACT_REPOSITORY env var), not per call —
this mirrors how MCP servers are normally launched by a host (one
configured server per project), and matches app.py (Phase 8), which opens
its own session the same way. Both consume the same core engine
(repository.py, search.py, impact.py, workflow.py) — no separate
intelligence lives here, only serialization.
"""

from __future__ import annotations

import os
import re
import sys
import sqlite3

from mcp.server.mcpserver import MCPServer

from repoimpact.graph import Neighbor, ReachableSymbol
from repoimpact.impact import ImpactResult
from repoimpact.impact import analyze_impact as _analyze_impact_core
from repoimpact.impact import analyze_impact_by_name as _analyze_impact_by_name
from repoimpact.llm import LLMProvider, load_default_provider
from repoimpact.repository import RepositorySession, open_repository
from repoimpact.search import SearchResult, SymbolDetail
from repoimpact.search import find_symbol as _find_symbol
from repoimpact.search import search_code as _search_code
from repoimpact.workflow import WorkflowNode
from repoimpact.workflow import trace_workflow as _trace_workflow

mcp = MCPServer(name="repoimpact", description="Structural change-impact analysis for Python repositories.")

_session: RepositorySession | None = None
_llm: LLMProvider | None = None


# --------------------------------------------------------------------------
# Serialization — plain dicts only, everything below returns JSON-friendly data
# --------------------------------------------------------------------------


def _file_paths(session: RepositorySession) -> dict[int, str]:
    return {f["id"]: f["path"] for f in session.storage.list_files()}


def _serialize_symbol(row: sqlite3.Row, file_paths: dict[int, str]) -> dict:
    return {
        "name": row["name"],
        "qualified_name": row["qualified_name"],
        "type": row["type"],
        "file": file_paths[row["file_id"]],
        "start_line": row["start_line"],
        "end_line": row["end_line"],
        "is_test": bool(row["is_test"]),
        "entry_point": (
            {"method": row["entry_point_method"], "route": row["entry_point_route"]}
            if row["entry_point_method"] is not None
            else None
        ),
    }


def _serialize_neighbor(neighbor: Neighbor, file_paths: dict[int, str]) -> dict:
    result = _serialize_symbol(neighbor.symbol, file_paths)
    result["confidence"] = neighbor.confidence.value
    return result


def _serialize_reachable(reached: ReachableSymbol, file_paths: dict[int, str]) -> dict:
    result = _serialize_symbol(reached.symbol, file_paths)
    result["confidence"] = reached.confidence.value
    result["depth"] = reached.depth
    return result


def _serialize_search_result(result: SearchResult) -> dict:
    return {
        "match_type": result.match_type,
        "file": result.file_path,
        "line": result.line,
        "name": result.name,
        "qualified_name": result.qualified_name,
        "snippet": result.snippet,
    }


def _serialize_symbol_detail(detail: SymbolDetail, file_paths: dict[int, str]) -> dict:
    return {
        "symbol": _serialize_symbol(detail.symbol, file_paths),
        "callers": [_serialize_neighbor(c, file_paths) for c in detail.callers],
        "callees": [_serialize_neighbor(c, file_paths) for c in detail.callees],
        "tests": [_serialize_neighbor(t, file_paths) for t in detail.tests],
        "entry_points": [
            {"method": e["entry_point_method"], "route": e["entry_point_route"], "symbol": e["qualified_name"]}
            for e in detail.entry_points
        ],
    }


def _serialize_impact(result: ImpactResult, file_paths: dict[int, str]) -> dict:
    return {
        "target": _serialize_symbol(result.target, file_paths),
        "direct_callers": [_serialize_reachable(r, file_paths) for r in result.direct_callers],
        "indirect_callers": [_serialize_reachable(r, file_paths) for r in result.indirect_callers],
        "affected_files": result.affected_files,
        "affected_tests": [_serialize_reachable(r, file_paths) for r in result.affected_tests],
        "entry_points": [{"method": e.method, "route": e.route} for e in result.entry_points],
        "workflows": result.workflows,
        "possible_references": [_serialize_reachable(r, file_paths) for r in result.possible_references],
        "impact_level": result.impact_level,
        "reason": result.reason,
    }


def _serialize_workflow_node(node: WorkflowNode, file_paths: dict[int, str]) -> dict:
    return {
        "symbol": _serialize_symbol(node.symbol, file_paths),
        "confidence": node.confidence.value if node.confidence is not None else None,
        "children": [_serialize_workflow_node(c, file_paths) for c in node.children],
    }


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@mcp.tool()
def search_code(query: str, limit: int = 20) -> list[dict]:
    """Search the repository for matching symbols/files. No embeddings —
    ranked lexical/structural search over the index plus a bounded
    source-text scan."""
    results = _search_code(_session.storage, _session.root, query, limit=limit)
    return [_serialize_search_result(r) for r in results]


@mcp.tool()
def find_symbol(name: str) -> list[dict]:
    """Look up a symbol: location, callers, callees, tests, entry points.
    A list because a bare name may match more than one symbol."""
    file_paths = _file_paths(_session)
    return [_serialize_symbol_detail(d, file_paths) for d in _find_symbol(_session.storage, _session.graph, name)]


@mcp.tool()
def analyze_impact(symbol: str) -> list[dict]:
    """What breaks if `symbol` changes: direct/indirect callers, affected
    files/tests, entry points, and a heuristic impact level with reason."""
    file_paths = _file_paths(_session)
    results = _analyze_impact_by_name(_session.storage, _session.graph, symbol)
    return [_serialize_impact(r, file_paths) for r in results]


@mcp.tool()
def trace_workflow(symbol: str) -> list[dict]:
    """Trace the execution flow from the nearest entry point down through
    `symbol` (or `symbol`'s own downstream tree, if no entry point reaches it)."""
    file_paths = _file_paths(_session)
    matches = [
        s for s in _session.storage.list_symbols() if s["name"] == symbol or s["qualified_name"] == symbol
    ]
    trees = [tree for target in matches for tree in _trace_workflow(_session.graph, target)]
    return [_serialize_workflow_node(t, file_paths) for t in trees]


@mcp.tool()
def explain(question: str) -> dict:
    """Answer a natural-language question about the repository (plan.md §30).

    Deterministic analysis (resolve symbol -> impact -> workflow) always
    runs first and is always returned as structured evidence. If an LLM
    provider is configured (GEMINI_API_KEY), its "answer" field also
    contains a natural-language explanation grounded in that evidence —
    the LLM only explains facts already computed by impact.py/workflow.py,
    it never sees repository source or decides what the facts are. Without
    a provider configured, the tool still works, just without "answer"."""
    return build_explanation(_session.storage, _session.graph, _session.root, question, llm=_llm)


_WORD_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def resolve_target_symbol(storage, repo_root, question: str) -> sqlite3.Row | None:
    """Deterministic symbol extraction from a natural-language question
    (§30) — try the whole question first, then fall back to its
    identifier-like tokens, most specific (underscored, longer) first.
    This is the "determine symbol" step; turning the result into prose is
    an LLM's job (Phase 9), not this function's."""
    tokens = [question] + sorted(
        set(_WORD_PATTERN.findall(question)), key=lambda t: (-("_" in t), -len(t))
    )
    for token in tokens:
        for result in _search_code(storage, repo_root, token, limit=5):
            if result.match_type in ("exact_symbol", "qualified_symbol", "symbol_substring"):
                return storage.get_symbol(result.file_path, result.qualified_name)
    return None


def build_explanation(storage, graph, repo_root, question: str, llm: LLMProvider | None = None) -> dict:
    """Shared by the `explain` MCP tool and app.py's Chat tab (§34) — one
    implementation, two consumers.

    `llm` is optional and, when given, only turns the already-computed
    facts below into prose (the "answer" field) — it is never sent the
    repository and never decides what symbol/impact/workflow to compute."""
    file_paths = {f["id"]: f["path"] for f in storage.list_files()}
    symbol = resolve_target_symbol(storage, repo_root, question)
    if symbol is None:
        return {
            "status": "insufficient_evidence",
            "question": question,
            "message": "I couldn't establish sufficient evidence.",
        }
    impact = _analyze_impact_core(storage, graph, symbol)
    trees = _trace_workflow(graph, symbol)
    result = {
        "status": "ok",
        "question": question,
        "symbol": _serialize_symbol(symbol, file_paths),
        "impact": _serialize_impact(impact, file_paths),
        "workflow": [_serialize_workflow_node(t, file_paths) for t in trees],
    }
    if llm is not None:
        try:
            result["answer"] = llm.explain(
                question, {"symbol": result["symbol"], "impact": result["impact"], "workflow": result["workflow"]}
            )
        except Exception as exc:
            # A bad/expired key, rate limit, or transient network failure
            # must not take down the deterministic facts above — surface it
            # as a separate field instead of raising.
            result["llm_error"] = str(exc)
    return result


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def _load_session() -> RepositorySession:
    source = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("REPOIMPACT_REPOSITORY")
    if not source:
        raise SystemExit(
            "Usage: python -m repoimpact.mcp_server <github-url-or-local-path>\n"
            "(or set the REPOIMPACT_REPOSITORY environment variable)"
        )
    return open_repository(source)


def main() -> None:
    global _session, _llm
    _session = _load_session()
    _llm = load_default_provider()
    try:
        mcp.run()
    finally:
        _session.close()


if __name__ == "__main__":
    main()
