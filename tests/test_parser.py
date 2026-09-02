from pathlib import Path

from repoimpact.models import Confidence, ResolutionKind, SymbolType
from repoimpact.parser import parse_repository, resolve_references


def _write(root: Path, relpath: str, content: str) -> None:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _symbol(repo, qualified_name):
    for f in repo.files:
        for s in f.symbols:
            if s.qualified_name == qualified_name:
                return s
    return None


def _reference_at(refs, file_path, line):
    for r in refs:
        if r.file_path == file_path and r.line == line:
            return r
    raise AssertionError(f"no reference at {file_path}:{line}")


# --------------------------------------------------------------------------
# Extraction: functions, classes, imports, calls, decorators (plan.md §42)
# --------------------------------------------------------------------------


def test_extracts_top_level_function_and_class_with_method(tmp_path):
    _write(
        tmp_path,
        "auth.py",
        """
def create_token():
    pass


class UserService:
    def get_user(self):
        pass
""",
    )
    repo = parse_repository(tmp_path)
    assert repo.errors == []

    fn = _symbol(repo, "create_token")
    assert fn.type == SymbolType.FUNCTION
    assert fn.parent_qualified_name is None
    assert fn.start_line == 2

    cls = _symbol(repo, "UserService")
    assert cls.type == SymbolType.CLASS

    method = _symbol(repo, "UserService.get_user")
    assert method.type == SymbolType.METHOD
    assert method.parent_qualified_name == "UserService"


def test_extracts_async_function(tmp_path):
    _write(tmp_path, "svc.py", "async def fetch():\n    pass\n")
    repo = parse_repository(tmp_path)
    fn = _symbol(repo, "fetch")
    assert fn.type == SymbolType.FUNCTION


def test_extracts_imports_with_aliases(tmp_path):
    _write(
        tmp_path,
        "routes.py",
        """
import auth as a
from auth import create_token as token
""",
    )
    repo = parse_repository(tmp_path)
    imports = repo.files[0].imports
    assert any(i.module == "auth" and i.local_name == "a" and i.imported_name is None for i in imports)
    assert any(i.module == "auth" and i.local_name == "token" and i.imported_name == "create_token" for i in imports)


def test_extracts_decorators_and_entry_point(tmp_path):
    _write(
        tmp_path,
        "routes.py",
        """
class Router:
    @staticmethod
    @app.post("/login")
    def login_endpoint():
        pass
""",
    )
    repo = parse_repository(tmp_path)
    symbol = _symbol(repo, "Router.login_endpoint")
    assert any("staticmethod" in d for d in symbol.decorators)
    assert symbol.entry_point is not None
    assert symbol.entry_point.method == "POST"
    assert symbol.entry_point.route == "/login"


def test_decorator_expression_not_attributed_to_decorated_function(tmp_path):
    _write(
        tmp_path,
        "routes.py",
        """
class Router:
    def post(self, path):
        def decorator(fn):
            return fn
        return decorator


router = Router()


@router.post("/login")
def login_endpoint():
    pass
""",
    )
    repo = parse_repository(tmp_path)
    refs = resolve_references(repo)
    # The decorator's own call (`router.post(...)`) must not appear as a
    # call made *by* login_endpoint — it runs in the enclosing (module)
    # scope, before login_endpoint's body scope even exists.
    login_endpoint_calls = [r for r in refs if r.source_qualified_name == "login_endpoint"]
    assert login_endpoint_calls == []


def test_decorator_call_resolves_in_enclosing_scope(tmp_path):
    _write(
        tmp_path,
        "routes.py",
        """
class Router:
    def post(self, path):
        def decorator(fn):
            return fn
        return decorator


router = Router()


@router.post("/login")
def login_endpoint():
    pass
""",
    )
    repo = parse_repository(tmp_path)
    refs = resolve_references(repo)
    # The decorator call is attributed to module scope instead, and is
    # simply not persisted as a reference edge (§16 — module-level call
    # sites aren't part of the function-level call graph); the important
    # assertion is the one above. This test just documents where it went.
    module_scope_calls = [r for r in refs if r.source_qualified_name == "<module>"]
    assert any(r.line for r in module_scope_calls)


