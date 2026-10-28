"""Парсер упрощённого Go (рекурсивный спуск).

Поддерживается как обычный Go (`type P struct {...}`), так и прежний диалект
(`struct P {...}`, `interface I {...}` внутри функций).
"""
import copy
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
from typing import Any, Dict, Iterator, List, Optional, Tuple

from golang_python_translator.lexer import GoTranslatorError, Token


class ParseError(GoTranslatorError, SyntaxError):
    """Синтаксическая ошибка с номером строки (совместима с SyntaxError)."""

    def __init__(self, message: str, lineno: Optional[int] = None) -> None:
        super().__init__(message, lineno)
        self.lineno = lineno
        self.msg = message

    def __str__(self) -> str:
        return f"{self.message} (строка {self.lineno})" if self.lineno else self.message


# Приоритеты бинарных операторов Go (общие для парсера и генератора)
BINARY_PRECEDENCE: Dict[str, int] = {
    "or": 1,
    "and": 2,
    "eq": 3, "ne": 3, "lt": 3, "le": 3, "gt": 3, "ge": 3,
    "plus": 4, "minus": 4, "bitor": 4, "bitxor": 4,
    "times": 5, "divide": 5, "mod": 5, "shl": 5, "shr": 5, "bitand": 5, "andnot": 5,
}

ASSIGN_OPS = (
    "assign", "declare",
    "plus_assign", "minus_assign", "times_assign", "divide_assign", "mod_assign",
    "and_assign", "or_assign", "xor_assign", "shl_assign", "shr_assign", "andnot_assign",
)

UNSUPPORTED_KEYWORDS = {
    "go": "горутины (go)",
    "defer": "defer",
    "select": "select и каналы",
    "goto": "goto",
    "fallthrough": "fallthrough",
    "chan": "каналы (chan)",
}

node = dataclass(repr=False, eq=False)


@node
class Node:
    """Базовый узел AST."""
    def __repr__(self) -> str:
        return self._fmt(0)

    def _fmt(self, depth: int) -> str:
        indent = "  " * depth
        name = self.__class__.__name__
        children = {f.name: getattr(self, f.name) for f in fields(self)
                    if getattr(self, f.name) is not None and getattr(self, f.name) != []}
        if not children:
            return f"{indent}{name}"
        lines = [f"{indent}{name}"]
        for k, v in children.items():
            if isinstance(v, list):
                lines.append(f"{indent}  {k}:")
                for item in v:
                    if isinstance(item, Node):
                        lines.append(item._fmt(depth + 2))
                    else:
                        lines.append(f"{'  ' * (depth + 2)}{item}")
            elif isinstance(v, Node):
                lines.append(f"{indent}  {k}:")
                lines.append(v._fmt(depth + 2))
            else:
                lines.append(f"{indent}  {k}: {v}")
        return "\n".join(lines)


@node
class Program(Node):
    package: Optional[str] = None
    imports: List[str] = field(default_factory=list)
    functions: List[Any] = field(default_factory=list)
    declarations: List[Any] = field(default_factory=list)  # var/const/type верхнего уровня


@node
class FunctionDecl(Node):
    name: Optional[str] = None
    params: List[Any] = field(default_factory=list)
    return_type: Optional[str] = None  # "int" или "(int,error)" для нескольких результатов
    body: Optional[Node] = None
    receiver: Optional["Param"] = None


@node
class Param(Node):
    name: Optional[str] = None
    type: Optional[str] = None


@node
class Block(Node):
    statements: List[Any] = field(default_factory=list)


@node
class VarDecl(Node):
    name: Optional[str] = None
    type: Optional[str] = None
    value: Any = None


@node
class ConstDecl(Node):
    name: Optional[str] = None
    value: Any = None
    type: Optional[str] = None
    iota: int = 0


@node
class DeclGroup(Node):
    decls: List[Any] = field(default_factory=list)


@node
class Assignment(Node):
    target: Any = None  # Identifier / IndexExpr / FieldExpr / UnaryOp / TupleExpr
    op: Optional[str] = None
    value: Any = None

    @property
    def name(self) -> Optional[str]:
        return self.target.name if isinstance(self.target, Identifier) else None


@node
class IfStatement(Node):
    condition: Any = None
    body: Any = None
    orelse: Any = None  # Block или IfStatement
    init: Any = None


@node
class ForStatement(Node):
    condition: Any = None  # None - бесконечный цикл
    body: Any = None
    init: Any = None
    post: Any = None


