from dataclasses import dataclass, field
from typing import Any, List, Optional, Union
from golang_python_translator.lexer import Token


@dataclass(repr=False)
class Node:
    """Базовый узел AST."""
    def __repr__(self) -> str:
        return self._fmt(0)

    def _fmt(self, depth: int) -> str:
        indent = "  " * depth
        name = self.__class__.__name__
        children = {k: v for k, v in self.__dict__.items() if v is not None and v != []}
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


@dataclass(repr=False)
class Program(Node):
    package: Optional[str] = None
    imports: List[str] = field(default_factory=list)
    functions: List[Any] = field(default_factory=list)


@dataclass(repr=False)
class FunctionDecl(Node):
    name: Optional[str] = None
    params: List[Any] = field(default_factory=list)
    return_type: Optional[str] = None
    body: Optional[Node] = None


@dataclass(repr=False)
class Param(Node):
    name: Optional[str] = None
    type: Optional[str] = None


@dataclass(repr=False)
class Block(Node):
    statements: List[Any] = field(default_factory=list)


@dataclass(repr=False)
class VarDecl(Node):
    name: Optional[str] = None
    type: Optional[str] = None
    value: Any = None


@dataclass(repr=False)
class ConstDecl(Node):
    name: Optional[str] = None
    value: Any = None


@dataclass(repr=False)
class Assignment(Node):
    name: Optional[str] = None
    op: Optional[str] = None
    value: Any = None


@dataclass(repr=False)
class IfStatement(Node):
    condition: Any = None
    body: Any = None


@dataclass(repr=False)
class ForStatement(Node):
    condition: Any = None
    body: Any = None


@dataclass(repr=False)
class RangeStatement(Node):
    iterable: Any = None


@dataclass(repr=False)
class ReturnStatement(Node):
    value: Any = None


@dataclass(repr=False)
class BreakStatement(Node):
    pass


@dataclass(repr=False)
class ContinueStatement(Node):
    pass


@dataclass(repr=False)
class SwitchStatement(Node):
    expression: Any = None
    cases: List[Any] = field(default_factory=list)


@dataclass(repr=False)
class SelectStatement(Node):
    cases: List[Any] = field(default_factory=list)


@dataclass(repr=False)
class CaseClause(Node):
    value: Any = None
    body: List[Any] = field(default_factory=list)


@dataclass(repr=False)
class FunctionCall(Node):
    name: Optional[str] = None
    args: List[Any] = field(default_factory=list)


@dataclass(repr=False)
class MethodCall(Node):
    receiver: Optional[str] = None
    method: Optional[str] = None
    args: List[Any] = field(default_factory=list)


@dataclass(repr=False)
class BinaryOp(Node):
    op: Optional[str] = None
    left: Any = None
    right: Any = None


@dataclass(repr=False)
class UnaryOp(Node):
    op: Optional[str] = None
    operand: Any = None


@dataclass(repr=False)
class Identifier(Node):
    name: Optional[str] = None


@dataclass(repr=False)
class NumberLiteral(Node):
    value: Any = None


@dataclass(repr=False)
class StringLiteral(Node):
    value: Optional[str] = None


@dataclass(repr=False)
class IndexExpr(Node):
    index: Any = None


@dataclass(repr=False)
class TypeDecl(Node):
    name: Optional[str] = None
    base: Optional[str] = None


@dataclass(repr=False)
class StructDecl(Node):
    name: Optional[str] = None
    fields: List[Any] = field(default_factory=list)


@dataclass(repr=False)
class StructField(Node):
    name: Optional[str] = None
    type: Optional[str] = None


@dataclass(repr=False)
class InterfaceDecl(Node):
    name: Optional[str] = None
    methods: List[str] = field(default_factory=list)


@dataclass(repr=False)
class MapDecl(Node):
    key_type: Optional[str] = None
    value_type: Optional[str] = None


@dataclass(repr=False)
class ExpressionStatement(Node):
    expression: Any = None


