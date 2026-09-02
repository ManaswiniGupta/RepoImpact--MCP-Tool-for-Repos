"""Python source parser and reference resolver.

Extraction (this module's first half) turns a repository's `.py` files into
raw `ParsedFile` records using the stdlib `ast` module: symbols, imports,
instance assignments, and unresolved call expressions.

Resolution (`resolve_references`) then turns those raw call expressions into
`Reference` edges using the fixed order defined in plan.md §15. Do not add
resolution steps beyond that list.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path

from repoimpact.models import (
    Confidence,
    EntryPoint,
    ImportBinding,
    InstanceAssignment,
    ParsedFile,
    ParsedRepository,
    RawCall,
    Reference,
    ResolutionKind,
    SourceFile,
    Symbol,
    SymbolType,
)

MODULE_SCOPE = "<module>"

IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
}

DEFAULT_MAX_FILE_SIZE = 2_000_000  # bytes

_ENTRY_POINT_METHODS = {"get", "post", "put", "delete", "patch"}


# --------------------------------------------------------------------------
# File scanning
# --------------------------------------------------------------------------


def find_python_files(root: Path) -> list[Path]:
    """Recursively find `.py` files under `root`, skipping ignored directories."""
    results: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        if any(part in IGNORED_DIRS for part in path.relative_to(root).parts[:-1]):
            continue
        results.append(path)
    return results


def compute_file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------


def _relative_posix(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _attr_chain_text(node: ast.expr) -> str | None:
    """Render a `Name`/`Attribute` chain as dotted text, or None if not one."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _attr_chain_text(node.value)
        return f"{base}.{node.attr}" if base is not None else None
    return None


def _is_test_file(relative_path: str) -> bool:
    name = Path(relative_path).name
    return name.startswith("test_") or name.endswith("_test.py")


def _decorator_text(node: ast.expr) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "<decorator>"


def _detect_entry_point(decorators: list[ast.expr], qualified_name: str) -> EntryPoint | None:
    for dec in decorators:
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in _ENTRY_POINT_METHODS:
            continue
        route = None
        if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
            route = dec.args[0].value
        if route is None:
            continue
        return EntryPoint(method=func.attr.upper(), route=route, symbol_qualified_name=qualified_name)
    return None


@dataclass
class _Scope:
    qualified_name: str
    class_qualified_name: str | None  # nearest enclosing class, if any


