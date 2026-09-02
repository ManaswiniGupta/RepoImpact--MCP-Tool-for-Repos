from pathlib import Path

from repoimpact.graph import CallGraph
from repoimpact.parser import parse_repository, resolve_references
from repoimpact.search import find_symbol, get_source_context, search_code
from repoimpact.storage import Storage


def _write(root: Path, relpath: str, content: str) -> None:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _index(tmp_path: Path) -> tuple[Storage, CallGraph]:
    repo = parse_repository(tmp_path)
    refs = resolve_references(repo)
    storage = Storage(":memory:")
    storage.reindex(repo, refs)
    return storage, CallGraph(storage)


def _types(results) -> list[str]:
    return [r.match_type for r in results]


def test_exact_symbol_match_ranks_before_substring_match(tmp_path):
    _write(tmp_path, "auth.py", "def login():\n    pass\n\n\ndef login_helper():\n    pass\n")
    storage, _ = _index(tmp_path)
    results = search_code(storage, tmp_path, "login")

    assert results[0].match_type == "exact_symbol"
    assert results[0].name == "login"
    assert any(r.name == "login_helper" and r.match_type == "symbol_substring" for r in results)


def test_qualified_symbol_match(tmp_path):
    _write(tmp_path, "auth.py", "class UserService:\n    def login(self):\n        pass\n")
    storage, _ = _index(tmp_path)
    results = search_code(storage, tmp_path, "UserService.login")

    assert results[0].match_type == "qualified_symbol"
    assert results[0].qualified_name == "UserService.login"


def test_filename_match(tmp_path):
    _write(tmp_path, "routes.py", "def unrelated_name():\n    pass\n")
    storage, _ = _index(tmp_path)
    results = search_code(storage, tmp_path, "routes")

    assert any(r.match_type == "filename" and r.file_path == "routes.py" for r in results)


def test_source_text_fallback_finds_comment_text(tmp_path):
    _write(tmp_path, "payments.py", "def process():\n    # TODO handle duplicate callbacks\n    pass\n")
    storage, _ = _index(tmp_path)
    results = search_code(storage, tmp_path, "duplicate callbacks")

    assert len(results) == 1
    assert results[0].match_type == "source_text"
    assert results[0].file_path == "payments.py"
    assert results[0].line == 2
    assert "duplicate callbacks" in results[0].snippet


def test_limit_truncates_results(tmp_path):
    lines = "\n".join(f"def match_{i}():\n    pass\n" for i in range(10))
    _write(tmp_path, "many.py", lines)
    storage, _ = _index(tmp_path)
    results = search_code(storage, tmp_path, "match", limit=3)
    assert len(results) == 3


def test_directory_boost_overrides_alphabetical_order(tmp_path):
    _write(tmp_path, "alpha/helper.py", "def do_auth_thing():\n    pass\n")
    _write(tmp_path, "zulu_auth/handler.py", "def do_auth_thing():\n    pass\n")
    storage, _ = _index(tmp_path)
    results = search_code(storage, tmp_path, "auth")

    substring_matches = [r for r in results if r.match_type == "symbol_substring"]
    assert substring_matches[0].file_path == "zulu_auth/handler.py"
    assert substring_matches[1].file_path == "alpha/helper.py"


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


@app.post("/login")
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


def test_find_symbol_returns_callers_tests_and_entry_points(tmp_path):
    _demo_repo(tmp_path)
    storage, graph = _index(tmp_path)

    [detail] = find_symbol(storage, graph, "create_token")
    caller_names = {c.symbol["qualified_name"] for c in detail.callers}
    assert caller_names == {"login_endpoint", "test_create_token"}

    test_names = {c.symbol["qualified_name"] for c in detail.tests}
    assert test_names == {"test_create_token"}

    entry_point_names = {e["qualified_name"] for e in detail.entry_points}
    assert entry_point_names == {"login_endpoint"}


def test_find_symbol_self_method_caller(tmp_path):
    _demo_repo(tmp_path)
    storage, graph = _index(tmp_path)

    [detail] = find_symbol(storage, graph, "get_user")
    assert [c.symbol["qualified_name"] for c in detail.callers] == ["UserService.login"]
    assert detail.tests == []


def test_find_symbol_no_match_returns_empty(tmp_path):
    _demo_repo(tmp_path)
    storage, graph = _index(tmp_path)
    assert find_symbol(storage, graph, "does_not_exist") == []


def test_get_source_context_pads_and_clamps(tmp_path):
    content = "\n".join(f"line{i}" for i in range(1, 11))  # line1..line10
    _write(tmp_path, "file.py", content + "\n")

    ctx = get_source_context(tmp_path, "file.py", start_line=5, end_line=6, padding=2)
    assert ctx.start_line == 3
    assert ctx.end_line == 8
    assert ctx.lines[0] == (3, "line3")
    assert ctx.lines[-1] == (8, "line8")

    ctx_clamped = get_source_context(tmp_path, "file.py", start_line=1, end_line=1, padding=5)
    assert ctx_clamped.start_line == 1
    assert ctx_clamped.lines[0] == (1, "line1")
