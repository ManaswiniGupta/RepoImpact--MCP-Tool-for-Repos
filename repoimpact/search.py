"""Local repository search. plan.md §17, §18, §26, §27.

No embeddings, no vector index. `search_code` ranks matches by a fixed
priority (exact symbol > qualified symbol > filename > symbol substring >
source text), falling back to a plain-text scan of the checked-out source
only for that last tier. `find_symbol` and `get_source_context` are thin
wrappers around storage.py/graph.py — no separate intelligence here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from repoimpact.graph import CallGraph, Neighbor
from repoimpact.parser import find_python_files
from repoimpact.storage import Storage

DEFAULT_SEARCH_LIMIT = 20
SOURCE_TEXT_MAX_MATCHES = 20
SOURCE_CONTEXT_PADDING = 3

_TIER_RANK = {
    "exact_symbol": 0,
    "qualified_symbol": 1,
    "filename": 2,
    "symbol_substring": 3,
    "source_text": 4,
}


@dataclass(frozen=True)
class SearchResult:
    match_type: str
    file_path: str
    line: int | None
    name: str
    qualified_name: str | None
    snippet: str | None = None


@dataclass(frozen=True)
class SourceContext:
    file_path: str
    start_line: int
    end_line: int
    lines: list[tuple[int, str]]


@dataclass(frozen=True)
class SymbolDetail:
    symbol: sqlite3.Row
    file_path: str
    callers: list[Neighbor]
    callees: list[Neighbor]
    tests: list[Neighbor]
    entry_points: list[sqlite3.Row]


def _directory_boost(query: str, file_path: str) -> int:
    """0 if a directory segment of file_path relates to the query, else 1.

    A generic version of plan.md §35's directory prioritization: no
    hardcoded folder names (auth/, security/, ...), just a bidirectional
    substring check between the query and each path segment.
    """
    query_lower = query.lower()
    for part in Path(file_path).parent.parts:
        part_lower = part.lower()
        if part_lower and (part_lower in query_lower or query_lower in part_lower):
            return 0
    return 1


def search_code(storage: Storage, repo_root: Path, query: str, limit: int = DEFAULT_SEARCH_LIMIT) -> list[SearchResult]:
    query = query.strip()
    if not query:
        return []
    query_lower = query.lower()

    file_paths = {f["id"]: f["path"] for f in storage.list_files()}
    by_location: dict[tuple[str, int | None, str], SearchResult] = {}

    def _add(result: SearchResult) -> None:
        key = (result.file_path, result.line, result.name)
        existing = by_location.get(key)
        if existing is None or _TIER_RANK[result.match_type] < _TIER_RANK[existing.match_type]:
            by_location[key] = result

    for symbol in storage.list_symbols():
        file_path = file_paths[symbol["file_id"]]
        name, qualified_name = symbol["name"], symbol["qualified_name"]
        if name.lower() == query_lower:
            _add(SearchResult("exact_symbol", file_path, symbol["start_line"], name, qualified_name))
        elif qualified_name.lower() == query_lower:
            _add(SearchResult("qualified_symbol", file_path, symbol["start_line"], name, qualified_name))
        elif query_lower in name.lower():
            _add(SearchResult("symbol_substring", file_path, symbol["start_line"], name, qualified_name))

    for f in storage.list_files():
        filename = Path(f["path"]).name
        if query_lower in filename.lower():
            _add(SearchResult("filename", f["path"], None, filename, None))

    text_matches = 0
    if repo_root.exists():
        for path in find_python_files(repo_root):
            if text_matches >= SOURCE_TEXT_MAX_MATCHES:
                break
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            relative_path = path.relative_to(repo_root).as_posix()
            for lineno, line in enumerate(lines, start=1):
                if query_lower in line.lower():
                    _add(SearchResult("source_text", relative_path, lineno, relative_path, None, snippet=line.strip()))
                    text_matches += 1
                    if text_matches >= SOURCE_TEXT_MAX_MATCHES:
                        break

    results = list(by_location.values())
    results.sort(
        key=lambda r: (
            _TIER_RANK[r.match_type],
            _directory_boost(query, r.file_path),
            r.file_path,
            r.line or 0,
        )
    )
    return results[:limit]


def get_source_context(
    repo_root: Path, file_path: str, start_line: int, end_line: int, padding: int = SOURCE_CONTEXT_PADDING
) -> SourceContext:
    text_lines = (repo_root / file_path).read_text(encoding="utf-8").splitlines()
    lo = max(1, start_line - padding)
    hi = min(len(text_lines), end_line + padding)
    snippet = [(i, text_lines[i - 1]) for i in range(lo, hi + 1)]
    return SourceContext(file_path=file_path, start_line=lo, end_line=hi, lines=snippet)


def find_symbol(storage: Storage, graph: CallGraph, name: str) -> list[SymbolDetail]:
    file_paths = {f["id"]: f["path"] for f in storage.list_files()}
    matches = [s for s in storage.list_symbols() if s["name"] == name or s["qualified_name"] == name]

    details = []
    for symbol in matches:
        callers = graph.get_callers(symbol["id"])
        callees = graph.get_callees(symbol["id"])
        tests = [c for c in callers if c.symbol["is_test"]]
        entry_points = graph.find_reaching_entry_points(symbol["id"])

        details.append(
            SymbolDetail(
                symbol=symbol,
                file_path=file_paths[symbol["file_id"]],
                callers=callers,
                callees=callees,
                tests=tests,
                entry_points=entry_points,
            )
        )
    return details