class _FileVisitor(ast.NodeVisitor):
    """Walks one file's AST, tracking a lexical scope stack."""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.symbols: list[Symbol] = []
        self.imports: list[ImportBinding] = []
        self.instance_assignments: list[InstanceAssignment] = []
        self.raw_calls: list[RawCall] = []
        self._scope_stack: list[_Scope] = [_Scope(MODULE_SCOPE, None)]

    @property
    def _current(self) -> _Scope:
        return self._scope_stack[-1]

    def _qualify(self, name: str) -> str:
        if self._current.qualified_name == MODULE_SCOPE:
            return name
        return f"{self._current.qualified_name}.{name}"

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local_name = alias.asname or alias.name.split(".")[0]
            self.imports.append(
                ImportBinding(
                    file_path=self.file_path,
                    module=alias.name,
                    imported_name=None,
                    local_name=local_name,
                    line=node.lineno,
                )
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = "." * node.level + (node.module or "")
        for alias in node.names:
            local_name = alias.asname or alias.name
            self.imports.append(
                ImportBinding(
                    file_path=self.file_path,
                    module=module,
                    imported_name=alias.name,
                    local_name=local_name,
                    line=node.lineno,
                )
            )
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified_name = self._qualify(node.name)
        decorators = [_decorator_text(d) for d in node.decorator_list]
        self.symbols.append(
            Symbol(
                file_path=self.file_path,
                name=node.name,
                qualified_name=qualified_name,
                type=SymbolType.CLASS,
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
                parent_qualified_name=self._current.class_qualified_name
                if self._current.qualified_name != MODULE_SCOPE
                else None,
                decorators=decorators,
            )
        )
        # Decorator expressions and base-class expressions run in the
        # *enclosing* scope, not inside the class body — visit them before
        # pushing the class's own scope, or a decorator like
        # `@app.route(...)` gets misattributed as a call made by the class.
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)

        self._scope_stack.append(_Scope(qualified_name, class_qualified_name=qualified_name))
        for stmt in node.body:
            self.visit(stmt)
        self._scope_stack.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualified_name = self._qualify(node.name)
        enclosing_class = self._current.class_qualified_name
        is_method = enclosing_class is not None and self._current.qualified_name == enclosing_class
        symbol_type = SymbolType.METHOD if is_method else SymbolType.FUNCTION
        decorators = [_decorator_text(d) for d in node.decorator_list]
        entry_point = _detect_entry_point(node.decorator_list, qualified_name)
        self.symbols.append(
            Symbol(
                file_path=self.file_path,
                name=node.name,
                qualified_name=qualified_name,
                type=symbol_type,
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
                parent_qualified_name=enclosing_class if is_method else None,
                is_test=node.name.startswith("test_"),
                decorators=decorators,
                entry_point=entry_point,
            )
        )
        # Decorators and default argument values run in the *enclosing*
        # scope, not inside the function body — visit them before pushing
        # this function's own scope (same reasoning as visit_ClassDef).
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)

        # Functions/methods open a new scope; a class nested inside a
        # function is not itself a "method", so class_qualified_name does
        # not carry into a function's body unless that function IS the
        # class's own method scope (handled by enclosing_class already).
        self._scope_stack.append(_Scope(qualified_name, class_qualified_name=enclosing_class if is_method else None))
        for stmt in node.body:
            self.visit(stmt)
        self._scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if (
            len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Call)
        ):
            class_name = _attr_chain_text(node.value.func)
            if class_name is not None:
                self.instance_assignments.append(
                    InstanceAssignment(
                        file_path=self.file_path,
                        scope_qualified_name=self._current.qualified_name,
                        variable_name=node.targets[0].id,
                        class_name=class_name,
                        line=node.lineno,
                    )
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        callee_expr = _attr_chain_text(node.func)
        if callee_expr is not None:
            self.raw_calls.append(
                RawCall(
                    file_path=self.file_path,
                    caller_qualified_name=self._current.qualified_name,
                    caller_class_qualified_name=self._current.class_qualified_name,
                    callee_expr=callee_expr,
                    line=node.lineno,
                )
            )
        self.generic_visit(node)


def parse_file(root: Path, path: Path, source: str) -> ParsedFile:
    relative_path = _relative_posix(root, path)
    tree = ast.parse(source, filename=relative_path)
    visitor = _FileVisitor(relative_path)
    visitor.visit(tree)

    is_test_file = _is_test_file(relative_path)
    if is_test_file:
        for symbol in visitor.symbols:
            if symbol.type in (SymbolType.FUNCTION, SymbolType.METHOD):
                symbol.is_test = True

    source_file = SourceFile(
        path=relative_path,
        file_hash=compute_file_hash(source.encode("utf-8")),
        size=len(source.encode("utf-8")),
    )
    return ParsedFile(
        source_file=source_file,
        symbols=visitor.symbols,
        imports=visitor.imports,
        instance_assignments=visitor.instance_assignments,
        raw_calls=visitor.raw_calls,
    )


def parse_repository(root: Path, max_file_size: int = DEFAULT_MAX_FILE_SIZE) -> ParsedRepository:
    repo = ParsedRepository()
    for path in find_python_files(root):
        try:
            if path.stat().st_size > max_file_size:
                repo.errors.append(f"{path}: skipped (exceeds max file size)")
                continue
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            repo.errors.append(f"{path}: skipped (not valid UTF-8 / binary)")
            continue
        except OSError as exc:
            repo.errors.append(f"{path}: skipped ({exc})")
            continue

        try:
            repo.files.append(parse_file(root, path, source))
        except SyntaxError as exc:
            repo.errors.append(f"{path}: skipped (malformed Python: {exc})")
    return repo


# --------------------------------------------------------------------------
# Resolution — plan.md §15. Fixed order. Do not add steps.
# --------------------------------------------------------------------------


def _module_name_for(relative_path: str) -> str:
    p = Path(relative_path)
    if p.name == "__init__.py":
        parts = p.parent.parts
    else:
        parts = p.with_suffix("").parts
    return ".".join(parts)


class _Index:
    """Lookup structures built once per repository, used by every resolution step."""

    def __init__(self, repo: ParsedRepository):
        self.symbols_by_file: dict[str, dict[str, Symbol]] = {}
        self.symbols_by_name: dict[str, list[Symbol]] = {}
        self.imports_by_file: dict[str, dict[str, ImportBinding]] = {}
        self.instance_assignments_by_file: dict[str, dict[tuple[str, str], InstanceAssignment]] = {}
        self.module_to_file: dict[str, str] = {}

        for parsed_file in repo.files:
            file_path = parsed_file.source_file.path
            self.module_to_file[_module_name_for(file_path)] = file_path

            by_qname = self.symbols_by_file.setdefault(file_path, {})
            for symbol in parsed_file.symbols:
                by_qname[symbol.qualified_name] = symbol
                self.symbols_by_name.setdefault(symbol.name, []).append(symbol)

            by_local = self.imports_by_file.setdefault(file_path, {})
            for binding in parsed_file.imports:
                by_local[binding.local_name] = binding

            by_scope_var = self.instance_assignments_by_file.setdefault(file_path, {})
            for assignment in parsed_file.instance_assignments:
                by_scope_var[(assignment.scope_qualified_name, assignment.variable_name)] = assignment

    def top_level_symbol(self, file_path: str, name: str) -> Symbol | None:
        symbol = self.symbols_by_file.get(file_path, {}).get(name)
        if symbol is not None and symbol.parent_qualified_name is None:
            return symbol
        return None

    def method_symbol(self, file_path: str, class_qualified_name: str, method_name: str) -> Symbol | None:
        symbol = self.symbols_by_file.get(file_path, {}).get(f"{class_qualified_name}.{method_name}")
        if symbol is not None and symbol.type == SymbolType.METHOD:
            return symbol
        return None

    def class_symbol(self, file_path: str, class_name: str) -> Symbol | None:
        symbol = self.symbols_by_file.get(file_path, {}).get(class_name)
        if symbol is not None and symbol.type == SymbolType.CLASS:
            return symbol
        return None

    def resolve_class_name(self, file_path: str, class_name_text: str) -> Symbol | None:
        """Resolve a class name written at an instance-assignment call site.

        Bounded to: same-file class, or a class reached through one import
        hop. No whole-repo name fallback here — an ambiguous class means the
        whole instance-assignment step is unresolved, not a guess.
        """
        base, _, attr = class_name_text.rpartition(".")
        if not base:
            local = self.class_symbol(file_path, class_name_text)
            if local is not None:
                return local
            binding = self.imports_by_file.get(file_path, {}).get(class_name_text)
            if binding is None:
                return None
            target_file = self.module_to_file.get(binding.module)
            if target_file is None:
                return None
            lookup_name = binding.imported_name or class_name_text
            return self.class_symbol(target_file, lookup_name)
        # `module.ClassName()` form.
        binding = self.imports_by_file.get(file_path, {}).get(base)
        if binding is None or binding.imported_name is not None:
            return None
        target_file = self.module_to_file.get(binding.module)
        if target_file is None:
            return None
        return self.class_symbol(target_file, attr)

    def unique_by_name(self, name: str) -> Symbol | None:
        candidates = self.symbols_by_name.get(name, [])
        if len(candidates) == 1:
            return candidates[0]
        return None


def _resolve_bare_call(call: RawCall, index: _Index) -> Reference:
    name = call.callee_expr

    symbol = index.top_level_symbol(call.file_path, name)
    if symbol is not None:
        return _reference(call, symbol, ResolutionKind.SAME_MODULE, Confidence.HIGH)

    binding = index.imports_by_file.get(call.file_path, {}).get(name)
    if binding is not None and binding.imported_name is not None:
        target_file = index.module_to_file.get(binding.module)
        if target_file is not None:
            target = index.top_level_symbol(target_file, binding.imported_name)
            if target is not None:
                return _reference(call, target, ResolutionKind.IMPORTED_SYMBOL, Confidence.HIGH)
        return _unresolved(call)

    fallback = index.unique_by_name(name)
    if fallback is not None:
        return _reference(call, fallback, ResolutionKind.NAME_FALLBACK, Confidence.LOW)

    return _unresolved(call)


def _resolve_attribute_call(call: RawCall, index: _Index) -> Reference:
    base, _, attr = call.callee_expr.rpartition(".")

    if base == "self":
        if call.caller_class_qualified_name is not None:
            symbol = index.method_symbol(call.file_path, call.caller_class_qualified_name, attr)
            if symbol is not None:
                return _reference(call, symbol, ResolutionKind.SELF_METHOD, Confidence.HIGH)
        return _unresolved(call)

    assignment = index.instance_assignments_by_file.get(call.file_path, {}).get(
        (call.caller_qualified_name, base)
    )
    if assignment is None:
        assignment = index.instance_assignments_by_file.get(call.file_path, {}).get((MODULE_SCOPE, base))
    if assignment is not None:
        class_symbol = index.resolve_class_name(call.file_path, assignment.class_name)
        if class_symbol is not None:
            method = index.method_symbol(call.file_path, class_symbol.qualified_name, attr)
            if method is None and class_symbol.file_path != call.file_path:
                method = index.method_symbol(class_symbol.file_path, class_symbol.qualified_name, attr)
            if method is not None:
                return _reference(call, method, ResolutionKind.INSTANCE_ASSIGNMENT, Confidence.HIGH)
        return _unresolved(call)

    binding = index.imports_by_file.get(call.file_path, {}).get(base)
    if binding is not None and binding.imported_name is None:
        target_file = index.module_to_file.get(binding.module)
        if target_file is not None:
            symbol = index.top_level_symbol(target_file, attr)
            if symbol is not None:
                return _reference(call, symbol, ResolutionKind.QUALIFIED_MODULE, Confidence.HIGH)
        return _unresolved(call)

    fallback = index.unique_by_name(attr)
    if fallback is not None:
        return _reference(call, fallback, ResolutionKind.NAME_FALLBACK, Confidence.LOW)

    return _unresolved(call)


def _reference(call: RawCall, target: Symbol, resolution: ResolutionKind, confidence: Confidence) -> Reference:
    return Reference(
        source_qualified_name=call.caller_qualified_name,
        file_path=call.file_path,
        line=call.line,
        kind="call",
        target_file_path=target.file_path,
        target_qualified_name=target.qualified_name,
        resolution=resolution,
        confidence=confidence,
    )


def _unresolved(call: RawCall) -> Reference:
    return Reference(
        source_qualified_name=call.caller_qualified_name,
        file_path=call.file_path,
        line=call.line,
        kind="call",
        target_file_path=None,
        target_qualified_name=None,
        resolution=None,
        confidence=Confidence.UNRESOLVED,
    )


def resolve_references(repo: ParsedRepository) -> list[Reference]:
    index = _Index(repo)
    references: list[Reference] = []
    for parsed_file in repo.files:
        for call in parsed_file.raw_calls:
            if "." in call.callee_expr:
                references.append(_resolve_attribute_call(call, index))
            else:
                references.append(_resolve_bare_call(call, index))
    return references