def test_is_test_detection_by_function_name(tmp_path):
    _write(tmp_path, "checks.py", "def test_login():\n    pass\n\ndef helper():\n    pass\n")
    repo = parse_repository(tmp_path)
    assert _symbol(repo, "test_login").is_test is True
    assert _symbol(repo, "helper").is_test is False


def test_is_test_detection_by_file_name(tmp_path):
    _write(tmp_path, "tests/test_auth.py", "def check_something():\n    pass\n")
    repo = parse_repository(tmp_path)
    assert _symbol(repo, "check_something").is_test is True


def test_scanner_ignores_venv_and_pycache(tmp_path):
    _write(tmp_path, "app.py", "def main():\n    pass\n")
    _write(tmp_path, ".venv/lib/thing.py", "def ignored():\n    pass\n")
    _write(tmp_path, "__pycache__/cached.py", "def ignored():\n    pass\n")
    repo = parse_repository(tmp_path)
    paths = {f.source_file.path for f in repo.files}
    assert paths == {"app.py"}


def test_malformed_python_is_skipped_not_fatal(tmp_path):
    _write(tmp_path, "broken.py", "def foo(:\n")
    _write(tmp_path, "good.py", "def bar():\n    pass\n")
    repo = parse_repository(tmp_path)
    assert len(repo.files) == 1
    assert repo.files[0].source_file.path == "good.py"
    assert any("broken.py" in e for e in repo.errors)


# --------------------------------------------------------------------------
# Resolution — plan.md §15 fixed order
# --------------------------------------------------------------------------


def test_resolves_same_module_call(tmp_path):
    _write(
        tmp_path,
        "auth.py",
        """
def validate():
    pass


def login():
    validate()
""",
    )
    repo = parse_repository(tmp_path)
    refs = resolve_references(repo)
    ref = _reference_at(refs, "auth.py", 7)
    assert ref.resolution == ResolutionKind.SAME_MODULE
    assert ref.confidence == Confidence.HIGH
    assert ref.target_qualified_name == "validate"


def test_resolves_self_method_call(tmp_path):
    _write(
        tmp_path,
        "auth.py",
        """
class UserService:
    def get_user(self):
        pass

    def login(self):
        self.get_user()
""",
    )
    repo = parse_repository(tmp_path)
    refs = resolve_references(repo)
    ref = _reference_at(refs, "auth.py", 7)
    assert ref.resolution == ResolutionKind.SELF_METHOD
    assert ref.confidence == Confidence.HIGH
    assert ref.target_qualified_name == "UserService.get_user"


def test_self_method_missing_is_unresolved(tmp_path):
    _write(
        tmp_path,
        "auth.py",
        """
class UserService:
    def login(self):
        self.does_not_exist()
""",
    )
    repo = parse_repository(tmp_path)
    refs = resolve_references(repo)
    ref = _reference_at(refs, "auth.py", 4)
    assert ref.confidence == Confidence.UNRESOLVED
    assert ref.target_qualified_name is None


def test_resolves_imported_symbol_call(tmp_path):
    _write(tmp_path, "auth.py", "def create_token():\n    pass\n")
    _write(
        tmp_path,
        "routes.py",
        """
from auth import create_token


def login():
    create_token()
""",
    )
    repo = parse_repository(tmp_path)
    refs = resolve_references(repo)
    ref = _reference_at(refs, "routes.py", 6)
    assert ref.resolution == ResolutionKind.IMPORTED_SYMBOL
    assert ref.confidence == Confidence.HIGH
    assert ref.target_file_path == "auth.py"
    assert ref.target_qualified_name == "create_token"


def test_external_import_is_unresolved(tmp_path):
    _write(
        tmp_path,
        "routes.py",
        """
from external_pkg import do_thing


def login():
    do_thing()
""",
    )
    repo = parse_repository(tmp_path)
    refs = resolve_references(repo)
    ref = _reference_at(refs, "routes.py", 6)
    assert ref.confidence == Confidence.UNRESOLVED


