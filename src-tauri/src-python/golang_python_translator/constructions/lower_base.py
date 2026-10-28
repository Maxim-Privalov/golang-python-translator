"""Этап 2 (основа): состояние лоуэринга, типы аннотаций и нулевые значения."""
import ast
from typing import Any, Callable, List, Optional, Set

from golang_python_translator.constructions import pyast as P
from golang_python_translator.constructions.gotypes import (
    ARRAY_RE, COMPLEXES, FLOATS, INTS, py_ident, split_map, split_tuple,
)
from golang_python_translator.constructions.symbols import Symbols


def gt(node: Any) -> Optional[str]:
    """Тип Go, проставленный анализатором."""
    return getattr(node, "go_type", None)


class LowerBase:
    def __init__(self, sym: Symbols, warn: Callable[[str], None]) -> None:
        self.sym = sym
        self.T = sym.types
        self.warn = warn
        self.helpers: Set[str] = set()
        self.imports: Set[str] = set()
        self.typing: Set[str] = set()
        self.need_dataclass = False
        self.contexts: List[str] = []       # "loop" / "switch" - для break
        self.loop_posts: List[Any] = []     # post-инструкции for - для continue
        self.cur_ret: Optional[str] = None
        self._tmp = 0

    def use(self, helper: str) -> None:
        self.helpers.add(helper)

    def imp(self, module: str) -> None:
        self.imports.add(module)

    def tmp(self, prefix: str) -> str:
        self._tmp += 1
        return f"_{prefix}{self._tmp}"

    # ------------------------------------------------------------ типы
    def pytype(self, t: Optional[str]) -> str:
        if t is None:
            self.typing.add("Any")
            return "Any"
        if t.startswith("*"):
            self.typing.add("Optional")
            return f"Optional[{self.pytype(t[1:])}]"
        if t.startswith("chan "):
            self.typing.add("Any")
            return "Any"
        parts = split_tuple(t)
        if parts is not None:
            self.typing.add("Tuple")
            return "Tuple[" + ", ".join(self.pytype(p) for p in parts) + "]"
        if t.startswith("[]"):
            self.typing.add("List")
            return f"List[{self.pytype(t[2:])}]"
        m = ARRAY_RE.match(t)
        if m:
            self.typing.add("List")
            return f"List[{self.pytype(m.group(2))}]"
        if t.startswith("map["):
            k, v = split_map(t)
            self.typing.add("Dict")
            return f"Dict[{self.pytype(k)}, {self.pytype(v)}]"
        if t in INTS:
            return "int"
        if t in FLOATS:
            return "float"
        if t in COMPLEXES:
            return "complex"
        if t == "string":
            return "str"
        if t == "bool":
            return "bool"
        if t == "error":
            self.typing.add("Any")
            return "Any"
        return py_ident(t)

    def ann(self, t: Optional[str]) -> ast.expr:
        return P.parse_expr(self.pytype(t))

    def zero_src(self, t: Optional[str]) -> str:
        """Нулевое значение типа Go (текст выражения Python)."""
        if t is None:
            return "None"
        u = self.T.under(t)
        if u is None or u.startswith("*"):
            return "None"
        if u.startswith("[]"):
            return "[]"
        m = ARRAY_RE.match(u)
        if m:
            return f"[{self.zero_src(m.group(2))} for _ in range({m.group(1)})]"
        if u.startswith("map["):
            return "{}"
        if u in INTS:
            return "0"
        if u in FLOATS:
            return "0.0"
        if u in COMPLEXES:
            return "0j"
        if u == "string":
            return '""'
        if u == "bool":
            return "False"
        if u in self.T.structs:
            return f"{py_ident(t)}()"
        return "None"

    def zero(self, t: Optional[str]) -> ast.expr:
        return P.parse_expr(self.zero_src(t))

    def field_default(self, t: Optional[str]) -> ast.expr:
        """Значение поля dataclass (изменяемые - через factory)."""
        if t is None:
            return P.const(None)
        u = self.T.under(t)
        if u is not None and u.startswith("[]"):
            return P.parse_expr("field(default_factory=list)")
        if u is not None and u.startswith("map["):
            return P.parse_expr("field(default_factory=dict)")
        if self.T.is_value_aggregate(t):
            return P.parse_expr(f"field(default_factory=lambda: {self.zero_src(t)})")
        return self.zero(t)
