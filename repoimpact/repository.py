"""Repository input handling. plan.md §8, §9, §38.

Turns a GitHub URL or local path into an indexed `RepositorySession`: clone
if needed (strict URL validation, subprocess argument list — never
shell=True, never string-interpolated), then run the full index pipeline
(parser.py + storage.py). This module does not parse or analyze anything
itself.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from repoimpact.graph import CallGraph
from repoimpact.parser import parse_repository, resolve_references
from repoimpact.storage import Storage

GITHUB_URL_PATTERN = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(\.git)?/?$")

DEFAULT_DATA_DIR = Path("data") / "repositories"


class InvalidRepositoryError(ValueError):
    pass


@dataclass
class RepositorySession:
    repo_id: str
    root: Path
    storage: Storage
    graph: CallGraph

    def close(self) -> None:
        self.storage.close()

    def __enter__(self) -> "RepositorySession":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def is_github_url(source: str) -> bool:
    return GITHUB_URL_PATTERN.match(source.strip()) is not None


def _repo_id_for(identifier: str) -> str:
    return hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:16]


def _clone(url: str, dest: Path) -> None:
    if not is_github_url(url):
        raise InvalidRepositoryError(
            f"Only https://github.com/<owner>/<repo> URLs are supported, got: {url!r}"
        )
    if dest.exists():
        return  # already cloned for this repo id — cheap existence-based caching (§39)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)],
            shell=False,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise InvalidRepositoryError("git executable not found on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise InvalidRepositoryError(f"git clone failed for {url!r}: {(exc.stderr or str(exc)).strip()}") from exc


def open_repository(source: str, data_dir: Path = DEFAULT_DATA_DIR, reindex: bool = True) -> RepositorySession:
    """`source` is either a GitHub URL (`https://github.com/<owner>/<repo>`)
    or a local directory path."""
    source = source.strip()

    if is_github_url(source):
        repo_id = _repo_id_for(source)
        repo_dir = data_dir / repo_id
        root = repo_dir / "source"
        _clone(source, root)
    else:
        local_path = Path(source)
        if not local_path.is_dir():
            raise InvalidRepositoryError(f"Not a directory: {source!r}")
        root = local_path
        repo_id = _repo_id_for(str(local_path.resolve()))
        repo_dir = data_dir / repo_id

    repo_dir.mkdir(parents=True, exist_ok=True)
    storage = Storage(repo_dir / "repo.db")

    if reindex:
        parsed = parse_repository(root)
        references = resolve_references(parsed)
        storage.reindex(parsed, references)

    return RepositorySession(repo_id=repo_id, root=root, storage=storage, graph=CallGraph(storage))