class Parser:
    def __init__(self, tokens: List[Token]) -> None:
        self.tokens: List[Token] = tokens
        self.pos: int = 0

    def current_token(self) -> Token:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return Token("#", "#", -1)

    def current(self) -> str:
        return self.current_token().type

    def next(self) -> None:
        self.pos += 1

    def peek(self) -> str:
        if self.pos + 1 < len(self.tokens):
            return self.tokens[self.pos + 1].type
        return "#"

    def match(self, expected: str) -> Any:
        tok = self.current_token()
        if tok.type == expected:
            self.next()
            return tok.value
        else:
            raise SyntaxError(f"Ожидался '{expected}', найден '{tok.type}' ({tok.value})")

    def parse(self) -> Program:
        tree = self.program()
        print("✔ OK")
        print("\n=== AST ===")
        print(tree._fmt(0))
        return tree

    def program(self) -> Program:
        node = Program()
        node.package = str(self.package_declaration())
        node.imports = self.import_declaration()

        while self.current() == "func":
            node.functions.append(self.function_declaration())

        return node

    def package_declaration(self) -> Any:
        self.match("package")
        return self.match("identifier")

    def import_declaration(self) -> List[str]:
        self.match("import")
        return [str(self.match("string"))]

    def function_declaration(self) -> FunctionDecl:
        node = FunctionDecl()
        self.match("func")
        node.name = str(self.match("identifier"))

        self.match("lparen")
        node.params = self.parameter_list()
        self.match("rparen")

        if self.is_type_start():
            node.return_type = self.type_spec()

        node.body = self.block()
        return node

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

        while self.current() not in ["rbrace", "#"]:
            node.statements.append(self.statement())

        self.match("rbrace")
        return node

    def statement(self) -> Any:
        t = self.current()

        if t == "var":
            return self.variable_declaration()
        elif t == "const":
            return self.const_declaration()
        elif t == "interface":
            return self.interface_declaration()
        elif t == "map":
            return self.map_declaration()
        elif t == "type":
            return self.type_declaration()
        elif t == "struct":
            return self.struct_declaration()
        elif t == "if":
            return self.if_statement()
        elif t == "for":
            return self.for_statement()
        elif t == "switch":
            return self.switch_statement()
        elif t == "select":
            return self.select_statement()
        elif t == "range":
            return self.range_statement()
        elif t == "return":
            return self.return_statement()
        elif t == "break":
            self.match("break")
            return BreakStatement()
        elif t == "continue":
            self.match("continue")
            return ContinueStatement()
        elif t == "identifier":
            if self.peek() == "dot":
                return self.function_call_with_dot()
            elif self.peek() == "lparen":
                return self.function_call()
            elif self.peek() in [
                "assign", "declare",
                "plus_assign", "minus_assign",
                "times_assign", "divide_assign",
                "mod_assign"
            ]:
                return self.assignment()
            else:
                return self.expression_statement()
        else:
            return self.expression_statement()

    def type_spec(self) -> str:
        if self.current() == "lbracket":
            self.match("lbracket")
            if self.current() == "number":
                size = self.match("number")
                self.match("rbracket")
                elem = self.type_spec()
                return f"[{size}]{elem}"
            else:
                self.match("rbracket")
                elem = self.type_spec()
                return f"[]{elem}"
        elif self.current() == "map":
            self.match("map")
            self.match("lbracket")
            key_type = self.type_spec()
            self.match("rbracket")
            value_type = self.type_spec()
            return f"map[{key_type}]{value_type}"
        else:
            return str(self.match("identifier"))

    def is_type_start(self) -> bool:
        return self.current() in ("identifier", "lbracket", "map")

    def variable_declaration(self) -> VarDecl:
        node = VarDecl()
        self.match("var")
        node.name = str(self.match("identifier"))

        if self.is_type_start():
            node.type = self.type_spec()

        if self.current() == "assign":
            self.match("assign")
            node.value = self.expression()
        return node

    def const_declaration(self) -> ConstDecl:
        node = ConstDecl()
        self.match("const")
        node.name = str(self.match("identifier"))
        if self.current() == "assign":
            self.match("assign")
            node.value = self.expression()
        return node

    def interface_declaration(self) -> InterfaceDecl:
        node = InterfaceDecl()
        self.match("interface")
        node.name = str(self.match("identifier"))
        self.match("lbrace")
        while self.current() != "rbrace":
            method = str(self.match("identifier"))
            self.match("lparen")
            self.match("rparen")
            node.methods.append(method)
        self.match("rbrace")
        return node

    def map_declaration(self) -> MapDecl:
        node = MapDecl()
        self.match("map")
        self.match("lbracket")
        node.key_type = str(self.match("identifier"))
        self.match("rbracket")
        node.value_type = str(self.match("identifier"))
        return node

    def type_declaration(self) -> TypeDecl:
        node = TypeDecl()
        self.match("type")
        node.name = str(self.match("identifier"))
        node.base = str(self.match("identifier"))
        return node

    def struct_declaration(self) -> StructDecl:
        node = StructDecl()
        self.match("struct")
        node.name = str(self.match("identifier"))
        self.match("lbrace")
        while self.current() != "rbrace":
            f = StructField()
            f.name = str(self.match("identifier"))
            if self.current() == "identifier":
                f.type = str(self.match("identifier"))
            node.fields.append(f)
        self.match("rbrace")
        return node

    def range_statement(self) -> RangeStatement:
        self.match("range")
        return RangeStatement(iterable=self.expression())

    def if_statement(self) -> IfStatement:
        self.match("if")
        condition = self.expression()
        body = self.block()
        return IfStatement(condition=condition, body=body)

    def for_statement(self) -> ForStatement:
        self.match("for")
        condition = self.expression()
        body = self.block()
        return ForStatement(condition=condition, body=body)

    def switch_statement(self) -> SwitchStatement:
        self.match("switch")
        expr = self.expression()
        self.match("lbrace")
        cases: List[CaseClause] = []

        while self.current() in ["case", "default"]:
            if self.current() == "case":
                self.match("case")
                val = self.expression()
                self.match("colon")
                body = self.statement_block()
                cases.append(CaseClause(value=val, body=body))
            else:
                self.match("default")
                self.match("colon")
                body = self.statement_block()
                cases.append(CaseClause(value=None, body=body))

        self.match("rbrace")
        return SwitchStatement(expression=expr, cases=cases)

    def select_statement(self) -> SelectStatement:
        self.match("select")
        self.match("lbrace")
        cases: List[CaseClause] = []

        while self.current() in ["case", "default"]:
            if self.current() == "case":
                self.match("case")
                val = self.expression()
                self.match("colon")
                body = self.statement_block()
                cases.append(CaseClause(value=val, body=body))
            else:
                self.match("default")
                self.match("colon")
                body = self.statement_block()
                cases.append(CaseClause(value=None, body=body))

        self.match("rbrace")
        return SelectStatement(cases=cases)

    def return_statement(self) -> ReturnStatement:
        self.match("return")
        value = None
        if self.current() not in ["rbrace", "#"]:
            value = self.expression()
        return ReturnStatement(value=value)

    def function_call(self) -> FunctionCall:
        name = str(self.match("identifier"))
        self.match("lparen")
        args: List[Any] = []
        if self.current() != "rparen":
            args.append(self.expression())
            while self.current() == "comma":
                self.match("comma")
                args.append(self.expression())
        self.match("rparen")
        return FunctionCall(name=name, args=args)

    def function_call_with_dot(self) -> MethodCall:
        receiver = str(self.match("identifier"))
        self.match("dot")
        method = str(self.match("identifier"))
        self.match("lparen")
        args: List[Any] = []
        if self.current() != "rparen":
            args.append(self.expression())
            while self.current() == "comma":
                self.match("comma")
                args.append(self.expression())
        self.match("rparen")
        return MethodCall(receiver=receiver, method=method, args=args)

    def expression(self) -> Any:
        return self.logical_or()

    def logical_or(self) -> Any:
        left = self.logical_and()
        while self.current() == "or":
            op = self.current()
            self.next()
            right = self.logical_and()
            left = BinaryOp(op=op, left=left, right=right)
        return left

    def logical_and(self) -> Any:
        left = self.equality()
        while self.current() == "and":
            op = self.current()
            self.next()
            right = self.equality()
            left = BinaryOp(op=op, left=left, right=right)
        return left

    def equality(self) -> Any:
        left = self.relational()
        while self.current() in ["eq", "ne"]:
            op = self.current()
            self.next()
            right = self.relational()
            left = BinaryOp(op=op, left=left, right=right)
        return left

    def relational(self) -> Any:
        left = self.additive()
        while self.current() in ["lt", "gt", "le", "ge"]:
            op = self.current()
            self.next()
            right = self.additive()
            left = BinaryOp(op=op, left=left, right=right)
        return left

    def additive(self) -> Any:
        left = self.multiplicative()
        while self.current() in ["plus", "minus"]:
            op = self.current()
            self.next()
            right = self.multiplicative()
            left = BinaryOp(op=op, left=left, right=right)
        return left

    def multiplicative(self) -> Any:
        left = self.unary()
        while self.current() in ["times", "divide", "mod"]:
            op = self.current()
            self.next()
            right = self.unary()
            left = BinaryOp(op=op, left=left, right=right)
        return left

    def unary(self) -> Any:
        if self.current() in ["minus", "not"]:
            op = self.current()
            self.next()
            return UnaryOp(op=op, operand=self.unary())
        return self.factor()

    def assignment(self) -> Optional[Assignment]:
        name = str(self.match("identifier"))
        op = self.current()
        if op in ["assign", "declare", "plus_assign", "minus_assign",
                  "times_assign", "divide_assign", "mod_assign"]:
            self.next()
            value = self.expression()
            return Assignment(name=name, op=op, value=value)
        return None

    def factor(self) -> Any:
        t = self.current()

        if t == "identifier":
            if self.peek() == "lparen":
                return self.function_call()
            name = str(self.match("identifier"))
            return Identifier(name=name)

        elif t == "number":
            val = self.match("number")
            return NumberLiteral(value=val)

        elif t == "string":
            val = self.match("string")
            return StringLiteral(value=val)

        elif t == "lparen":
            self.match("lparen")
            node = self.expression()
            self.match("rparen")
            return node

        elif t == "lbracket":
            self.match("lbracket")
            node = self.expression()
            self.match("rbracket")
            return IndexExpr(index=node)

        else:
            raise SyntaxError(f"Ошибка выражения: {t}")

    def expression_statement(self) -> ExpressionStatement:
        return ExpressionStatement(expression=self.expression())

    def statement_block(self) -> List[Any]:
        stmts: List[Any] = []
        while self.current() not in ["case", "default", "rbrace", "#"]:
            stmts.append(self.statement())
        return stmts