import sqlite3
from pathlib import Path

import pytest

from repoimpact.models import ParsedFile, SourceFile
from repoimpact.parser import parse_repository, resolve_references
from repoimpact.storage import Storage


def _write(root: Path, relpath: str, content: str) -> None:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _index(tmp_path: Path) -> Storage:
    repo = parse_repository(tmp_path)
    refs = resolve_references(repo)
    storage = Storage(":memory:")
    storage.reindex(repo, refs)
    return storage


def _demo_repo(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "auth.py",
        """
class UserService:
    def get_user(self):
        pass

    def login(self):
        self.get_user()


def create_token():
    pass
""",
    )
    _write(
        tmp_path,
        "routes.py",
        """
from auth import create_token


def login_endpoint():
    create_token()
""",
    )
    _write(
        tmp_path,
        "tests/test_auth.py",
        """
from auth import create_token


def test_create_token():
    create_token()
""",
    )


def test_reindex_inserts_files_symbols_references(tmp_path):
    _demo_repo(tmp_path)
    storage = _index(tmp_path)

    files = storage.list_files()
    assert {f["path"] for f in files} == {"auth.py", "routes.py", "tests/test_auth.py"}

    symbols = storage.list_symbols()
    names = {s["qualified_name"] for s in symbols}
    assert "UserService" in names
    assert "UserService.get_user" in names
    assert "create_token" in names

    refs = storage.list_references()
    assert len(refs) >= 3  # self.get_user(), create_token() x2


def test_parent_id_set_for_methods(tmp_path):
    _demo_repo(tmp_path)
    storage = _index(tmp_path)

    cls = storage.get_symbol("auth.py", "UserService")
    method = storage.get_symbol("auth.py", "UserService.get_user")
    assert method["parent_id"] == cls["id"]


def test_is_test_flag_persisted(tmp_path):
    _demo_repo(tmp_path)
    storage = _index(tmp_path)

    test_symbol = storage.get_symbol("tests/test_auth.py", "test_create_token")
    assert test_symbol["is_test"] == 1

    non_test = storage.get_symbol("auth.py", "create_token")
    assert non_test["is_test"] == 0


def test_confidence_and_resolution_persisted(tmp_path):
    _demo_repo(tmp_path)
    storage = _index(tmp_path)

    method = storage.get_symbol("auth.py", "UserService.get_user")
    incoming = storage.references_to(method["id"])
    assert len(incoming) == 1
    assert incoming[0]["confidence"] == "HIGH"
    assert incoming[0]["resolution"] == "self_method"


def test_references_from_and_to(tmp_path):
    _demo_repo(tmp_path)
    storage = _index(tmp_path)

    create_token = storage.get_symbol("auth.py", "create_token")
    callers = storage.references_to(create_token["id"])
    assert len(callers) == 2  # routes.login_endpoint + tests.test_create_token

    login_endpoint = storage.get_symbol("routes.py", "login_endpoint")
    callees = storage.references_from(login_endpoint["id"])
    assert len(callees) == 1
    assert callees[0]["target_symbol_id"] == create_token["id"]


def test_find_symbols_by_name(tmp_path):
    _demo_repo(tmp_path)
    storage = _index(tmp_path)

    matches = storage.find_symbols_by_name("create_token")
    assert len(matches) == 1
    assert matches[0]["qualified_name"] == "create_token"


def test_reindex_twice_does_not_duplicate(tmp_path):
    _demo_repo(tmp_path)
    repo = parse_repository(tmp_path)
    refs = resolve_references(repo)

    storage = Storage(":memory:")
    storage.reindex(repo, refs)
    first_files = len(storage.list_files())
    first_symbols = len(storage.list_symbols())
    first_refs = len(storage.list_references())

    storage.reindex(repo, refs)
    assert len(storage.list_files()) == first_files
    assert len(storage.list_symbols()) == first_symbols
    assert len(storage.list_references()) == first_refs


def test_reindex_rolls_back_on_failure_and_keeps_previous_index(tmp_path):
    _demo_repo(tmp_path)
    repo = parse_repository(tmp_path)
    refs = resolve_references(repo)

    storage = Storage(":memory:")
    storage.reindex(repo, refs)
    good_file_count = len(storage.list_files())
    good_symbol_count = len(storage.list_symbols())

    # Craft a broken repository: two files sharing the same path, which
    # violates the UNIQUE constraint on files.path mid-transaction.
    broken = ParsedFile(source_file=SourceFile(path="dup.py", file_hash="a", size=1))
    broken2 = ParsedFile(source_file=SourceFile(path="dup.py", file_hash="b", size=1))
    repo.files.append(broken)
    repo.files.append(broken2)

    with pytest.raises(sqlite3.IntegrityError):
        storage.reindex(repo, refs)

    assert len(storage.list_files()) == good_file_count
    assert len(storage.list_symbols()) == good_symbol_count


def test_module_level_call_site_is_not_persisted_as_reference(tmp_path):
    _write(
        tmp_path,
        "auth.py",
        """
class UserService:
    def get_user(self):
        pass


service = UserService()
""",
    )
    storage = _index(tmp_path)
    refs = storage.list_references()
    assert refs == []
