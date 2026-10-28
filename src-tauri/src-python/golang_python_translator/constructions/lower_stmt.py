"""Этап 2: инструкции Go -> список инструкций Python AST."""
import ast
from typing import Any, List, Optional

from golang_python_translator import parser as G
from golang_python_translator.constructions import pyast as P
from golang_python_translator.constructions.gotypes import (
    ARRAY_RE, AUG_ASSIGN, INTS, SIZED, WRAP_OPS, CodeGenError, py_ident, split_map,
    split_tuple, strip_ptr,
)
from golang_python_translator.constructions.lower_base import gt
from golang_python_translator.constructions.lower_expr import ExprLowerer
from golang_python_translator.constructions.symbols import param_types, walk


class StmtLowerer(ExprLowerer):
    def s(self, n: Any) -> List[ast.stmt]:
        h = getattr(self, "s_" + type(n).__name__, None)
        if h is None:
            raise CodeGenError(f"Неизвестный узел-инструкция: {type(n).__name__}")
        return h(n)

    def block(self, stmts: Any, ctx: Optional[str] = None, strip_break: bool = False,
              tail: Any = None) -> List[ast.stmt]:
        """Тело блока; для пустого тела - pass."""
        items = list(getattr(stmts, "statements", stmts) or [])
        if strip_break and items and isinstance(items[-1], G.BreakStatement):
            items.pop()
        if ctx:
            self.contexts.append(ctx)
        out: List[ast.stmt] = []
        for st in items:
            out += self.s(st)
        if tail is not None:
            out += self.s(tail)
        if ctx:
            self.contexts.pop()
        return out or [ast.Pass()]

    # ------------------------------------------------------------ функции
    def mutates(self, body: Any, name: str) -> bool:
        """Присваивает ли тело поле/элемент переменной name (для приёмника-значения)."""
        for n in walk(body):
            if isinstance(n, G.Assignment):
                items = n.target.items if isinstance(n.target, G.TupleExpr) else [n.target]
                for it in items:
                    root = it
                    while isinstance(root, (G.FieldExpr, G.IndexExpr)):
                        root = root.base
                    if root is not it and isinstance(root, G.Identifier) and root.name == name:
                        return True
        return False

    def lower_function(self, fn: G.FunctionDecl) -> ast.FunctionDef:
        recv = fn.receiver
        rtype = strip_ptr(recv.type) if recv else None
        as_method = recv is not None and rtype in self.T.structs

        args = []
        if recv is not None:
            args.append(("self", None) if as_method else (fn.py_recv, self.ann(recv.type)))
        for py, t in zip(fn.py_params, param_types(fn.params)):
            args.append((py, self.ann(t) if t else None))

        self.cur_ret = fn.return_type
        body: List[ast.stmt] = []
        if as_method and not (recv.type or "").startswith("*") and self.mutates(fn.body, recv.name or ""):
            self.imp("copy")  # приёмник-значение: изменения должны быть локальными
            body.append(P.assign(P.name("self", True), P.call(P.attr("copy", "deepcopy"), P.name("self"))))
        body += self.block(fn.body)
        if fn.assigned_globals:
            body.insert(0, ast.Global(names=list(fn.assigned_globals)))
        self.cur_ret = None

        name = py_ident(fn.name) if (recv is None or as_method) else f"{py_ident(rtype)}_{fn.name}"
        returns = self.ann(fn.return_type) if fn.return_type else P.const(None)
        return P.funcdef(name, args, body, returns)

    # ------------------------------------------------------------ простые инструкции
    def s_FunctionDecl(self, n: G.FunctionDecl) -> List[ast.stmt]:
        return [self.lower_function(n)]

    def s_Block(self, n: G.Block) -> List[ast.stmt]:
        out: List[ast.stmt] = []
        for st in n.statements:
            out += self.s(st)
        return out

    def s_DeclGroup(self, n: G.DeclGroup) -> List[ast.stmt]:
        out: List[ast.stmt] = []
        for d in n.decls:
            out += self.s(d)
        return out

    def s_StructDecl(self, n: Any) -> List[ast.stmt]:
        return []  # типы поднимаются на уровень модуля

    s_InterfaceDecl = s_TypeDecl = s_StructDecl

    def s_VarDecl(self, n: G.VarDecl) -> List[ast.stmt]:
        t = n.type
        if n.value is not None:
            value: Optional[ast.expr] = self.val(n.value, t)
        else:
            value = self.zero(t) if t else P.const(None)
        target = P.name(n.py_name, True)
        if t:
            return [P.ann_assign(target, self.ann(t), value)]
        return [P.assign(target, value)]

    def s_ConstDecl(self, n: G.ConstDecl) -> List[ast.stmt]:
        value = self.val(n.value, n.type) if n.value is not None else P.const(None)
        return [P.assign(P.name(n.py_name, True), value)]

    def s_ExpressionStatement(self, n: G.ExpressionStatement) -> List[ast.stmt]:
        e = n.expression
        if (isinstance(e, G.FunctionCall) and e.name == "panic" and len(e.args) == 1
                and not e.is_var and "panic" not in self.sym.funcs):
            msg = self.strexpr(e.args[0])
            exc = P.call("RuntimeError", P.binop(P.const("panic: "), "+", msg))
            return [ast.Raise(exc=exc, cause=None)]
        return [P.expr_stmt(self.e(e))]

    def s_ReturnStatement(self, n: G.ReturnStatement) -> List[ast.stmt]:
        if n.value is None:
            return [ast.Return(value=None)]
        rets = split_tuple(self.cur_ret)
        if isinstance(n.value, G.TupleExpr):
            vals = [self.val(v, rets[i] if rets and i < len(rets) else None)
                    for i, v in enumerate(n.value.items)]
            return [ast.Return(value=P.tup(vals))]
        return [ast.Return(value=self.val(n.value, None if rets else self.cur_ret))]

    def s_BreakStatement(self, n: Any) -> List[ast.stmt]:
        if self.contexts and self.contexts[-1] == "switch":
            self.warn("break внутри switch (не в конце case) не перенесён")
            return [ast.Pass()]
        return [ast.Break()]

    def s_ContinueStatement(self, n: Any) -> List[ast.stmt]:
        # continue в Go сначала выполняет post-инструкцию цикла for
        post = self.loop_posts[-1] if self.loop_posts else None
        return (self.s(post) if post is not None else []) + [ast.Continue()]

    # ------------------------------------------------------------ присваивание
    def lv(self, n: Any) -> ast.expr:
        if isinstance(n, G.Identifier):
            return P.name(n.py_name, True)
        if isinstance(n, G.IndexExpr):
            bt = gt(n.base)
            key = self.val(n.index, split_map(self.T.base(bt))[0]) if self.T.is_map(bt) else self.e(n.index)
            return P.subscript(self.e(n.base), key, True)
        if isinstance(n, G.FieldExpr):
            return P.attr(self.e(n.base), py_ident(n.name), True)
        raise CodeGenError(f"Недопустимая цель присваивания: {type(n).__name__}")

    def s_Assignment(self, n: G.Assignment) -> List[ast.stmt]:
        if n.op in ("assign", "declare"):
            return self.assign(n)
        if n.op not in AUG_ASSIGN:
            raise CodeGenError(f"Неизвестный оператор присваивания: {n.op}")
        bop, pyop = AUG_ASSIGN[n.op]
        tgt = n.target
        ttype = gt(tgt)
        base_map = isinstance(tgt, G.IndexExpr) and self.T.is_map(gt(tgt.base))
        if pyop is None or (self.T.base(ttype) in SIZED and bop in WRAP_OPS) or base_map:
            syn = G.BinaryOp(op=bop, left=tgt, right=n.value)
            syn.go_type = ttype
            return [P.assign(self.lv(tgt), self.e(syn))]
        return [ast.AugAssign(target=self.lv(tgt), op=P.BIN[pyop](), value=self.val(n.value, ttype))]

    def assign(self, n: G.Assignment) -> List[ast.stmt]:
        declare = n.op == "declare"
        targets = list(n.target.items) if isinstance(n.target, G.TupleExpr) else [n.target]
        values = list(n.value.items) if isinstance(n.value, G.TupleExpr) else [n.value]

        # *p = v
        if len(targets) == 1 and isinstance(targets[0], G.UnaryOp) and targets[0].op == "times":
            p = targets[0].operand
            if self.T.base(gt(p)) in self.T.structs:
                self.imp("copy")
                copied = P.call(P.attr("copy", "deepcopy"), self.e(values[0]))
                upd = P.call(P.attr(P.attr(self.e(p), "__dict__"), "update"), P.attr(copied, "__dict__"))
                return [P.expr_stmt(upd)]
            self.warn("присваивание через указатель на не-структуру не поддержано")
            return [ast.Pass()]

        # x = append(x, ...)  ->  x.append(...)  (без копирования списка)
        if (not declare and len(targets) == 1 and len(values) == 1
                and isinstance(targets[0], G.Identifier) and isinstance(values[0], G.FunctionCall)
                and values[0].name == "append" and values[0].args and not values[0].is_var
                and "append" not in self.sym.funcs and isinstance(values[0].args[0], G.Identifier)
                and values[0].args[0].name == targets[0].name and self.T.is_slice(gt(targets[0]))):
            et = self.T.elem(gt(targets[0]))
            extra = [self.val(a, et) for a in values[0].args[1:]]
            recv = P.name(targets[0].py_name)
            if len(extra) == 1:
                return [P.expr_stmt(P.call(P.attr(recv, "append"), extra[0]))]
            if extra:
                return [P.expr_stmt(P.call(P.attr(recv, "extend"), P.lst(extra)))]
            return []

        # v, ok := m[k]
        if (len(targets) == 2 and len(values) == 1 and isinstance(values[0], G.IndexExpr)
                and self.T.is_map(gt(values[0].base))):
            kt, vt = split_map(self.T.base(gt(values[0].base)))
            m, k = self.e(values[0].base), self.val(values[0].index, kt)
            out: List[ast.stmt] = []
            if not _is_blank(targets[1]):
                out.append(P.assign(self.lv(targets[1]), P.cmpop(k, "in", m)))
            if not _is_blank(targets[0]):
                out.append(P.assign(self.lv(targets[0]), P.call(P.attr(m, "get"), k, self.zero(vt))))
            return out

        if len(values) == len(targets):
            vals = [self.val(v, None if declare else gt(t)) for t, v in zip(targets, values)]
        else:
            vals = [self.e(values[0])]
        lhs = [self.lv(t) for t in targets]
        return [P.assign(lhs[0] if len(lhs) == 1 else P.tup(lhs, True),
                         vals[0] if len(vals) == 1 else P.tup(vals))]

    # ------------------------------------------------------------ управление
    def s_IfStatement(self, n: G.IfStatement) -> List[ast.stmt]:
        out = self.s(n.init) if n.init is not None else []
        return out + [self.if_chain(n)]

    def if_chain(self, n: G.IfStatement) -> ast.If:
        orelse: List[ast.stmt] = []
        o = n.orelse
        if isinstance(o, G.IfStatement):
            orelse = (self.s(o.init) if o.init is not None else []) + [self.if_chain(o)]
        elif o is not None:
            orelse = self.block(o)
        return ast.If(test=self.e(n.condition), body=self.block(n.body), orelse=orelse)

    def s_ForStatement(self, n: G.ForStatement) -> List[ast.stmt]:
        out = self.s(n.init) if n.init is not None else []
        cond = self.e(n.condition) if n.condition is not None else P.const(True)
        self.loop_posts.append(n.post)
        body = self.block(n.body, ctx="loop", tail=n.post)
        self.loop_posts.pop()
        return out + [ast.While(test=cond, body=body, orelse=[])]

    def s_RangeStatement(self, n: G.RangeStatement) -> List[ast.stmt]:
        t = self.T.base(gt(n.iterable))
        it = self.e(n.iterable)
        key = None if _is_blank(n.key) else n.key
        value = None if _is_blank(n.value) else n.value
        k = self.lv(key) if key is not None else None
        v = self.lv(value) if value is not None else None
        blank = P.name("_", True)

        if t == "string":
            self.use("_go_range_str")
            target, iterable = P.tup([k or blank, v or blank], True), P.call("_go_range_str", it)
        elif t in INTS:
            if value is not None:
                raise CodeGenError("range по целому числу допускает только одну переменную")
            target, iterable = k or blank, P.call("range", it)
        elif t is not None and (t.startswith("[]") or ARRAY_RE.match(t)):
            if k and v:
                target, iterable = P.tup([k, v], True), P.call("enumerate", it)
            elif k:
                target, iterable = k, P.call("range", P.call("len", it))
            else:
                target, iterable = v or blank, it
        elif t is not None and t.startswith("map["):
            if k and v:
                target, iterable = P.tup([k, v], True), P.call("list", P.call(P.attr(it, "items")))
            elif v:
                target, iterable = v, P.call("list", P.call(P.attr(it, "values")))
            else:
                target, iterable = k or blank, P.call("list", it)
        else:
            self.use("_go_range")
            target, iterable = P.tup([k or blank, v or blank], True), P.call("_go_range", it)

        self.loop_posts.append(None)
        body = self.block(n.body, ctx="loop")
        self.loop_posts.pop()
        return [ast.For(target=target, iter=iterable, body=body, orelse=[])]

    def s_SwitchStatement(self, n: G.SwitchStatement) -> List[ast.stmt]:
        out = self.s(n.init) if n.init is not None else []
        subj: Optional[ast.expr] = None
        if n.expression is not None:
            if isinstance(n.expression, (G.Identifier, G.NumberLiteral, G.StringLiteral, G.RuneLiteral)):
                subj = self.e(n.expression)
            else:
                tmp = self.tmp("sw")
                out.append(P.assign(P.name(tmp, True), self.e(n.expression)))
                subj = P.name(tmp)

        def cond(c: G.CaseClause) -> ast.expr:
            conds = []
            for v in c.values:
                if subj is None:
                    conds.append(self.e(v))
                elif self._is_nil(v):
                    conds.append(P.cmpop(subj, "is", P.const(None)))
                else:
                    conds.append(P.cmpop(subj, "==", self.e(v)))
            return conds[0] if len(conds) == 1 else P.boolop("or", conds)

        default = next((c for c in n.cases if c.is_default), None)
        tail: List[ast.stmt] = self.block(default.body, "switch", True) if default else []
        for c in reversed([c for c in n.cases if not c.is_default]):
            tail = [ast.If(test=cond(c), body=self.block(c.body, "switch", True), orelse=tail)]
        return out + tail


def _is_blank(n: Any) -> bool:
    return n is None or (isinstance(n, G.Identifier) and n.name == "_")
