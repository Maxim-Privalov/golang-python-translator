"""Мелкие конструкторы узлов Python AST (чтобы лоуэринг читался как код, а не как ast.Call(...))."""
import ast
import copy
import re
from typing import Any, List, Optional, Sequence, Tuple, Union

BIN = {
    "+": ast.Add, "-": ast.Sub, "*": ast.Mult, "/": ast.Div, "//": ast.FloorDiv,
    "%": ast.Mod, "&": ast.BitAnd, "|": ast.BitOr, "^": ast.BitXor,
    "<<": ast.LShift, ">>": ast.RShift,
}
CMP = {
    "==": ast.Eq, "!=": ast.NotEq, "<": ast.Lt, "<=": ast.LtE, ">": ast.Gt, ">=": ast.GtE,
    "is": ast.Is, "is not": ast.IsNot, "in": ast.In,
}
UNARY = {"-": ast.USub, "+": ast.UAdd, "~": ast.Invert, "not": ast.Not}

Expr = ast.expr
Stmt = ast.stmt
ExprOrName = Union[ast.expr, str]


def name(id_: str, store: bool = False) -> ast.Name:
    return ast.Name(id=id_, ctx=ast.Store() if store else ast.Load())


def const(v: Any) -> ast.Constant:
    return ast.Constant(value=v)


def attr(value: ExprOrName, a: str, store: bool = False) -> ast.Attribute:
    v = name(value) if isinstance(value, str) else value
    return ast.Attribute(value=v, attr=a, ctx=ast.Store() if store else ast.Load())


def call(func: ExprOrName, *args: ast.expr, kw: Optional[Sequence[Tuple[str, ast.expr]]] = None) -> ast.Call:
    f = name(func) if isinstance(func, str) else func
    return ast.Call(func=f, args=list(args), keywords=[ast.keyword(arg=k, value=v) for k, v in (kw or [])])


def subscript(value: ast.expr, index: ast.expr, store: bool = False) -> ast.Subscript:
    return ast.Subscript(value=value, slice=index, ctx=ast.Store() if store else ast.Load())


def slice_(value: ast.expr, lo: Optional[ast.expr], hi: Optional[ast.expr]) -> ast.Subscript:
    return subscript(value, ast.Slice(lower=lo, upper=hi, step=None))


def tup(items: Sequence[ast.expr], store: bool = False) -> ast.Tuple:
    return ast.Tuple(elts=list(items), ctx=ast.Store() if store else ast.Load())


def lst(items: Sequence[ast.expr]) -> ast.List:
    return ast.List(elts=list(items), ctx=ast.Load())


def dct(keys: Sequence[ast.expr], values: Sequence[ast.expr]) -> ast.Dict:
    return ast.Dict(keys=list(keys), values=list(values))


def binop(left: ast.expr, op: str, right: ast.expr) -> ast.BinOp:
    return ast.BinOp(left=left, op=BIN[op](), right=right)


def cmpop(left: ast.expr, op: str, right: ast.expr) -> ast.Compare:
    return ast.Compare(left=left, ops=[CMP[op]()], comparators=[right])


def boolop(op: str, values: Sequence[ast.expr]) -> ast.BoolOp:
    return ast.BoolOp(op=ast.Or() if op == "or" else ast.And(), values=list(values))


def unary(op: str, operand: ast.expr) -> ast.UnaryOp:
    return ast.UnaryOp(op=UNARY[op](), operand=operand)


def listcomp(elt: ast.expr, count: ast.expr) -> ast.ListComp:
    gen = ast.comprehension(target=name("_", True), iter=call("range", count), ifs=[], is_async=0)
    return ast.ListComp(elt=elt, generators=[gen])


def parse_expr(src: str) -> ast.expr:
    return ast.parse(src, mode="eval").body


def fill(template: str, args: Sequence[ast.expr]) -> ast.expr:
    """Подставляет готовые выражения в шаблон вида '{0}.upper()'."""
    tree = parse_expr(re.sub(r"\{(\d)\}", r"__p\1", template))

    class Sub(ast.NodeTransformer):
        def visit_Name(self, node: ast.Name) -> ast.AST:
            m = re.fullmatch(r"__p(\d)", node.id)
            return copy.deepcopy(args[int(m.group(1))]) if m else node

    return Sub().visit(tree)


def template_arity(template: str) -> int:
    idx = [int(i) for i in re.findall(r"\{(\d)\}", template)]
    return max(idx) + 1 if idx else 0


# --- инструкции ---
def assign(target: ast.expr, value: ast.expr) -> ast.Assign:
    return ast.Assign(targets=[target], value=value)


def ann_assign(target: ast.expr, annotation: ast.expr, value: Optional[ast.expr]) -> ast.AnnAssign:
    if not isinstance(target, (ast.Name, ast.Attribute, ast.Subscript)):
        raise TypeError(f"Invalid AnnAssign target: {type(target).__name__}")
    
    return ast.AnnAssign(target=target, annotation=annotation, value=value, simple=1)


def expr_stmt(e: ast.expr) -> ast.Expr:
    return ast.Expr(value=e)


def funcdef(fname: str, args: Sequence[Tuple[str, Optional[ast.expr]]], body: List[ast.stmt],
            returns: Optional[ast.expr]) -> ast.FunctionDef:
    arguments = ast.arguments(
        posonlyargs=[], args=[ast.arg(arg=a, annotation=ann) for a, ann in args],
        vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[],
    )
    return ast.FunctionDef(name=fname, args=arguments, body=body, decorator_list=[],
                           returns=returns, type_params=[])


def classdef(cname: str, body: List[ast.stmt], decorators: Sequence[ast.expr] = (),
             bases: Sequence[ast.expr] = ()) -> ast.ClassDef:
    return ast.ClassDef(name=cname, bases=list(bases), keywords=[], body=body,
                        decorator_list=list(decorators), type_params=[])


def import_(mod: str) -> ast.Import:
    return ast.Import(names=[ast.alias(name=mod)])


def import_from(mod: str, names: Sequence[str]) -> ast.ImportFrom:
    return ast.ImportFrom(module=mod, names=[ast.alias(name=n) for n in names], level=0)