def test_resolves_qualified_module_call(tmp_path):
    _write(tmp_path, "auth.py", "def create_token():\n    pass\n")
    _write(
        tmp_path,
        "routes.py",
        """
import auth


def login():
    auth.create_token()
""",
    )
    repo = parse_repository(tmp_path)
    refs = resolve_references(repo)
    ref = _reference_at(refs, "routes.py", 6)
    assert ref.resolution == ResolutionKind.QUALIFIED_MODULE
    assert ref.confidence == Confidence.HIGH
    assert ref.target_file_path == "auth.py"
    assert ref.target_qualified_name == "create_token"


def test_resolves_instance_assignment_call(tmp_path):
    _write(
        tmp_path,
        "auth.py",
        """
class UserService:
    def get_user(self):
        pass


def login():
    service = UserService()
    service.get_user()
""",
    )
    repo = parse_repository(tmp_path)
    refs = resolve_references(repo)
    ref = _reference_at(refs, "auth.py", 9)
    assert ref.resolution == ResolutionKind.INSTANCE_ASSIGNMENT
    assert ref.confidence == Confidence.HIGH
    assert ref.target_qualified_name == "UserService.get_user"


def test_resolves_instance_assignment_with_imported_class(tmp_path):
    _write(
        tmp_path,
        "auth.py",
        """
class UserService:
    def get_user(self):
        pass
""",
    )
    _write(
        tmp_path,
        "routes.py",
        """
from auth import UserService


def login():
    service = UserService()
    service.get_user()
""",
    )
    repo = parse_repository(tmp_path)
    refs = resolve_references(repo)
    ref = _reference_at(refs, "routes.py", 7)
    assert ref.resolution == ResolutionKind.INSTANCE_ASSIGNMENT
    assert ref.confidence == Confidence.HIGH
    assert ref.target_file_path == "auth.py"
    assert ref.target_qualified_name == "UserService.get_user"


def test_resolves_module_level_instance_assignment_used_inside_function(tmp_path):
    _write(
        tmp_path,
        "auth.py",
        """
class UserService:
    def get_user(self):
        pass


service = UserService()


def login():
    service.get_user()
""",
    )
    repo = parse_repository(tmp_path)
    refs = resolve_references(repo)
    ref = _reference_at(refs, "auth.py", 11)
    assert ref.resolution == ResolutionKind.INSTANCE_ASSIGNMENT
    assert ref.target_qualified_name == "UserService.get_user"


def test_unique_bare_name_fallback(tmp_path):
    _write(tmp_path, "payments.py", "def process():\n    pass\n")
    _write(
        tmp_path,
        "unrelated.py",
        """
def handler():
    process()
""",
    )
    repo = parse_repository(tmp_path)
    refs = resolve_references(repo)
    ref = _reference_at(refs, "unrelated.py", 3)
    assert ref.resolution == ResolutionKind.NAME_FALLBACK
    assert ref.confidence == Confidence.LOW
    assert ref.target_qualified_name == "process"


def test_ambiguous_bare_name_is_unresolved(tmp_path):
    _write(tmp_path, "a.py", "def process():\n    pass\n")
    _write(tmp_path, "b.py", "def process():\n    pass\n")
    _write(
        tmp_path,
        "unrelated.py",
        """
def handler():
    process()
""",
    )
    repo = parse_repository(tmp_path)
    refs = resolve_references(repo)
    ref = _reference_at(refs, "unrelated.py", 3)
    assert ref.confidence == Confidence.UNRESOLVED
    assert ref.target_qualified_name is None


def test_test_function_calling_target_is_ordinary_call_graph_edge(tmp_path):
    _write(tmp_path, "auth.py", "def login():\n    pass\n")
    _write(
        tmp_path,
        "tests/test_auth.py",
        """
from auth import login


def test_login():
    login()
""",
    )
    repo = parse_repository(tmp_path)
    refs = resolve_references(repo)
    ref = _reference_at(refs, "tests/test_auth.py", 6)
    assert ref.resolution == ResolutionKind.IMPORTED_SYMBOL
    assert ref.target_qualified_name == "login"
    test_symbol = _symbol(repo, "test_login")
    assert test_symbol.is_test is True
