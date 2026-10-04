"""Кодогенератор: AST упрощённого Go -> исходный код на Python.

Использование:
    tree = Parser(tokens).parse()
    gen = PythonCodeGenerator()
    code = gen.generate(tree)
    print(gen.warnings)   # что не удалось перенести точно
"""
import keyword
import re
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from golang_python_translator.parser import (
    BinaryOp,
    BreakStatement,
    FunctionDecl,
    Identifier,
    InterfaceDecl,
    Node,
    Program,
    StructDecl,
    TypeDecl,
)


class CodeGenError(Exception):
    """Ошибка генерации кода."""


# --------------------------------------------------------------------------
# Типы
# --------------------------------------------------------------------------
INTS: Set[str] = {
    "int", "int8", "int16", "int32", "int64",
    "uint", "uint8", "uint16", "uint32", "uint64", "uintptr",
    "byte", "rune",
}
FLOATS: Set[str] = {"float32", "float64"}
COMPLEXES: Set[str] = {"complex64", "complex128"}

# Приведения типов в Go: int(x), float64(x), bool(x)
CONVERSIONS: Dict[str, str] = {
    **{t: "int" for t in INTS},
    **{t: "float" for t in FLOATS},
    "bool": "bool",
}

# --------------------------------------------------------------------------
# Операторы и приоритеты (по возрастанию)
# --------------------------------------------------------------------------
PREC_OR, PREC_AND, PREC_NOT, PREC_CMP, PREC_ADD, PREC_MUL, PREC_UNARY, PREC_ATOM = range(1, 9)

BINOPS: Dict[str, Tuple[str, int]] = {
    "or": ("or", PREC_OR),
    "and": ("and", PREC_AND),
    "eq": ("==", PREC_CMP),
    "ne": ("!=", PREC_CMP),
    "lt": ("<", PREC_CMP),
    "gt": (">", PREC_CMP),
    "le": ("<=", PREC_CMP),
    "ge": (">=", PREC_CMP),
    "plus": ("+", PREC_ADD),
    "minus": ("-", PREC_ADD),
    "times": ("*", PREC_MUL),
    "divide": ("/", PREC_MUL),
    "mod": ("%", PREC_MUL),
}
BOOL_OPS: Set[str] = {"or", "and", "eq", "ne", "lt", "gt", "le", "ge"}

AUG_ASSIGN: Dict[str, str] = {
    "plus_assign": "+=",
    "minus_assign": "-=",
    "times_assign": "*=",
}

# --------------------------------------------------------------------------
# Стандартная библиотека: "пакет.Метод" -> (шаблон, python-модуль, тип результата)
# --------------------------------------------------------------------------
STDLIB: Dict[str, Tuple[str, Optional[str], Optional[str]]] = {
    "strings.ToUpper": ("{0}.upper()", None, "string"),
    "strings.ToLower": ("{0}.lower()", None, "string"),
    "strings.TrimSpace": ("{0}.strip()", None, "string"),
    "strings.Contains": ("({1} in {0})", None, "bool"),
    "strings.HasPrefix": ("{0}.startswith({1})", None, "bool"),
    "strings.HasSuffix": ("{0}.endswith({1})", None, "bool"),
    "strings.Repeat": ("({0} * {1})", None, "string"),
    "strings.Split": ("{0}.split({1})", None, None),
    "strings.Fields": ("{0}.split()", None, None),
    "strings.Join": ("{1}.join({0})", None, "string"),
    "strings.Replace": ("{0}.replace({1}, {2}, {3})", None, "string"),
    "strings.ReplaceAll": ("{0}.replace({1}, {2})", None, "string"),
    "strings.Index": ("{0}.find({1})", None, "int"),
    "math.Sqrt": ("math.sqrt({0})", "math", "float64"),
    "math.Pow": ("math.pow({0}, {1})", "math", "float64"),
    "math.Abs": ("abs({0})", None, "float64"),
    "math.Floor": ("float(math.floor({0}))", "math", "float64"),
    "math.Ceil": ("float(math.ceil({0}))", "math", "float64"),
    "math.Max": ("max({0}, {1})", None, "float64"),
    "math.Min": ("min({0}, {1})", None, "float64"),
    "math.Sin": ("math.sin({0})", "math", "float64"),
    "math.Cos": ("math.cos({0})", "math", "float64"),
    "math.Tan": ("math.tan({0})", "math", "float64"),
    "math.Log": ("math.log({0})", "math", "float64"),
    "math.Exp": ("math.exp({0})", "math", "float64"),
    "math.Hypot": ("math.hypot({0}, {1})", "math", "float64"),
    "math.Mod": ("math.fmod({0}, {1})", "math", "float64"),
    "strconv.Itoa": ("str({0})", None, "string"),
    "strconv.Atoi": ("int({0})", None, "int"),  # Go возвращает (int, error)
    "strconv.ParseFloat": ("float({0})", None, "float64"),
    "os.Exit": ("sys.exit({0})", "sys", None),
}

