"""SQLite persistence layer. plan.md §13, §14.

This module owns the schema and CRUD only. Traversal algorithms (callers,
callees, transitive closure) belong in graph.py, built on top of the simple
accessors here — this module does not implement graph logic itself.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from repoimpact.models import ParsedRepository, Reference

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    hash TEXT NOT NULL,
    size INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    type TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    parent_id INTEGER REFERENCES symbols(id) ON DELETE SET NULL,
    is_test INTEGER NOT NULL DEFAULT 0,
    entry_point_method TEXT,
    entry_point_route TEXT,
    UNIQUE (file_id, qualified_name)
);

CREATE TABLE IF NOT EXISTS "references" (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_symbol_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    target_symbol_id INTEGER REFERENCES symbols(id) ON DELETE SET NULL,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    line INTEGER NOT NULL,
    kind TEXT NOT NULL,
    resolution TEXT,
    confidence TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    module TEXT NOT NULL,
    name TEXT,
    line INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_qualified_name ON symbols(qualified_name);
CREATE INDEX IF NOT EXISTS idx_symbols_is_test ON symbols(is_test);
CREATE INDEX IF NOT EXISTS idx_references_source ON "references"(source_symbol_id);
CREATE INDEX IF NOT EXISTS idx_references_target ON "references"(target_symbol_id);
CREATE INDEX IF NOT EXISTS idx_references_confidence ON "references"(confidence);
"""


class Storage:
    def __init__(self, db_path: str | Path):
        # check_same_thread=False: Streamlit (app.py) reruns each interaction
        # on a new thread while keeping a Storage instance alive in
        # session_state across reruns. Access here is sequential, never
        # concurrent, so this is safe despite the name.
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ----------------------------------------------------------------
    # Indexing
    # ----------------------------------------------------------------

    def reindex(self, parsed_repo: ParsedRepository, references: list[Reference]) -> None:
        """Full re-index: clear tables, write the new index, commit as one
        transaction. plan.md §14 — no incremental upsert/dedup logic. If
        anything fails, roll back so the previous valid index is untouched.
        """
        conn = self._conn
        try:
            conn.execute("BEGIN")
            conn.execute('DELETE FROM "references"')
            conn.execute("DELETE FROM imports")
            conn.execute("DELETE FROM symbols")
            conn.execute("DELETE FROM files")

            file_ids: dict[str, int] = {}
            for parsed_file in parsed_repo.files:
                sf = parsed_file.source_file
                cur = conn.execute(
                    "INSERT INTO files (path, hash, size) VALUES (?, ?, ?)",
                    (sf.path, sf.file_hash, sf.size),
                )
                file_ids[sf.path] = cur.lastrowid

            # Last definition wins when a qualified_name repeats in one file
            # (matches Python's own runtime redefinition semantics, and the
            # resolver's index in parser.py uses the same rule).
            symbol_ids: dict[tuple[str, str], int] = {}
            for parsed_file in parsed_repo.files:
                file_path = parsed_file.source_file.path
                deduped = {}
                for symbol in parsed_file.symbols:
                    deduped[symbol.qualified_name] = symbol
                for symbol in deduped.values():
                    cur = conn.execute(
                        """INSERT INTO symbols
                           (file_id, name, qualified_name, type, start_line, end_line,
                            is_test, entry_point_method, entry_point_route)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            file_ids[file_path],
                            symbol.name,
                            symbol.qualified_name,
                            symbol.type.value,
                            symbol.start_line,
                            symbol.end_line,
                            int(symbol.is_test),
                            symbol.entry_point.method if symbol.entry_point else None,
                            symbol.entry_point.route if symbol.entry_point else None,
                        ),
                    )
                    symbol_ids[(file_path, symbol.qualified_name)] = cur.lastrowid

            for parsed_file in parsed_repo.files:
                file_path = parsed_file.source_file.path
                deduped = {}
                for symbol in parsed_file.symbols:
                    deduped[symbol.qualified_name] = symbol
                for symbol in deduped.values():
                    if symbol.parent_qualified_name is None:
                        continue
                    parent_id = symbol_ids.get((file_path, symbol.parent_qualified_name))
                    if parent_id is not None:
                        conn.execute(
                            "UPDATE symbols SET parent_id = ? WHERE id = ?",
                            (parent_id, symbol_ids[(file_path, symbol.qualified_name)]),
                        )

            for parsed_file in parsed_repo.files:
                file_path = parsed_file.source_file.path
                for binding in parsed_file.imports:
                    conn.execute(
                        "INSERT INTO imports (file_id, module, name, line) VALUES (?, ?, ?, ?)",
                        (file_ids[file_path], binding.module, binding.imported_name, binding.line),
                    )

            for ref in references:
                # A module-level call site (top-level statement, not inside
                # any function) has no enclosing symbol to be the "caller" —
                # it isn't part of the function-level call graph (§16), so
                # it is intentionally not persisted as a reference edge.
                source_id = symbol_ids.get((ref.file_path, ref.source_qualified_name))
                if source_id is None:
                    continue
                target_id = None
                if ref.target_qualified_name is not None and ref.target_file_path is not None:
                    target_id = symbol_ids.get((ref.target_file_path, ref.target_qualified_name))
                conn.execute(
                    """INSERT INTO "references"
                       (source_symbol_id, target_symbol_id, file_id, line, kind, resolution, confidence)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        source_id,
                        target_id,
                        file_ids[ref.file_path],
                        ref.line,
                        ref.kind,
                        ref.resolution.value if ref.resolution else None,
                        ref.confidence.value,
                    ),
                )

            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # ----------------------------------------------------------------
    # Accessors
    # ----------------------------------------------------------------

    def list_files(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM files ORDER BY path").fetchall()

    def get_file_by_path(self, path: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()

    def list_symbols(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM symbols ORDER BY id").fetchall()

    def get_symbol_by_id(self, symbol_id: int) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM symbols WHERE id = ?", (symbol_id,)).fetchone()

    def get_symbol(self, file_path: str, qualified_name: str) -> sqlite3.Row | None:
        return self._conn.execute(
            """SELECT symbols.* FROM symbols
               JOIN files ON files.id = symbols.file_id
               WHERE files.path = ? AND symbols.qualified_name = ?""",
            (file_path, qualified_name),
        ).fetchone()

    def find_symbols_by_name(self, name: str) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM symbols WHERE name = ?", (name,)).fetchall()

    def references_from(self, symbol_id: int) -> list[sqlite3.Row]:
        """Outgoing edges — callees of `symbol_id`."""
        return self._conn.execute(
            'SELECT * FROM "references" WHERE source_symbol_id = ?', (symbol_id,)
        ).fetchall()

    def references_to(self, symbol_id: int) -> list[sqlite3.Row]:
        """Incoming edges — callers of `symbol_id`."""
        return self._conn.execute(
            'SELECT * FROM "references" WHERE target_symbol_id = ?', (symbol_id,)
        ).fetchall()

    def list_references(self) -> list[sqlite3.Row]:
        return self._conn.execute('SELECT * FROM "references" ORDER BY id').fetchall()
