"""Workflow tracing. plan.md §21, §29.

`trace_workflow(symbol)` renders the execution flow starting from the
nearest entry point that reaches `symbol` (or `symbol` itself if it has no
traceable entry point), down through its callees. Entry-point discovery
reuses `CallGraph.find_reaching_entry_points` — no separate logic here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from repoimpact.graph import CallGraph, DEFAULT_MAX_DEPTH
from repoimpact.models import Confidence


@dataclass(frozen=True)
class WorkflowNode:
    symbol: sqlite3.Row
    # Confidence of the edge that reached this node from its parent; None
    # for the root, which has no incoming edge in this tree.
    confidence: Confidence | None
    children: list["WorkflowNode"]


def _build_tree(graph: CallGraph, symbol: sqlite3.Row, confidence: Confidence | None, max_depth: int) -> WorkflowNode:
    def _walk(node_symbol: sqlite3.Row, node_confidence: Confidence | None, ancestors: frozenset[int], depth: int) -> WorkflowNode:
        children: list[WorkflowNode] = []
        # Don't build a multi-hop story on an uncertain foundation (same
        # rule as graph.py's transitive traversal): only expand past a
        # HIGH-confidence edge.
        may_expand = node_confidence is None or node_confidence == Confidence.HIGH
        if may_expand and depth < max_depth:
            for neighbor in graph.get_callees(node_symbol["id"]):
                if neighbor.symbol["id"] in ancestors:
                    continue  # cycle guard: don't recurse into our own ancestor
                children.append(
                    _walk(neighbor.symbol, neighbor.confidence, ancestors | {node_symbol["id"]}, depth + 1)
                )
        return WorkflowNode(symbol=node_symbol, confidence=node_confidence, children=children)

    return _walk(symbol, confidence, frozenset(), 0)


def trace_workflow(graph: CallGraph, target: sqlite3.Row, max_depth: int = DEFAULT_MAX_DEPTH) -> list[WorkflowNode]:
    """One tree per distinct entry point that reaches `target` (usually
    zero or one; occasionally more, if a shared utility function is
    reachable from more than one route). Falls back to a tree rooted at
    `target` itself when no entry point reaches it."""
    entry_points = graph.find_reaching_entry_points(target["id"], max_depth=max_depth)
    roots = entry_points if entry_points else [target]
    return [_build_tree(graph, root, None, max_depth) for root in roots]


def render_workflow_text(node: WorkflowNode) -> str:
    """A compact text tree, mainly for demos/tests — MCP/UI consumers are
    free to render the structured WorkflowNode however they prefer."""
    lines: list[str] = []

    def _render(n: WorkflowNode) -> None:
        if n.symbol["entry_point_method"] is not None:
            lines.append(f'{n.symbol["entry_point_method"]} {n.symbol["entry_point_route"]}')
            lines.append("    ↓")
        suffix = " [LOW confidence]" if n.confidence == Confidence.LOW else ""
        lines.append(f'{n.symbol["qualified_name"]}(){suffix}')
        for child in n.children:
            lines.append("    ↓")
            _render(child)

    _render(node)
    return "\n".join(lines)