def py_string(raw: str) -> str:
    """Превращает содержимое Go-строки (как его отдал лексер) в литерал Python."""
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


class PythonCodeGenerator:
    """Обходит AST (parser.py) и строит эквивалентный код на Python.

    Результат не использует вспомогательных функций: / и % для int переводятся
    в // и % Python (для отрицательных чисел результат может отличаться от Go),
    Printf переводится в оператор % над строкой.
    """

    def __init__(self, indent: str = "    ") -> None:
        self.indent_str: str = indent
        self._reset()

    # ------------------------------------------------------------------
    # Состояние
    # ------------------------------------------------------------------
    def _reset(self) -> None:
        self.lines: List[str] = []
        self.level: int = 0
        self.scopes: List[Dict[str, Optional[str]]] = []
        self.contexts: List[str] = []
        self.cur_ret: Optional[str] = None
        self.funcs: Dict[str, Optional[str]] = {}
        self.structs: Dict[str, StructDecl] = {}
        self.struct_nodes: List[StructDecl] = []
        self.iface_nodes: List[InterfaceDecl] = []
        self.alias_nodes: List[TypeDecl] = []
        self.packages: Set[str] = set()
        self.py_imports: Set[str] = set()
        self.typing: Set[str] = set()
        self.need_dataclass: bool = False
        self.warnings: List[str] = []
        self._tmp: int = 0

    # ------------------------------------------------------------------
    # Публичный интерфейс
    # ------------------------------------------------------------------
    def generate(self, program: Program) -> str:
        self._reset()
        self._prescan(program)

        type_sections: List[List[str]] = []
        for node in self.struct_nodes:
            type_sections.append(self._section(self._gen_struct, node))
        for node in self.iface_nodes:
            type_sections.append(self._section(self._gen_interface, node))
        for node in self.alias_nodes:
            type_sections.append(self._section(self._gen_alias, node))

        func_sections: List[List[str]] = []
        for fn in program.functions:
            func_sections.append(self._section(self.stmt, fn))

        sections: List[List[str]] = [self._header(program)]
        sections += type_sections
        sections += func_sections
        if "main" in self.funcs:
            sections.append(['if __name__ == "__main__":', f"{self.indent_str}main()"])

        return "\n\n\n".join("\n".join(s) for s in sections) + "\n"

    # ------------------------------------------------------------------
    # Предварительный обход
    # ------------------------------------------------------------------
    def _walk(self, n: Any) -> Iterator[Node]:
        if isinstance(n, Node):
            yield n
            for v in vars(n).values():
                yield from self._walk(v)
        elif isinstance(n, (list, tuple)):
            for x in n:
                yield from self._walk(x)

    def _prescan(self, program: Program) -> None:
        for imp in program.imports:
            self.packages.add(str(imp).split("/")[-1])
        for n in self._walk(program):
            if isinstance(n, FunctionDecl) and n.name:
                self.funcs[n.name] = n.return_type
            elif isinstance(n, StructDecl) and n.name:
                self.structs[n.name] = n
                self.struct_nodes.append(n)
            elif isinstance(n, InterfaceDecl):
                self.iface_nodes.append(n)
            elif isinstance(n, TypeDecl):
                self.alias_nodes.append(n)

    def _section(self, fn: Any, node: Any) -> List[str]:
        self.lines = []
        self.level = 0
        fn(node)
        return self.lines

    def _header(self, program: Program) -> List[str]:
        lines = [
            f"# Автоматически сгенерировано из Go (package {program.package})",
            "from __future__ import annotations",
            "",
        ]
        for mod in sorted(self.py_imports):
            lines.append(f"import {mod}")
        if self.typing:
            lines.append("from typing import " + ", ".join(sorted(self.typing)))
        if self.need_dataclass:
            lines.append("from dataclasses import dataclass, field")
        while lines and lines[-1] == "":
            lines.pop()
        return lines

    # ------------------------------------------------------------------
    # Мелкие утилиты
    # ------------------------------------------------------------------
    def emit(self, line: str = "") -> None:
        self.lines.append(self.indent_str * self.level + line if line else "")

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def tmp(self, prefix: str) -> str:
        self._tmp += 1
        return f"_{prefix}{self._tmp}"

    def ident(self, name: Optional[str]) -> str:
        name = name or "_"
        return name + "_" if keyword.iskeyword(name) else name

    # --- области видимости (нужны только для вывода типов) ---
    def push(self) -> None:
        self.scopes.append({})

    def pop(self) -> None:
        self.scopes.pop()

    def declare(self, name: Optional[str], t: Optional[str]) -> None:
        if name and self.scopes:
            self.scopes[-1][name] = t

    def is_var(self, name: Optional[str]) -> bool:
        return any(name in s for s in self.scopes)

    def var_type(self, name: Optional[str]) -> Optional[str]:
        for s in reversed(self.scopes):
            if name in s:
                return s[name]
        return None

    # ------------------------------------------------------------------
    # Типы Go -> Python
    # ------------------------------------------------------------------
    @staticmethod
    def _split_map(t: str) -> Tuple[str, str]:
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

    def pytype(self, t: Optional[str]) -> str:
        if t is None:
            self.typing.add("Any")
            return "Any"
        if t.startswith("[]"):
            self.typing.add("List")
            return f"List[{self.pytype(t[2:])}]"
        m = re.match(r"\[\d+\](.*)$", t)
        if m:
            self.typing.add("List")
            return f"List[{self.pytype(m.group(1))}]"
        if t.startswith("map["):
            k, v = self._split_map(t)
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
        return self.ident(t)

    def zero(self, t: Optional[str]) -> str:
        """Нулевое значение типа Go."""
        if t is None:
            return "None"
        if t.startswith("[]"):
            return "[]"
        m = re.match(r"\[(\d+)\](.*)$", t)
        if m:
            return f"[{self.zero(m.group(2))} for _ in range({m.group(1)})]"
        if t.startswith("map["):
            return "{}"
        if t in INTS:
            return "0"
        if t in FLOATS:
            return "0.0"
        if t in COMPLEXES:
            return "0j"
        if t == "string":
            return '""'
        if t == "bool":
            return "False"
        if t in self.structs:
            return f"{self.ident(t)}()"
        return "None"

    def field_default(self, t: Optional[str]) -> str:
        """Значение по умолчанию для поля dataclass (изменяемые - через factory)."""
        if t is None:
            return "None"
        if t.startswith("[]"):
            return "field(default_factory=list)"
        if t.startswith("map["):
            return "field(default_factory=dict)"
        if re.match(r"\[\d+\]", t) or t in self.structs:
            return f"field(default_factory=lambda: {self.zero(t)})"
        return self.zero(t)

    # ------------------------------------------------------------------
    # Вывод типа выражения (нужен для выбора /, форматирования и т.п.)
    # ------------------------------------------------------------------
    def infer(self, n: Any) -> Optional[str]:
        kind = type(n).__name__
        if kind == "NumberLiteral":
            v = n.value
            if isinstance(v, int):
                return "int"
            if isinstance(v, float):
                return "float64"
            return "complex128" if re.match(r"\d", str(v)) else "rune"
        if kind == "StringLiteral":
            return "string"
        if kind == "Identifier":
            if n.name in ("true", "false") and not self.is_var(n.name):
                return "bool"
            return self.var_type(n.name)
        if kind == "UnaryOp":
            return "bool" if n.op == "not" else self.infer(n.operand)
        if kind == "BinaryOp":
            if n.op in BOOL_OPS:
                return "bool"
            lt, rt = self.infer(n.left), self.infer(n.right)
            if lt in FLOATS or rt in FLOATS:
                return lt if lt in FLOATS else rt
            if lt == rt:
                return lt
            if lt in INTS and rt in INTS:
                return lt
            return None
        if kind == "FunctionCall":
            if n.name in CONVERSIONS or n.name == "string":
                return n.name
            if n.name in ("len", "cap"):
                return "int"
            return self.funcs.get(n.name)
        if kind == "MethodCall":
            if n.receiver == "fmt" and n.method.startswith("Sprint"):
                return "string"
            entry = STDLIB.get(f"{n.receiver}.{n.method}")
            return entry[2] if entry else None
        return None

    def val(self, node: Any, target: Optional[str] = None) -> str:
        """Выражение с неявным приведением int -> float, если целевой тип float."""
        s = self.expr(node)
        if target in FLOATS:
            t = self.infer(node)
            if type(node).__name__ == "NumberLiteral" and isinstance(node.value, int):
                return f"{node.value}.0"
            if t in INTS:
                return f"float({s})"
        return s

    # ------------------------------------------------------------------
    # Диспетчеризация
    # ------------------------------------------------------------------
    def stmt(self, node: Any) -> None:
        handler = getattr(self, "stmt_" + type(node).__name__, None)
        if handler is None:
            raise CodeGenError(f"Неизвестный узел-инструкция: {type(node).__name__}")
        handler(node)

    def expr(self, node: Any, pp: int = 0, right: bool = False) -> str:
        handler = getattr(self, "expr_" + type(node).__name__, None)
        if handler is None:
            raise CodeGenError(f"Неизвестный узел-выражение: {type(node).__name__}")
        return handler(node, pp, right)

    def body(self, node: Any, ctx: Optional[str] = None, strip_break: bool = False) -> None:
        """Тело блока с отступом. Для пустого тела пишет pass."""
        stmts = list(getattr(node, "statements", node) or [])
        if strip_break and stmts and isinstance(stmts[-1], BreakStatement):
            stmts.pop()
        self.level += 1
        self.push()
        if ctx:
            self.contexts.append(ctx)
        start = len(self.lines)
        for s in stmts:
            self.stmt(s)
        if len(self.lines) == start:
            self.emit("pass")
        if ctx:
            self.contexts.pop()
        self.pop()
        self.level -= 1

    # ------------------------------------------------------------------
    # Типы верхнего уровня (struct / interface / type поднимаются в модуль)
    # ------------------------------------------------------------------
    def _gen_struct(self, n: StructDecl) -> None:
        self.need_dataclass = True
        self.emit("@dataclass")
        self.emit(f"class {self.ident(n.name)}:")
        self.level += 1
        if not n.fields:
            self.emit("pass")
        for f in n.fields:
            self.emit(f"{self.ident(f.name)}: {self.pytype(f.type)} = {self.field_default(f.type)}")
        self.level -= 1

    def _gen_interface(self, n: InterfaceDecl) -> None:
        self.typing.update({"Protocol", "Any"})
        self.emit(f"class {self.ident(n.name)}(Protocol):")
        self.level += 1
        if not n.methods:
            self.emit("pass")
        for m in n.methods:
            self.emit(f"def {self.ident(m)}(self) -> Any: ...")
        self.level -= 1

    def _gen_alias(self, n: TypeDecl) -> None:
        self.emit(f"{self.ident(n.name)} = {self.pytype(n.base)}")

    def stmt_StructDecl(self, n: StructDecl) -> None:
        pass  # поднято на уровень модуля

    def stmt_InterfaceDecl(self, n: InterfaceDecl) -> None:
        pass

    def stmt_TypeDecl(self, n: TypeDecl) -> None:
        pass

    def stmt_MapDecl(self, n: Any) -> None:
        self.emit(f"pass  # map[{n.key_type}]{n.value_type}")

    # ------------------------------------------------------------------
    # Инструкции
    # ------------------------------------------------------------------
    def stmt_FunctionDecl(self, n: FunctionDecl) -> None:
        # Go: func f(a, b int) - тип b относится и к a
        params: List[Tuple[str, Optional[str]]] = []
        next_t: Optional[str] = None
        for p in reversed(n.params):
            if p.type is not None:
                next_t = p.type
            params.append((p.name, p.type if p.type is not None else next_t))
        params.reverse()

        sig = ", ".join(
            self.ident(name) + (f": {self.pytype(t)}" if t else "") for name, t in params
        )
        ret = self.pytype(n.return_type) if n.return_type else "None"
        self.emit(f"def {self.ident(n.name)}({sig}) -> {ret}:")

        self.cur_ret = n.return_type
        self.push()
        for name, t in params:
            self.declare(name, t)
        self.body(n.body)
        self.pop()
        self.cur_ret = None

    def stmt_VarDecl(self, n: Any) -> None:
        t = n.type
        if n.value is not None:
            value = self.val(n.value, t)
            vt = t or self.infer(n.value)
        else:
            value = self.zero(t) if t else "None"
            vt = t
        ann = f": {self.pytype(t)}" if t else ""
        self.emit(f"{self.ident(n.name)}{ann} = {value}")
        self.declare(n.name, vt)

    def stmt_ConstDecl(self, n: Any) -> None:
        value = self.expr(n.value) if n.value is not None else "None"
        self.emit(f"{self.ident(n.name)} = {value}")
        self.declare(n.name, self.infer(n.value) if n.value is not None else None)

    def stmt_Assignment(self, n: Any) -> None:
        target = self.ident(n.name)
        op = n.op
        if op in ("assign", "declare"):
            value = self.val(n.value, self.var_type(n.name) if op == "assign" else None)
            self.emit(f"{target} = {value}")
            if op == "declare":
                self.declare(n.name, self.infer(n.value))
        elif op in AUG_ASSIGN:
            self.emit(f"{target} {AUG_ASSIGN[op]} {self.expr(n.value)}")
        elif op in ("divide_assign", "mod_assign"):
            synthetic = BinaryOp(
                op="divide" if op == "divide_assign" else "mod",
                left=Identifier(name=n.name),
                right=n.value,
            )
            self.emit(f"{target} = {self.expr(synthetic)}")
        else:
            raise CodeGenError(f"Неизвестный оператор присваивания: {op}")

    def stmt_IfStatement(self, n: Any) -> None:
        self.emit(f"if {self.expr(n.condition)}:")
        self.body(n.body)

    def stmt_ForStatement(self, n: Any) -> None:
        self.emit(f"while {self.expr(n.condition)}:")
        self.body(n.body, ctx="loop")

    def stmt_RangeStatement(self, n: Any) -> None:
        it = self.expr(n.iterable)
        if self.infer(n.iterable) in INTS:
            it = f"range({it})"
        self.emit(f"for _ in {it}:")
        self.level += 1
        self.emit("pass")
        self.level -= 1

    def stmt_ReturnStatement(self, n: Any) -> None:
        if n.value is None:
            self.emit("return")
        else:
            self.emit(f"return {self.val(n.value, self.cur_ret)}")

    def stmt_BreakStatement(self, n: Any) -> None:
        if self.contexts and self.contexts[-1] == "switch":
            self.warn("break внутри switch/select (не в конце case) не перенесён")
            self.emit("pass  # WARNING: break внутри switch/select не поддержан")
        else:
            self.emit("break")

    def stmt_ContinueStatement(self, n: Any) -> None:
        self.emit("continue")

    def stmt_SwitchStatement(self, n: Any) -> None:
        subj = n.expression
        if type(subj).__name__ in ("Identifier", "NumberLiteral", "StringLiteral"):
            s = self.expr(subj)
        else:
            s = self.tmp("sw")
            self.emit(f"{s} = {self.expr(subj)}")

        cases = [c for c in n.cases if c.value is not None]
        default = next((c for c in n.cases if c.value is None), None)

        for i, c in enumerate(cases):
            kw = "if" if i == 0 else "elif"
            self.emit(f"{kw} {s} == {self.expr(c.value, PREC_CMP, True)}:")
            self.body(c.body, ctx="switch", strip_break=True)

        if default is not None:
            if cases:
                self.emit("else:")
                self.body(default.body, ctx="switch", strip_break=True)
            else:
                stmts = list(default.body)
                if stmts and isinstance(stmts[-1], BreakStatement):
                    stmts.pop()
                self.contexts.append("switch")
                for st in stmts:
                    self.stmt(st)
                self.contexts.pop()

    def stmt_SelectStatement(self, n: Any) -> None:
        # В грамматике нет операции <-, поэтому select приближается цепочкой if/elif:
        # выполняется первый case, чьё выражение истинно.
        self.warn("select приближён цепочкой if/elif (каналов в грамматике нет)")
        self.emit("# select: выполняется первый case с истинным условием")
        cases = [c for c in n.cases if c.value is not None]
        default = next((c for c in n.cases if c.value is None), None)
        for i, c in enumerate(cases):
            kw = "if" if i == 0 else "elif"
            self.emit(f"{kw} {self.expr(c.value)}:")
            self.body(c.body, ctx="switch", strip_break=True)
        if default is not None:
            if cases:
                self.emit("else:")
                self.body(default.body, ctx="switch", strip_break=True)
            else:
                self.emit("if True:")
                self.body(default.body, ctx="switch", strip_break=True)

    def stmt_FunctionCall(self, n: Any) -> None:
        if n.name == "panic" and len(n.args) == 1 and not self.is_var("panic"):
            arg = n.args[0]
            msg = self.expr(arg, PREC_ADD, True) if self.infer(arg) == "string" else f"str({self.expr(arg)})"
            self.emit(f'raise RuntimeError("panic: " + {msg})')
            return
        self.emit(self.expr(n))

    def stmt_MethodCall(self, n: Any) -> None:
        self.emit(self.expr(n))

    def stmt_ExpressionStatement(self, n: Any) -> None:
        self.emit(self.expr(n.expression))

    # ------------------------------------------------------------------
    # Выражения
    # ------------------------------------------------------------------
    def expr_Identifier(self, n: Any, pp: int, right: bool) -> str:
        if not self.is_var(n.name):
            mapping = {"nil": "None", "true": "True", "false": "False"}
            if n.name in mapping:
                return mapping[n.name]
        return self.ident(n.name)

    def expr_NumberLiteral(self, n: Any, pp: int, right: bool) -> str:
        v = n.value
        if isinstance(v, int):
            return str(v)
        if isinstance(v, float):
            return repr(v)
        text = str(v)
        if re.match(r"\d", text):  # комплексный литерал 3i -> 3j
            return text[:-1] + "j"
        # руна 'a' -> ord("a")
        raw = '\\"' if text == '"' else text
        return f'ord("{raw}")'

    def expr_StringLiteral(self, n: Any, pp: int, right: bool) -> str:
        return py_string(n.value or "")

    def expr_UnaryOp(self, n: Any, pp: int, right: bool) -> str:
        if n.op == "minus":
            prec, sym = PREC_UNARY, "-"
        else:
            prec, sym = PREC_NOT, "not "
        s = sym + self.expr(n.operand, prec, False)
        return f"({s})" if prec < pp else s

    def expr_BinaryOp(self, n: Any, pp: int, right: bool) -> str:
        sym, prec = BINOPS[n.op]

        if n.op == "divide":
            lt, rt = self.infer(n.left), self.infer(n.right)
            if lt in INTS and rt in INTS:
                sym = "//"
            elif lt not in FLOATS and rt not in FLOATS:
                self.warn("деление с неизвестным типом операндов переведено как '/' "
                          "(в Go для int это целочисленное деление)")

        left = self.expr(n.left, prec, False)
        rhs = self.expr(n.right, prec, True)
        s = f"{left} {sym} {rhs}"
        # Сравнения в Python образуют цепочки (a < b < c), поэтому скобки всегда
        if prec < pp or (prec == pp and (right or prec == PREC_CMP)):
            return f"({s})"
        return s

    def expr_IndexExpr(self, n: Any, pp: int, right: bool) -> str:
        base = getattr(n, "base", None)
        if base is not None:
            return f"{self.expr(base, PREC_ATOM)}[{self.expr(n.index)}]"
        self.warn("IndexExpr без base: парсер не сохраняет индексируемое выражение (a[i])")
        return f"[{self.expr(n.index)}]"

    def expr_FunctionCall(self, n: Any, pp: int, right: bool) -> str:
        if not self.is_var(n.name):
            s = self._builtin(n.name, n.args)
            if s is not None:
                return s
        args = ", ".join(self.expr(a) for a in n.args)
        return f"{self.ident(n.name)}({args})"

    def expr_MethodCall(self, n: Any, pp: int, right: bool) -> str:
        pkg, method = n.receiver, n.method
        if pkg in self.packages and not self.is_var(pkg):
            s = self._pkg_call(pkg, method, n.args)
            if s is not None:
                return s
            self.warn(f"{pkg}.{method}: нет соответствия в Python, вызов оставлен как есть")
        args = ", ".join(self.expr(a) for a in n.args)
        return f"{self.ident(pkg)}.{method}({args})"

    # --- встроенные функции Go ---
    def _builtin(self, name: str, args: List[Any]) -> Optional[str]:
        if name in ("len", "cap") and len(args) == 1:
            return f"len({self.expr(args[0])})"
        if name == "append" and args:
            first = self.expr(args[0], PREC_ATOM)
            rest = "".join(", " + self.expr(a) for a in args[1:])
            return f"[*{first}{rest}]"
        if name == "delete" and len(args) == 2:
            return f"{self.expr(args[0], PREC_ATOM)}.pop({self.expr(args[1])}, None)"
        if name == "string" and len(args) == 1:
            t = self.infer(args[0])
            if t in INTS:
                return f"chr({self.expr(args[0])})"
            if t == "string":
                return self.expr(args[0])
            return f"str({self.expr(args[0])})"
        if name in CONVERSIONS and len(args) == 1:
            return f"{CONVERSIONS[name]}({self.expr(args[0])})"
        if name in ("min", "max") and args:
            return f"{name}({', '.join(self.expr(a) for a in args)})"
        if name in ("print", "println"):
            self.py_imports.add("sys")
            vals = [self.expr(a) for a in args] + ["file=sys.stderr"]
            if name == "print":
                vals.append('sep=""')
            return "print(" + ", ".join(vals) + ")"
        return None

    # --- пакеты стандартной библиотеки ---
    def strexpr(self, node: Any) -> str:
        """Выражение, приведённое к строке."""
        if self.infer(node) == "string":
            return self.expr(node)
        return f"str({self.expr(node)})"

    @staticmethod
    def go_format(fmt: str) -> str:
        """Переводит глаголы Go в формат Python (%v, %t -> %s; %q -> %r)."""
        def repl(m: Any) -> str:
            flags, verb = m.group(1), m.group(2)
            if verb in "vtT":
                return "%" + re.sub(r"[+#]", "", flags) + "s"
            if verb == "q":
                return "%" + flags + "r"
            return m.group(0)
        return re.sub(r"%([-+# 0]*\d*(?:\.\d+)?)([a-zA-Z%])", repl, fmt)

    def _pkg_call(self, pkg: str, method: str, args: List[Any]) -> Optional[str]:
        if pkg == "fmt":
            return self._fmt_call(method, args)
        entry = STDLIB.get(f"{pkg}.{method}")
        if entry is None:
            return None
        template, module, _ = entry
        if module:
            self.py_imports.add(module)
        vals = [self.expr(a, PREC_ATOM) for a in args]
        try:
            return template.format(*vals)
        except IndexError:
            raise CodeGenError(f"{pkg}.{method}: не хватает аргументов")

    def _fmt_call(self, method: str, args: List[Any]) -> Optional[str]:
        if method == "Println":
            return "print(" + ", ".join(self.expr(a) for a in args) + ")"
        if method == "Sprintln":
            return '(" ".join([' + ", ".join(self.strexpr(a) for a in args) + ']) + "\\n")'
        if method in ("Print", "Sprint"):
            parts: List[str] = []
            for i, a in enumerate(args):
                # Go ставит пробел между операндами, если ни один из них не строка
                if i and self.infer(args[i - 1]) != "string" and self.infer(a) != "string":
                    parts.append('" "')
                parts.append(self.strexpr(a))
            joined = " + ".join(parts) if parts else '""'
            if method == "Print":
                return f'print({joined}, end="")'
            return f"({joined})" if len(parts) > 1 else joined
        if method in ("Printf", "Sprintf"):
            if not args:
                raise CodeGenError(f"fmt.{method} требует строку формата")
            first = args[0]
            if type(first).__name__ == "StringLiteral":
                fmt = py_string(self.go_format(first.value or ""))
            else:
                fmt = self.expr(first, PREC_ATOM)
            rest = ", ".join(self.expr(a) for a in args[1:])
            tup = "(" + rest + ("," if len(args) == 2 else "") + ")"
            call = f"{fmt} % {tup}"
            return f'print({call}, end="")' if method == "Printf" else call
        return None
