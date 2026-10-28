"""Общие константы и работа с типами Go (используется всеми этапами)."""
import ast
import builtins
import keyword
import re
import warnings
from typing import Any, Dict, List, Optional, Set, Tuple

from golang_python_translator.lexer import GoTranslatorError


class CodeGenError(GoTranslatorError):
    """Ошибка анализа или генерации кода."""


INTS: Set[str] = {
    "int", "int8", "int16", "int32", "int64",
    "uint", "uint8", "uint16", "uint32", "uint64", "uintptr",
    "byte", "rune",
}
FLOATS: Set[str] = {"float32", "float64"}
COMPLEXES: Set[str] = {"complex64", "complex128"}

# Типы, у которых арифметика «заворачивается» при переполнении.
# int/int64/rune исключены сознательно, чтобы не засорять код.
SIZED: Dict[str, Tuple[int, bool]] = {
    "int8": (8, True), "int16": (16, True), "int32": (32, True),
    "uint8": (8, False), "byte": (8, False), "uint16": (16, False),
    "uint32": (32, False), "uint64": (64, False), "uint": (64, False),
    "uintptr": (64, False),
}

CONVERSIONS: Dict[str, str] = {
    **{t: "int" for t in INTS},
    **{t: "float" for t in FLOATS},
    "bool": "bool",
}

ARRAY_RE = re.compile(r"\[(\d+)\](.*)$")

BOOL_OPS: Set[str] = {"or", "and", "eq", "ne", "lt", "gt", "le", "ge"}
WRAP_OPS: Set[str] = {"plus", "minus", "times", "shl"}

BINOP_SYM: Dict[str, str] = {
    "plus": "+", "minus": "-", "times": "*", "divide": "/", "mod": "%",
    "bitor": "|", "bitxor": "^", "bitand": "&", "shl": "<<", "shr": ">>",
}
CMP_SYM: Dict[str, str] = {
    "eq": "==", "ne": "!=", "lt": "<", "gt": ">", "le": "<=", "ge": ">=",
}

# операция присваивания -> (бинарная операция AST, python-оператор или None)
AUG_ASSIGN: Dict[str, Tuple[str, Optional[str]]] = {
    "plus_assign": ("plus", "+"),
    "minus_assign": ("minus", "-"),
    "times_assign": ("times", "*"),
    "divide_assign": ("divide", None),
    "mod_assign": ("mod", None),
    "and_assign": ("bitand", "&"),
    "or_assign": ("bitor", "|"),
    "xor_assign": ("bitxor", "^"),
    "shl_assign": ("shl", "<<"),
    "shr_assign": ("shr", ">>"),
    "andnot_assign": ("andnot", None),
}

# Имена, которые нельзя использовать в сгенерированном коде как есть
RESERVED: Set[str] = (
    set(keyword.kwlist) | set(dir(builtins))
    | {"math", "sys", "copy", "random", "re", "decimal", "dataclasses", "self",
       "List", "Dict", "Any", "Tuple", "Optional", "Protocol", "field", "dataclass",
       "annotations"}
)


def py_ident(name: Optional[str]) -> str:
    name = name or "_"
    if name == "_":
        return name
    return name + "_" if name in RESERVED else name


def strip_ptr(t: Optional[str]) -> Optional[str]:
    while t is not None and t.startswith("*"):
        t = t[1:]
    return t


def split_top(s: str) -> List[str]:
    """Разделить 'a,[]b,map[k]v' по запятым верхнего уровня."""
    parts: List[str] = []
    depth, cur = 0, ""
    for ch in s:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def split_tuple(t: Optional[str]) -> Optional[List[str]]:
    if t and t.startswith("(") and t.endswith(")"):
        return split_top(t[1:-1])
    return None


def split_map(t: str) -> Tuple[str, str]:
    """'map[K]V' -> (K, V) с учётом вложенных скобок в K."""
    depth = 0
    for i in range(3, len(t)):
        if t[i] == "[":
            depth += 1
        elif t[i] == "]":
            depth -= 1
            if depth == 0:
                return t[4:i], t[i + 1:]
    raise CodeGenError(f"Некорректный тип map: {t}")


def py_string(raw: str) -> str:
    """Содержимое Go-строки (как его отдал лексер) -> текст литерала Python."""
    out: List[str] = []
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == "\\":
            if i + 1 < len(raw):
                out.append(raw[i:i + 2])
                i += 2
                continue
            out.append("\\\\")
        elif c == '"':
            out.append('\\"')
        elif c == "\n":
            out.append("\\n")
        elif c == "\r":
            out.append("\\r")
        else:
            out.append(c)
        i += 1
    return '"' + "".join(out) + '"'


def go_string_value(raw: str) -> str:
    """Значение Go-строки с раскрытыми escape-последовательностями."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return ast.literal_eval(py_string(raw))
        except (SyntaxError, ValueError) as e:
            raise CodeGenError(f"Некорректная строка: {raw!r} ({e})")


class TypeEnv:
    """Вопросы о типах с учётом алиасов (`type MyInt int`) и структур."""

    def __init__(self) -> None:
        self.aliases: Dict[str, str] = {}
        self.structs: Dict[str, Any] = {}

    def under(self, t: Optional[str]) -> Optional[str]:
        n = 0
        while t is not None and t in self.aliases and n < 32:
            t = self.aliases[t]
            n += 1
        return t

    def base(self, t: Optional[str]) -> Optional[str]:
        """Тип без указателей и алиасов."""
        return self.under(strip_ptr(t))

    def elem(self, t: Optional[str]) -> Optional[str]:
        u = self.base(t)
        if u is None:
            return None
        if u.startswith("[]"):
            return u[2:]
        m = ARRAY_RE.match(u)
        if m:
            return m.group(2)
        if u.startswith("map["):
            return split_map(u)[1]
        if u == "string":
            return "byte"
        return None

    def is_float(self, t: Optional[str]) -> bool:
        return self.base(t) in FLOATS

    def is_int(self, t: Optional[str]) -> bool:
        return self.base(t) in INTS

    def is_map(self, t: Optional[str]) -> bool:
        u = self.base(t)
        return u is not None and u.startswith("map[")

    def is_slice(self, t: Optional[str]) -> bool:
        u = self.base(t)
        return u is not None and u.startswith("[]")

    def is_value_aggregate(self, t: Optional[str]) -> bool:
        """Структура или массив по значению (копируются при присваивании)."""
        u = self.under(t)
        return u is not None and (u in self.structs or ARRAY_RE.match(u) is not None)
