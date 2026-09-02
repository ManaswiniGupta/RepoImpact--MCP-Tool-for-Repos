"""Call graph traversal. plan.md §16.

Built on top of storage.py's plain accessors — no SQL/schema knowledge here,
only graph algorithms: direct callers/callees, and transitive closure with
cycle protection, a configurable depth limit, and deterministic ordering.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable

from repoimpact.models import Confidence
from repoimpact.storage import Storage

DEFAULT_MAX_DEPTH = 10

_CONFIDENCE_RANK = {Confidence.HIGH: 2, Confidence.LOW: 1}


@dataclass(frozen=True)
class Neighbor:
    symbol: sqlite3.Row
    confidence: Confidence


@dataclass(frozen=True)
class ReachableSymbol:
    symbol: sqlite3.Row
    depth: int
    # The confidence of the edge that discovered this node, not a
    # cumulative path confidence — this is per-edge information, not full
    # path analysis (plan.md §15's "don't overbuild" applies here too).
    confidence: Confidence


class CallGraph:
    def __init__(self, storage: Storage):
        self.storage = storage

    def get_callers(self, symbol_id: int) -> list[Neighbor]:
        return self._direct(symbol_id, self.storage.references_to, "source_symbol_id")

    def get_callees(self, symbol_id: int) -> list[Neighbor]:
        return self._direct(symbol_id, self.storage.references_from, "target_symbol_id")

    def get_transitive_callers(
        self, symbol_id: int, max_depth: int = DEFAULT_MAX_DEPTH, include_low_confidence: bool = False
    ) -> list[ReachableSymbol]:
        return self._transitive(symbol_id, self.get_callers, max_depth, include_low_confidence)

    def get_transitive_callees(
        self, symbol_id: int, max_depth: int = DEFAULT_MAX_DEPTH, include_low_confidence: bool = False
    ) -> list[ReachableSymbol]:
        return self._transitive(symbol_id, self.get_callees, max_depth, include_low_confidence)

    def find_reaching_entry_points(self, symbol_id: int, max_depth: int = DEFAULT_MAX_DEPTH) -> list[sqlite3.Row]:
        """Entry-point symbols that transitively call `symbol_id`, plus the
        symbol itself if it is one. Shared by search.find_symbol,
        impact.analyze_impact, and workflow.trace_workflow so each doesn't
        re-walk the same transitive closure independently."""
        symbol = self.storage.get_symbol_by_id(symbol_id)
        entry_points = []
        if symbol is not None and symbol["entry_point_method"] is not None:
            entry_points.append(symbol)
        for reached in self.get_transitive_callers(symbol_id, max_depth=max_depth):
            if reached.symbol["entry_point_method"] is not None:
                entry_points.append(reached.symbol)
        return entry_points

    def _direct(
        self,
        symbol_id: int,
        fetch_edges: Callable[[int], list[sqlite3.Row]],
        neighbor_key: str,
    ) -> list[Neighbor]:
        best: dict[int, Confidence] = {}
        for edge in fetch_edges(symbol_id):
            neighbor_id = edge[neighbor_key]
            if neighbor_id is None:
                continue
            confidence = Confidence(edge["confidence"])
            if confidence not in _CONFIDENCE_RANK:
                continue
            if neighbor_id not in best or _CONFIDENCE_RANK[confidence] > _CONFIDENCE_RANK[best[neighbor_id]]:
                best[neighbor_id] = confidence

        neighbors = []
        for neighbor_id, confidence in best.items():
            symbol = self.storage.get_symbol_by_id(neighbor_id)
            if symbol is not None:
                neighbors.append(Neighbor(symbol=symbol, confidence=confidence))
        return sorted(neighbors, key=lambda n: n.symbol["qualified_name"])

    def _transitive(
        self,
        symbol_id: int,
        direct_fn: Callable[[int], list[Neighbor]],
        max_depth: int,
        include_low_confidence: bool,
    ) -> list[ReachableSymbol]:
        visited = {symbol_id}
        frontier = [symbol_id]
        results: list[ReachableSymbol] = []
        depth = 0

        while frontier and depth < max_depth:
            depth += 1
            candidates: list[Neighbor] = []
            for node_id in frontier:
                candidates.extend(direct_fn(node_id))
            candidates.sort(key=lambda n: n.symbol["qualified_name"])

            next_frontier: list[int] = []
            for neighbor in candidates:
                neighbor_id = neighbor.symbol["id"]
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)
                results.append(ReachableSymbol(symbol=neighbor.symbol, depth=depth, confidence=neighbor.confidence))
                # Don't build a multi-hop story on an uncertain foundation:
                # a LOW-confidence (name-fallback) node isn't expanded
                # further unless the caller opts in.
                if include_low_confidence or neighbor.confidence == Confidence.HIGH:
                    next_frontier.append(neighbor_id)
            frontier = next_frontier

        return results
