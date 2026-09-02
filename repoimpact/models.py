"""Data models for RepoImpact.

Plain dataclasses only (plan.md §12) — no ORM, no dozens of abstractions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SymbolType(str, Enum):
    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"


class Confidence(str, Enum):
    """plan.md §15"""

    HIGH = "HIGH"
    LOW = "LOW"
    UNRESOLVED = "UNRESOLVED"


class ResolutionKind(str, Enum):
    """How a reference edge was produced. plan.md §13, §15."""

    SAME_MODULE = "same_module"
    SELF_METHOD = "self_method"
    IMPORTED_SYMBOL = "imported_symbol"
    INSTANCE_ASSIGNMENT = "instance_assignment"
    QUALIFIED_MODULE = "qualified_module"
    NAME_FALLBACK = "name_fallback"


@dataclass(frozen=True)
class SourceFile:
    path: str  # repo-relative, forward slashes
    file_hash: str
    size: int


@dataclass(frozen=True)
class EntryPoint:
    """Detected API route. plan.md §20."""

    method: str  # HTTP method, e.g. "GET"
    route: str
    symbol_qualified_name: str


@dataclass
class Symbol:
    file_path: str
    name: str
    qualified_name: str
    type: SymbolType
    start_line: int
    end_line: int
    parent_qualified_name: str | None = None
    is_test: bool = False
    decorators: list[str] = field(default_factory=list)
    entry_point: EntryPoint | None = None


@dataclass(frozen=True)
class ImportBinding:
    """A name bound into a file's namespace via import.

    ``import auth``            -> module="auth", imported_name=None, local_name="auth"
    ``import auth as a``       -> module="auth", imported_name=None, local_name="a"
    ``from auth import login`` -> module="auth", imported_name="login", local_name="login"
    ``from auth import login as l`` -> module="auth", imported_name="login", local_name="l"
    """

    file_path: str
    module: str
    imported_name: str | None
    local_name: str
    line: int


@dataclass(frozen=True)
class InstanceAssignment:
    """A direct ``x = ClassName()`` binding. plan.md §15 step 4.

    Scope is the enclosing function's qualified name, or ``"<module>"``.
    """

    file_path: str
    scope_qualified_name: str
    variable_name: str
    class_name: str  # name as written at the call site, not yet resolved
    line: int


@dataclass(frozen=True)
class RawCall:
    """An unresolved call expression extracted straight from the AST."""

    file_path: str
    caller_qualified_name: str  # enclosing function/method, or "<module>"
    caller_class_qualified_name: str | None  # enclosing class, if any
    callee_expr: str  # e.g. "foo", "self.foo", "obj.foo", "auth.foo"
    line: int


@dataclass
class Reference:
    source_qualified_name: str
    file_path: str  # location of the call site == source symbol's file
    line: int
    kind: str  # currently always "call"
    target_file_path: str | None
    target_qualified_name: str | None
    resolution: ResolutionKind | None
    confidence: Confidence


@dataclass
class ParsedFile:
    """Everything extracted from a single file, before cross-file resolution."""

    source_file: SourceFile
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[ImportBinding] = field(default_factory=list)
    instance_assignments: list[InstanceAssignment] = field(default_factory=list)
    raw_calls: list[RawCall] = field(default_factory=list)


@dataclass
class ParsedRepository:
    files: list[ParsedFile] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
