"""Change-impact analysis. plan.md §22, §23, §24 — the flagship feature.

`analyze_impact` answers "what breaks if I change this?" using only the
call graph (graph.py) and the entry-point metadata already captured at
parse time (§20) — no dependency on trace_workflow (Phase 6); "workflows"
here is just the distinct set of entry points reached, not the full path
render that trace_workflow will produce.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from repoimpact.graph import CallGraph, ReachableSymbol
from repoimpact.models import Confidence
from repoimpact.storage import Storage

IMPACT_HIGH_CALLER_THRESHOLD = 5
IMPACT_MEDIUM_CALLER_THRESHOLD = 2


@dataclass(frozen=True)
class EntryPointRef:
    method: str
    route: str
    symbol: sqlite3.Row


@dataclass(frozen=True)
class ImpactResult:
    target: sqlite3.Row
    direct_callers: list[ReachableSymbol]
    indirect_callers: list[ReachableSymbol]
    affected_files: list[str]
    affected_tests: list[ReachableSymbol]
    entry_points: list[EntryPointRef]
    workflows: list[str]  # "METHOD /route", one per distinct entry point
    possible_references: list[ReachableSymbol]  # LOW confidence — never counted toward the score
    impact_level: str  # LOW | MEDIUM | HIGH
    reason: str


def analyze_impact(storage: Storage, graph: CallGraph, target: sqlite3.Row, max_depth: int = 10) -> ImpactResult:
    file_paths = {f["id"]: f["path"] for f in storage.list_files()}

    transitive = graph.get_transitive_callers(target["id"], max_depth=max_depth)
    direct_callers = [r for r in transitive if r.depth == 1 and r.confidence == Confidence.HIGH]
    indirect_callers = [r for r in transitive if r.depth > 1]
    possible_references = [r for r in transitive if r.depth == 1 and r.confidence == Confidence.LOW]
    confident_callers = direct_callers + indirect_callers

    affected_tests = [r for r in confident_callers if r.symbol["is_test"]]

    entry_points = [
        EntryPointRef(method=s["entry_point_method"], route=s["entry_point_route"], symbol=s)
        for s in graph.find_reaching_entry_points(target["id"], max_depth=max_depth)
    ]
    workflows = sorted({f"{e.method} {e.route}" for e in entry_points})

    affected_file_paths = {file_paths[target["file_id"]]}
    for r in confident_callers:
        affected_file_paths.add(file_paths[r.symbol["file_id"]])

    downstream_count = len(confident_callers)
    has_entry_point = len(entry_points) > 0
    impact_level = _impact_level(downstream_count, has_entry_point, len(workflows))
    reason = _reason(downstream_count, has_entry_point, len(affected_tests))

    return ImpactResult(
        target=target,
        direct_callers=direct_callers,
        indirect_callers=indirect_callers,
        affected_files=sorted(affected_file_paths),
        affected_tests=affected_tests,
        entry_points=entry_points,
        workflows=workflows,
        possible_references=possible_references,
        impact_level=impact_level,
        reason=reason,
    )


def analyze_impact_by_name(
    storage: Storage, graph: CallGraph, name: str, max_depth: int = 10
) -> list[ImpactResult]:
    """Convenience wrapper matching the `analyze_impact(symbol: str)` tool
    signature (§28). A bare name may match more than one symbol across the
    repository — return one result per match rather than guessing."""
    matches = [s for s in storage.list_symbols() if s["name"] == name or s["qualified_name"] == name]
    return [analyze_impact(storage, graph, target, max_depth=max_depth) for target in matches]


def _impact_level(downstream_count: int, has_entry_point: bool, workflow_count: int) -> str:
    if downstream_count >= IMPACT_HIGH_CALLER_THRESHOLD or has_entry_point or workflow_count > 1:
        return "HIGH"
    if downstream_count >= IMPACT_MEDIUM_CALLER_THRESHOLD:
        return "MEDIUM"
    return "LOW"


def _reason(downstream_count: int, has_entry_point: bool, test_count: int) -> str:
    caller_word = "caller" if downstream_count == 1 else "callers"
    text = f"The target has {downstream_count} downstream {caller_word}"
    extras = []
    if has_entry_point:
        extras.append("a detected public API entry point")
    if test_count:
        test_word = "test" if test_count == 1 else "tests"
        extras.append(f"{test_count} related {test_word}")
    if extras:
        text += ", including " + " and ".join(extras)
    return text + "."
