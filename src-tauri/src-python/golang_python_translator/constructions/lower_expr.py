"""Этап 2: выражения Go -> выражения Python AST."""
import ast
from typing import Any, List, Optional

from golang_python_translator import parser as G
from golang_python_translator.constructions import pyast as P
from golang_python_translator.constructions.gotypes import (
    ARRAY_RE, BINOP_SYM, CMP_SYM, COMPLEXES, CONVERSIONS, FLOATS, INTS, SIZED, WRAP_OPS,
    CodeGenError, go_string_value, py_ident, split_map,
)
from golang_python_translator.constructions.lower_base import LowerBase, gt
from golang_python_translator.constructions.stdlib import PKG_CONSTS, STDLIB

IMMUTABLE_ZEROS = {"0", "0.0", '""', "False", "None", "0j"}


class ExprLowerer(LowerBase):
    def e(self, n: Any) -> ast.expr:
        h = getattr(self, "x_" + type(n).__name__, None)
        if h is None:
            raise CodeGenError(f"Неизвестный узел-выражение: {type(n).__name__}")
        return h(n)

    def val(self, n: Any, target: Optional[str] = None) -> ast.expr:
        """Выражение с неявным int -> float и копированием структур/массивов."""
        s = self.e(n)
        t = gt(n)
        if target is not None and self.T.is_float(target):
            if isinstance(n, G.NumberLiteral) and isinstance(n.value, int):
                return P.const(float(n.value))
            if t is not None and self.T.under(t) in INTS:
                return P.call("float", s)
        is_place = isinstance(n, (G.Identifier, G.IndexExpr, G.FieldExpr)) or (
            isinstance(n, G.UnaryOp) and n.op == "times")
        if is_place and t is not None and self.T.is_value_aggregate(t):
            self.imp("copy")
            return P.call(P.attr("copy", "deepcopy"), s)  # в Go они копируются при присваивании
        return s

    # ------------------------------------------------------------ литералы и имена
    def x_Identifier(self, n: G.Identifier) -> ast.expr:
        if not n.is_var:
            consts = {"nil": None, "true": True, "false": False}
            if n.name in consts:
                return P.const(consts[n.name])
            if hasattr(n, "iota_value"):
                return P.const(n.iota_value)
        return P.name(n.py_name)

    def x_NumberLiteral(self, n: G.NumberLiteral) -> ast.expr:
        v = n.value
        if isinstance(v, str):  # комплексный литерал 3i
            return P.const(complex(v[:-1] + "j"))
        return P.const(v)

    def x_RuneLiteral(self, n: G.RuneLiteral) -> ast.expr:
        ch = chr(n.value)
        if ch.isprintable() and not 0xD800 <= n.value <= 0xDFFF:
            return P.call("ord", P.const(ch))
        return P.const(n.value)

    def x_StringLiteral(self, n: G.StringLiteral) -> ast.expr:
        return P.const(go_string_value(n.value or ""))

    def x_TupleExpr(self, n: G.TupleExpr) -> ast.expr:
        return P.tup([self.e(i) for i in n.items])

    def x_TypeExpr(self, n: G.TypeExpr) -> ast.expr:
        raise CodeGenError(f"Тип {n.type} не может использоваться как значение")

    # ------------------------------------------------------------ операторы
    def x_UnaryOp(self, n: G.UnaryOp) -> ast.expr:
        if n.op in ("bitand", "times"):  # &x и *p: указатель на структуру - сама ссылка
            if not isinstance(n.operand, G.CompositeLit) and self.T.base(gt(n.operand)) not in self.T.structs:
                self.warn("указатель на не-структуру не поддержан: заменён самим значением")
            return self.e(n.operand)
        x = self.e(n.operand)
        if n.op == "plus":
            return x
        if n.op == "minus":
            return P.unary("-", x)
        if n.op == "not":
            return P.unary("not", x)
        if n.op == "bitxor":
            ut = self.T.base(gt(n.operand))
            if ut in SIZED and not SIZED[ut][1]:  # беззнаковые: ^x не отрицательно
                self.use("_go_wrap")
                return P.call("_go_wrap", P.unary("~", x), P.const(SIZED[ut][0]), P.const(False))
            return P.unary("~", x)
        raise CodeGenError(f"Неизвестный унарный оператор: {n.op}")

    @staticmethod
    def _is_nil(n: Any) -> bool:
        return isinstance(n, G.Identifier) and n.name == "nil" and not n.is_var

    def x_BinaryOp(self, n: G.BinaryOp) -> ast.expr:
        op = n.op
        if op in ("eq", "ne") and (self._is_nil(n.left) or self._is_nil(n.right)):
            other = n.right if self._is_nil(n.left) else n.left
            return P.cmpop(self.e(other), "is" if op == "eq" else "is not", P.const(None))

        lu, ru = self.T.under(gt(n.left)), self.T.under(gt(n.right))
        sym = BINOP_SYM.get(op)
        if op in ("divide", "mod"):
            both_int = lu in INTS and ru in INTS
            if both_int or (op == "mod" and (lu in INTS or ru in INTS)):
                lits = all(isinstance(x, G.NumberLiteral) and isinstance(x.value, int) for x in (n.left, n.right))
                if lits:
                    sym = "//" if op == "divide" else "%"
                else:
                    helper = "_go_div" if op == "divide" else "_go_mod"
                    self.use(helper)
                    return P.call(helper, self.e(n.left), self.e(n.right))
            elif lu not in FLOATS and ru not in FLOATS:
                self.warn("деление/остаток с неизвестным типом операндов переведено как '/' и '%' "
                          "(в Go для int это целочисленные операции)")

        left, right = self.e(n.left), self.e(n.right)
        if op in ("or", "and"):
            res: ast.expr = P.boolop(op, [left, right])
        elif op in CMP_SYM:
            res = P.cmpop(left, CMP_SYM[op], right)
        elif op == "andnot":
            res = P.binop(left, "&", P.unary("~", right))
        else:
            res = P.binop(left, sym, right)

        result_t = self.T.base(gt(n))
        if op in WRAP_OPS and result_t in SIZED:
            bits, signed = SIZED[result_t]
            self.use("_go_wrap")
            return P.call("_go_wrap", res, P.const(bits), P.const(signed))
        return res

    # ------------------------------------------------------------ индексы, поля, литералы
    def x_IndexExpr(self, n: G.IndexExpr) -> ast.expr:
        bt = self.T.base(gt(n.base))
        base = self.e(n.base)
        if bt == "string":  # индекс строки в Go - байт
            return P.subscript(P.call(P.attr(base, "encode")), self.e(n.index))
        if self.T.is_map(bt):  # отсутствующий ключ даёт нулевое значение
            kt, vt = split_map(bt)
            return P.call(P.attr(base, "get"), self.val(n.index, kt), self.zero(vt))
        return P.subscript(base, self.e(n.index))

    def x_SliceExpr(self, n: G.SliceExpr) -> ast.expr:
        base = self.e(n.base)
        lo = self.e(n.low) if n.low is not None else None
        hi = self.e(n.high) if n.high is not None else None
        if self.T.base(gt(n.base)) == "string":
            sliced = P.slice_(P.call(P.attr(base, "encode")), lo, hi)
            return P.call(P.attr(sliced, "decode"), P.const("utf-8"), P.const("replace"))
        return P.slice_(base, lo, hi)

    def x_FieldExpr(self, n: G.FieldExpr) -> ast.expr:
        if n.pkg:
            c = PKG_CONSTS.get(f"{n.pkg}.{n.name}")
            if c is None:
                self.warn(f"{n.pkg}.{n.name}: константа не поддержана")
                self.use("_go_unsupported")
                return P.call("_go_unsupported", P.const(f"{n.pkg}.{n.name}"))
            if c[1]:
                self.imp(c[1])
            return P.parse_expr(c[0])
        return P.attr(self.e(n.base), py_ident(n.name))

    def x_CompositeLit(self, n: G.CompositeLit) -> ast.expr:
        t = n.type
        u = self.T.base(t)
        els = n.elements
        if u in self.T.structs:
            fields = self.T.structs[u].fields
            ftypes = {f.name: f.type for f in fields}
            args: List[ast.expr] = []
            kw = []
            for i, el in enumerate(els):
                if isinstance(el, G.KeyedElement):
                    kw.append((py_ident(el.key.name), self.val(el.value, ftypes.get(el.key.name))))
                elif i < len(fields):
                    args.append(self.val(el, fields[i].type))
                else:
                    raise CodeGenError(f"Слишком много значений для структуры {u}")
            return P.call(py_ident(u), *args, kw=kw)
        et = self.T.elem(u)
        if u.startswith("[]"):
            return P.lst([self.val(el, et) for el in els])
        m = ARRAY_RE.match(u)
        if m:
            size = int(m.group(1))
            vals = [self.val(el, et) for el in els]
            if len(vals) > size:
                raise CodeGenError(f"Слишком много значений для массива {t}")
            vals += [self.zero(et) for _ in range(size - len(vals))]
            return P.lst(vals)
        kt, vt = split_map(u)
        return P.dct([self.val(el.key, kt) for el in els], [self.val(el.value, vt) for el in els])

    # ------------------------------------------------------------ вызовы
    def x_FunctionCall(self, n: G.FunctionCall) -> ast.expr:
        if not n.is_var and n.name not in self.sym.funcs:
            r = self.builtin(n.name, n.args)
            if r is not None:
                return r
        info = None if n.is_var else self.sym.funcs.get(n.name)
        ptypes = info[0] if info else []
        args = [self.val(a, ptypes[i] if i < len(ptypes) else None) for i, a in enumerate(n.args)]
        return P.call(n.py_name, *args)

    def x_MethodCall(self, n: G.MethodCall) -> ast.expr:
        if n.pkg:
            return self.pkg_call(n.pkg, n.method, n.args)
        recv = n.receiver
        rt = self.T.base(gt(recv))
        info = self.sym.methods.get(rt or "", {}).get(n.method)
        if info:
            ptypes = info[0]
            args = [self.val(a, ptypes[i] if i < len(ptypes) else None) for i, a in enumerate(n.args)]
            if rt in self.T.structs:
                return P.call(P.attr(self.e(recv), py_ident(n.method)), *args)
            return P.call(f"{py_ident(rt)}_{n.method}", self.e(recv), *args)  # метод не-структуры
        if n.method == "Error" and not n.args and rt in (None, "error"):
            return P.call("str", self.e(recv))
        return P.call(P.attr(self.e(recv), py_ident(n.method)), *[self.e(a) for a in n.args])

    def builtin(self, name: str, args: List[Any]) -> Optional[ast.expr]:
        T = self.T
        if name in ("len", "cap") and len(args) == 1:
            x = self.e(args[0])
            if name == "len" and T.base(gt(args[0])) == "string":  # len строки - байты
                x = P.call(P.attr(x, "encode"))
            return P.call("len", x)
        if name == "append" and args:
            et = T.elem(gt(args[0]))
            return P.lst([ast.Starred(value=self.e(args[0]), ctx=ast.Load())] + [self.val(a, et) for a in args[1:]])
        if name == "delete" and len(args) == 2:
            mt = gt(args[0])
            kt = split_map(T.base(mt))[0] if T.is_map(mt) else None
            return P.call(P.attr(self.e(args[0]), "pop"), self.val(args[1], kt), P.const(None))
        if name == "make" and args:
            a0 = args[0]
            t = a0.type if isinstance(a0, G.TypeExpr) else (a0.name if isinstance(a0, G.Identifier) else None)
            u = T.under(t)
            if u is not None and u.startswith("map["):
                return P.dct([], [])
            if u is not None and u.startswith("[]") and len(args) >= 2:
                z = self.zero_src(u[2:])
                if z in IMMUTABLE_ZEROS:
                    return P.binop(P.lst([P.parse_expr(z)]), "*", self.e(args[1]))
                return P.listcomp(P.parse_expr(z), self.e(args[1]))
            raise CodeGenError(f"make: тип {t} не поддержан")
        if name == "new" and len(args) == 1:
            a0 = args[0]
            t = a0.type if isinstance(a0, G.TypeExpr) else (a0.name if isinstance(a0, G.Identifier) else None)
            return self.zero(t) if t and T.under(t) in T.structs else P.const(None)
        if name == "string" and len(args) == 1:
            t = T.base(gt(args[0]))
            if t in INTS:
                return P.call("chr", self.e(args[0]))
            return self.e(args[0]) if t == "string" else P.call("str", self.e(args[0]))
        if name in ("min", "max") and args:
            return P.call(name, *[self.e(a) for a in args])
        if name in ("print", "println"):
            self.imp("sys")
            kw = [("file", P.parse_expr("sys.stderr"))] + ([("sep", P.const(""))] if name == "print" else [])
            return P.call("print", *[self.plain(a) for a in args], kw=kw)
        if name in CONVERSIONS and len(args) == 1:
            return self.convert(name, args[0])
        if name in COMPLEXES and len(args) == 1:
            return P.call("complex", self.e(args[0]))
        if name in T.aliases and len(args) == 1:
            u = T.under(name)
            r = self.builtin(u, args) if u is not None and u != name else None
            return r if r is not None else self.e(args[0])
        return None

    def convert(self, name: str, arg: Any) -> ast.expr:
        s = self.e(arg)
        if CONVERSIONS[name] != "int":
            return P.call(CONVERSIONS[name], s)
        if isinstance(arg, (G.NumberLiteral, G.RuneLiteral)) and self.T.under(gt(arg)) in INTS:
            return s
        r = P.call("int", s)
        if name in SIZED:
            bits, signed = SIZED[name]
            self.use("_go_wrap")
            return P.call("_go_wrap", r, P.const(bits), P.const(signed))
        return r

    # ------------------------------------------------------------ стандартная библиотека
    def plain(self, n: Any) -> ast.expr:
        """Аргумент print: строки и целые как есть, остальное как %v."""
        t = self.T.base(gt(n))
        if t == "string" or t in INTS:
            return self.e(n)
        self.use("_go_str")
        return P.call("_go_str", self.e(n))

    def strexpr(self, n: Any) -> ast.expr:
        t = self.T.base(gt(n))
        if t == "string":
            return self.e(n)
        if t in INTS:
            return P.call("str", self.e(n))
        self.use("_go_str")
        return P.call("_go_str", self.e(n))

    def pkg_call(self, pkg: str, method: str, args: List[Any]) -> ast.expr:
        if pkg == "fmt":
            r = self.fmt_call(method, args)
            if r is not None:
                return r
        else:
            entry = STDLIB.get(f"{pkg}.{method}")
            if entry is not None:
                if P.template_arity(entry.template) > len(args):
                    raise CodeGenError(f"{pkg}.{method}: не хватает аргументов")
                if entry.module:
                    self.imp(entry.module)
                if entry.helper:
                    self.use(entry.helper)
                return P.fill(entry.template, [self.e(a) for a in args])
        self.warn(f"{pkg}.{method}: нет соответствия в Python (вызов заменён на ошибку времени выполнения)")
        self.use("_go_unsupported")
        return P.call("_go_unsupported", P.const(f"{pkg}.{method}"))

    def fmt_call(self, method: str, args: List[Any]) -> Optional[ast.expr]:
        if method == "Println":
            return P.call("print", *[self.plain(a) for a in args])
        if method == "Sprintln":
            joined = P.call(P.attr(P.const(" "), "join"), P.lst([self.strexpr(a) for a in args]))
            return P.binop(joined, "+", P.const("\n"))
        if method in ("Print", "Sprint"):
            parts: List[ast.expr] = []
            for i, a in enumerate(args):
                # Go ставит пробел между операндами, если ни один из них не строка
                if i and self.T.base(gt(args[i - 1])) != "string" and self.T.base(gt(a)) != "string":
                    parts.append(P.const(" "))
                parts.append(self.strexpr(a))
            joined = parts[0] if parts else P.const("")
            for p in parts[1:]:
                joined = P.binop(joined, "+", p)
            return P.call("print", joined, kw=[("end", P.const(""))]) if method == "Print" else joined
        if method in ("Printf", "Sprintf", "Errorf"):
            if not args:
                raise CodeGenError(f"fmt.{method} требует строку формата")
            self.use("_go_printf")
            call = P.call("_go_printf", *[self.e(a) for a in args])
            if method == "Printf":
                return P.call("print", call, kw=[("end", P.const(""))])
            return P.call("RuntimeError", call) if method == "Errorf" else call
        return None