@node
class RangeStatement(Node):
    iterable: Any = None
    key: Any = None
    value: Any = None
    define: bool = False
    body: Any = None


@node
class ReturnStatement(Node):
    value: Any = None  # выражение или TupleExpr


@node
class BreakStatement(Node):
    pass


@node
class ContinueStatement(Node):
    pass


@node
class SwitchStatement(Node):
    expression: Any = None  # None - switch без выражения
    cases: List[Any] = field(default_factory=list)
    init: Any = None


@node
class CaseClause(Node):
    values: List[Any] = field(default_factory=list)
    body: List[Any] = field(default_factory=list)
    is_default: bool = False


@node
class FunctionCall(Node):
    name: Optional[str] = None
    args: List[Any] = field(default_factory=list)


@node
class MethodCall(Node):
    receiver: Any = None  # выражение (Identifier для pkg.Func)
    method: Optional[str] = None
    args: List[Any] = field(default_factory=list)


@node
class BinaryOp(Node):
    op: Optional[str] = None
    left: Any = None
    right: Any = None


@node
class UnaryOp(Node):
    op: Optional[str] = None  # minus, plus, not, bitxor, bitand (&x), times (*p)
    operand: Any = None


@node
class Identifier(Node):
    name: Optional[str] = None


@node
class NumberLiteral(Node):
    value: Any = None  # int, float или строка '3i' (комплексное)


@node
class RuneLiteral(Node):
    value: int = 0


@node
class StringLiteral(Node):
    value: Optional[str] = None


@node
class IndexExpr(Node):
    base: Any = None
    index: Any = None


@node
class SliceExpr(Node):
    base: Any = None
    low: Any = None
    high: Any = None


@node
class FieldExpr(Node):
    base: Any = None
    name: Optional[str] = None


@node
class KeyedElement(Node):
    key: Any = None
    value: Any = None


@node
class CompositeLit(Node):
    type: Optional[str] = None  # None - тип выводится из контекста ({1, 2} внутри []Point{...})
    elements: List[Any] = field(default_factory=list)


@node
class TypeExpr(Node):
    type: Optional[str] = None  # тип в позиции выражения: make([]int, n)


@node
class TupleExpr(Node):
    items: List[Any] = field(default_factory=list)


@node
class TypeDecl(Node):
    name: Optional[str] = None
    base: Optional[str] = None


@node
class StructDecl(Node):
    name: Optional[str] = None
    fields: List[Any] = field(default_factory=list)


@node
class StructField(Node):
    name: Optional[str] = None
    type: Optional[str] = None


@node
class InterfaceMethod(Node):
    name: Optional[str] = None
    params: List[Any] = field(default_factory=list)
    return_type: Optional[str] = None


@node
class InterfaceDecl(Node):
    name: Optional[str] = None
    methods: List[Any] = field(default_factory=list)  # InterfaceMethod


@node
class ExpressionStatement(Node):
    expression: Any = None


class Parser:
    def __init__(self, tokens: List[Token]) -> None:
        self.tokens: List[Token] = tokens
        self.pos: int = 0
        self.no_lit: int = 0  # >0: в заголовке if/for/switch `T {` - это блок, а не литерал

    # ------------------------------------------------------------ токены
    def current_token(self) -> Token:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        last = self.tokens[-1].lineno if self.tokens else 1
        return Token("#", "#", last)

    def current(self) -> str:
        return self.current_token().type

    def next(self) -> None:
        self.pos += 1

    def peek(self) -> str:
        if self.pos + 1 < len(self.tokens):
            return self.tokens[self.pos + 1].type
        return "#"

    def error(self, message: str) -> ParseError:
        return ParseError(message, self.current_token().lineno)

    def match(self, expected: str) -> Any:
        tok = self.current_token()
        if tok.type == expected:
            self.next()
            return tok.value
        found = "конец файла" if tok.type == "#" else f"'{tok.type}' ({tok.value!r})"
        raise ParseError(f"Ожидался '{expected}', найден {found}", tok.lineno)

    def skip_semis(self) -> None:
        while self.current() == "semicolon":
            self.next()

    def end_statement(self) -> None:
        if self.current() == "semicolon":
            self.skip_semis()
        elif self.current() not in ("rbrace", "case", "default", "#"):
            raise self.error(f"Ожидался конец инструкции, найден '{self.current()}'")

    @contextmanager
    def lits_ok(self) -> Iterator[None]:
        saved, self.no_lit = self.no_lit, 0
        try:
            yield
        finally:
            self.no_lit = saved

    @contextmanager
    def no_lits(self) -> Iterator[None]:
        self.no_lit += 1
        try:
            yield
        finally:
            self.no_lit -= 1

    # ------------------------------------------------------------ программа
    def parse(self, verbose: bool = False) -> Program:
        tree = self.program()
        if verbose:
            print("OK")
            print("\n=== AST ===")
            print(tree._fmt(0))
        return tree

    def program(self) -> Program:
        node = Program()
        self.skip_semis()
        node.package = str(self.package_declaration())
        self.skip_semis()
        while self.current() == "import":
            node.imports.extend(self.import_declaration())
            self.skip_semis()

        while self.current() != "#":
            t = self.current()
            if t == "func":
                node.functions.append(self.function_declaration())
            elif t in ("var", "const", "type", "struct", "interface"):
                node.declarations.append(self.declaration())
            else:
                raise self.error(f"Неожиданный токен на верхнем уровне: '{t}'")
            self.skip_semis()
        return node

    def package_declaration(self) -> Any:
        self.match("package")
        return self.match("identifier")

    def import_declaration(self) -> List[str]:
        self.match("import")
        paths: List[str] = []
        if self.current() == "lparen":
            self.next()
            self.skip_semis()
            while self.current() != "rparen":
                paths.append(str(self.match("string")))
                self.skip_semis()
            self.match("rparen")
        else:
            if self.current() in ("identifier", "dot"):
                raise self.error("Алиасы импорта не поддерживаются")
            paths.append(str(self.match("string")))
        return paths

    # ------------------------------------------------------------ функции
    def function_declaration(self) -> FunctionDecl:
        node = FunctionDecl()
        self.match("func")
        if self.current() == "lparen":  # метод: func (p *T) Name(...)
            self.next()
            recv = Param()
            recv.name = str(self.match("identifier"))
            recv.type = self.type_spec()
            self.match("rparen")
            node.receiver = recv
        node.name = str(self.match("identifier"))

        self.match("lparen")
        node.params = self.parameter_list()
        self.match("rparen")
        node.return_type = self.result_type()
        node.body = self.block()
        return node

    def result_type(self) -> Optional[str]:
        if self.current() == "lparen":
            self.next()
            types = [self.type_spec()]
            while self.current() == "comma":
                self.next()
                types.append(self.type_spec())
            self.match("rparen")
            return types[0] if len(types) == 1 else "(" + ",".join(types) + ")"
        if self.is_type_start():
            return self.type_spec()
        return None

    def parameter_list(self) -> List[Param]:
        params: List[Param] = []
        if self.current() == "rparen":
            return params
        params.append(self.parameter())
        while self.current() == "comma":
            self.match("comma")
            params.append(self.parameter())
        return params

    def parameter(self) -> Param:
        node = Param()
        node.name = str(self.match("identifier"))
        if self.is_type_start():
            node.type = self.type_spec()
        return node

    def block(self) -> Block:
        node = Block()
        self.match("lbrace")
        with self.lits_ok():
            self.skip_semis()
            while self.current() not in ("rbrace", "#"):
                node.statements.append(self.statement())
                self.end_statement()
        self.match("rbrace")
        return node

    def statement_block(self) -> List[Any]:
        stmts: List[Any] = []
        self.skip_semis()
        while self.current() not in ("case", "default", "rbrace", "#"):
            stmts.append(self.statement())
            self.end_statement()
        return stmts

    # ------------------------------------------------------------ инструкции
    def statement(self) -> Any:
        t = self.current()
        if t in ("var", "const", "type", "struct", "interface"):
            return self.declaration()
        if t == "if":
            return self.if_statement()
        if t == "for":
            return self.for_statement()
        if t == "switch":
            return self.switch_statement()
        if t == "return":
            return self.return_statement()
        if t == "break":
            self.next()
            return BreakStatement()
        if t == "continue":
            self.next()
            return ContinueStatement()
        if t in UNSUPPORTED_KEYWORDS:
            raise self.error(f"Не поддерживается: {UNSUPPORTED_KEYWORDS[t]}")
        if t == "lbrace":
            return self.block()
        if t == "func":
            raise self.error("Функциональные литералы не поддерживаются")
        return self.simple_statement()

    def simple_statement(self, allow_range: bool = False) -> Any:
        lhs = self.expression_list()
        t = self.current()
        if t in ASSIGN_OPS:
            self.next()
            if allow_range and self.current() == "range" and t in ("assign", "declare"):
                self.next()
                if len(lhs) > 2:
                    raise self.error("range допускает не более двух переменных")
                return RangeStatement(
                    iterable=self.expression(),
                    key=lhs[0],
                    value=lhs[1] if len(lhs) > 1 else None,
                    define=(t == "declare"),
                )
            rhs = self.expression_list()
            target = lhs[0] if len(lhs) == 1 else TupleExpr(items=lhs)
            value = rhs[0] if len(rhs) == 1 else TupleExpr(items=rhs)
            if len(lhs) > 1 and len(rhs) > 1 and len(lhs) != len(rhs):
                raise self.error("Число переменных и значений в присваивании не совпадает")
            return Assignment(target=target, op=t, value=value)
        if t in ("increment", "decrement"):
            if len(lhs) != 1:
                raise self.error("++/-- применимо к одному выражению")
            self.next()
            return Assignment(
                target=lhs[0],
                op="plus_assign" if t == "increment" else "minus_assign",
                value=NumberLiteral(value=1),
            )
        if len(lhs) != 1:
            raise self.error("Ожидалось присваивание")
        return ExpressionStatement(expression=lhs[0])

    def expression_list(self) -> List[Any]:
        items = [self.expression()]
        while self.current() == "comma":
            self.next()
            items.append(self.expression())
        return items

    def declaration(self) -> Any:
        t = self.current()
        if t == "var":
            return self.variable_declaration()
        if t == "const":
            return self.const_declaration()
        if t == "type":
            return self.type_declaration()
        if t == "struct":  # диалект: struct Name { ... }
            self.next()
            name = str(self.match("identifier"))
            return self.struct_body(name)
        if t == "interface":  # диалект: interface Name { ... }
            self.next()
            name = str(self.match("identifier"))
            return self.interface_body(name)
        raise self.error(f"Ожидалось объявление, найден '{t}'")

    def variable_declaration(self) -> Any:
        self.match("var")
        if self.current() == "lparen":
            self.next()
            self.skip_semis()
            decls: List[Any] = []
            while self.current() != "rparen":
                decls.append(self.var_spec())
                self.skip_semis()
            self.match("rparen")
            return DeclGroup(decls=decls)
        return self.var_spec()

    def var_spec(self) -> Any:
        names = [str(self.match("identifier"))]
        while self.current() == "comma":
            self.next()
            names.append(str(self.match("identifier")))
        typ = self.type_spec() if self.is_type_start() else None
        values: List[Any] = []
        if self.current() == "assign":
            self.next()
            values = self.expression_list()
        if values and len(values) != len(names):
            raise self.error("Число переменных и значений в var не совпадает")
        decls = [
            VarDecl(name=nm, type=typ, value=values[i] if values else None)
            for i, nm in enumerate(names)
        ]
        return decls[0] if len(decls) == 1 else DeclGroup(decls=decls)

    def const_declaration(self) -> Any:
        self.match("const")
        if self.current() == "lparen":
            self.next()
            self.skip_semis()
            decls: List[Any] = []
            prev: Optional[Tuple[Optional[str], List[Any]]] = None
            idx = 0
            while self.current() != "rparen":
                specs, prev = self.const_spec(idx, prev)
                decls.extend(specs)
                idx += 1
                self.skip_semis()
            self.match("rparen")
            return DeclGroup(decls=decls)
        specs, _ = self.const_spec(0, None)
        return specs[0] if len(specs) == 1 else DeclGroup(decls=specs)

    def const_spec(self, idx: int, prev: Optional[Tuple[Optional[str], List[Any]]]) -> Tuple[List[Any], Tuple[Optional[str], List[Any]]]:
        names = [str(self.match("identifier"))]
        while self.current() == "comma":
            self.next()
            names.append(str(self.match("identifier")))
        typ = self.type_spec() if self.is_type_start() else None
        values: List[Any] = []
        if self.current() == "assign":
            self.next()
            values = self.expression_list()
        elif prev is not None:  # неявное повторение предыдущего выражения (iota)
            typ = prev[0]
            values = [copy.deepcopy(v) for v in prev[1]]
        if values and len(values) != len(names):
            raise self.error("Число констант и значений не совпадает")
        specs = [
            ConstDecl(name=nm, type=typ, value=values[i] if values else None, iota=idx)
            for i, nm in enumerate(names)
        ]
        return specs, (typ, values)

    def type_declaration(self) -> Any:
        self.match("type")
        name = str(self.match("identifier"))
        if self.current() == "struct":
            self.next()
            return self.struct_body(name)
        if self.current() == "interface":
            self.next()
            return self.interface_body(name)
        return TypeDecl(name=name, base=self.type_spec())

    def struct_body(self, name: str) -> StructDecl:
        node = StructDecl(name=name)
        self.match("lbrace")
        self.skip_semis()
        while self.current() != "rbrace":
            names = [str(self.match("identifier"))]
            while self.current() == "comma":
                self.next()
                names.append(str(self.match("identifier")))
            typ = self.type_spec() if self.is_type_start() else None
            for nm in names:
                node.fields.append(StructField(name=nm, type=typ))
            self.end_statement()
        self.match("rbrace")
        return node

    def interface_body(self, name: str) -> InterfaceDecl:
        node = InterfaceDecl(name=name)
        self.match("lbrace")
        self.skip_semis()
        while self.current() != "rbrace":
            m = InterfaceMethod(name=str(self.match("identifier")))
            self.match("lparen")
            m.params = self.parameter_list()
            self.match("rparen")
            m.return_type = self.result_type()
            node.methods.append(m)
            self.end_statement()
        self.match("rbrace")
        return node

    # ------------------------------------------------------------ типы
    def type_spec(self) -> str:
        t = self.current()
        if t == "times":
            self.next()
            return "*" + self.type_spec()
        if t == "lbracket":
            self.next()
            if self.current() == "number":
                size = self.match("number")
                if not isinstance(size, int):
                    raise self.error("Размер массива должен быть целым числом")
                self.match("rbracket")
                return f"[{size}]{self.type_spec()}"
            if self.current() != "rbracket":
                raise self.error("Размер массива должен быть числовым литералом")
            self.match("rbracket")
            return f"[]{self.type_spec()}"
        if t == "map":
            self.next()
            self.match("lbracket")
            key_type = self.type_spec()
            self.match("rbracket")
            value_type = self.type_spec()
            return f"map[{key_type}]{value_type}"
        if t == "chan":
            raise self.error("Не поддерживается: каналы (chan)")
        return str(self.match("identifier"))

    def is_type_start(self) -> bool:
        return self.current() in ("identifier", "lbracket", "map", "times", "chan")

    # ------------------------------------------------------------ управление
    def if_statement(self) -> IfStatement:
        self.match("if")
        init = None
        with self.no_lits():
            first = self.simple_statement()
            if self.current() == "semicolon":
                self.next()
                init = first
                cond = self.expression()
            else:
                cond = self._as_condition(first)
        body = self.block()
        orelse: Any = None
        if self.current() == "else":
            self.next()
            if self.current() == "if":
                orelse = self.if_statement()
            elif self.current() == "lbrace":
                orelse = self.block()
            else:
                raise self.error("После else ожидался if или '{'")
        return IfStatement(init=init, condition=cond, body=body, orelse=orelse)

    def _as_condition(self, stmt: Any) -> Any:
        if not isinstance(stmt, ExpressionStatement):
            raise self.error("Ожидалось условие (выражение)")
        return stmt.expression

    def for_statement(self) -> Any:
        self.match("for")
        init = cond = post = rng = None
        with self.no_lits():
            if self.current() == "lbrace":
                pass  # for { ... }
            elif self.current() == "range":  # for range x { ... }
                self.next()
                rng = RangeStatement(iterable=self.expression())
            else:
                first = None if self.current() == "semicolon" else self.simple_statement(allow_range=True)
                if isinstance(first, RangeStatement):
                    rng = first
                elif self.current() == "semicolon":  # for init; cond; post { ... }
                    self.next()
                    init = first
                    if self.current() != "semicolon":
                        cond = self.expression()
                    self.match("semicolon")
                    if self.current() != "lbrace":
                        post = self.simple_statement()
                else:
                    cond = self._as_condition(first)
        body = self.block()
        if rng is not None:
            rng.body = body
            return rng
        return ForStatement(init=init, condition=cond, post=post, body=body)

    def switch_statement(self) -> SwitchStatement:
        self.match("switch")
        init = expr = None
        with self.no_lits():
            if self.current() != "lbrace":
                first = None if self.current() == "semicolon" else self.simple_statement()
                if self.current() == "semicolon":
                    self.next()
                    init = first
                    if self.current() != "lbrace":
                        expr = self.expression()
                else:
                    expr = self._as_condition(first)
        self.match("lbrace")
        cases: List[CaseClause] = []
        self.skip_semis()
        while self.current() in ("case", "default"):
            if self.current() == "case":
                self.next()
                with self.lits_ok():
                    values = self.expression_list()
                self.match("colon")
                cases.append(CaseClause(values=values, body=self.statement_block()))
            else:
                self.next()
                self.match("colon")
                cases.append(CaseClause(is_default=True, body=self.statement_block()))
        self.match("rbrace")
        return SwitchStatement(init=init, expression=expr, cases=cases)

    def return_statement(self) -> ReturnStatement:
        self.match("return")
        value = None
        if self.current() not in ("semicolon", "rbrace", "case", "default", "#"):
            items = self.expression_list()
            value = items[0] if len(items) == 1 else TupleExpr(items=items)
        return ReturnStatement(value=value)

    # ------------------------------------------------------------ выражения
    def expression(self) -> Any:
        return self.binary(1)

    def binary(self, min_prec: int) -> Any:
        left = self.unary()
        while True:
            op = self.current()
            prec = BINARY_PRECEDENCE.get(op)
            if prec is None or prec < min_prec:
                break
            self.next()
            right = self.binary(prec + 1)
            left = BinaryOp(op=op, left=left, right=right)
        return left

    def unary(self) -> Any:
        t = self.current()
        if t in ("minus", "plus", "not", "bitxor", "bitand", "times"):
            self.next()
            return UnaryOp(op=t, operand=self.unary())
        if t == "arrow":
            raise self.error("Не поддерживается: операция <- (каналы)")
        return self.postfix()

    def postfix(self) -> Any:
        node = self.operand()
        while True:
            t = self.current()
            if t == "lbracket":
                self.next()
                with self.lits_ok():
                    low = None
                    if self.current() != "colon":
                        low = self.expression()
                    if self.current() == "colon":
                        self.next()
                        high = None if self.current() == "rbracket" else self.expression()
                        self.match("rbracket")
                        node = SliceExpr(base=node, low=low, high=high)
                    else:
                        self.match("rbracket")
                        node = IndexExpr(base=node, index=low)
            elif t == "dot":
                self.next()
                name = str(self.match("identifier"))
                if self.current() == "lparen":
                    node = MethodCall(receiver=node, method=name, args=self.call_args())
                else:
                    node = FieldExpr(base=node, name=name)
            else:
                break
        return node

    def call_args(self) -> List[Any]:
        self.match("lparen")
        args: List[Any] = []
        with self.lits_ok():
            self.skip_semis()
            while self.current() != "rparen":
                args.append(self.expression())
                if self.current() == "comma":
                    self.next()
                    self.skip_semis()
                else:
                    self.skip_semis()
                    break
        self.match("rparen")
        return args

    def operand(self) -> Any:
        t = self.current()
        if t == "identifier":
            if self.peek() == "lparen":
                name = str(self.match("identifier"))
                return FunctionCall(name=name, args=self.call_args())
            name = str(self.match("identifier"))
            if self.current() == "lbrace" and self.no_lit == 0:
                return self.composite_body(name)
            return Identifier(name=name)
        if t == "number":
            return NumberLiteral(value=self.match("number"))
        if t == "rune":
            return RuneLiteral(value=self.match("rune"))
        if t == "string":
            return StringLiteral(value=self.match("string"))
        if t == "lparen":
            self.next()
            with self.lits_ok():
                node = self.expression()
            self.match("rparen")
            return node
        if t in ("lbracket", "map"):
            typ = self.type_spec()
            if self.current() == "lbrace":
                return self.composite_body(typ)
            if self.current() == "lparen":
                raise self.error("Приведение к составному типу не поддерживается")
            return TypeExpr(type=typ)
        if t == "func":
            raise self.error("Функциональные литералы не поддерживаются")
        if t in UNSUPPORTED_KEYWORDS:
            raise self.error(f"Не поддерживается: {UNSUPPORTED_KEYWORDS[t]}")
        raise self.error(f"Ошибка выражения: {t}")

    def composite_body(self, typ: Optional[str]) -> CompositeLit:
        self.match("lbrace")
        elements: List[Any] = []
        with self.lits_ok():
            self.skip_semis()
            while self.current() != "rbrace":
                el = self.element()
                if self.current() == "colon":
                    self.next()
                    el = KeyedElement(key=el, value=self.element())
                elements.append(el)
                if self.current() == "comma":
                    self.next()
                    self.skip_semis()
                else:
                    self.skip_semis()
                    break
        self.match("rbrace")
        return CompositeLit(type=typ, elements=elements)

    def element(self) -> Any:
        if self.current() == "lbrace":
            return self.composite_body(None)
        return self.expression()
