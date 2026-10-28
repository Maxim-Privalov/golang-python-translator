"""Этап 1: анализ. Расставляет на узлах Go AST типы и имена Python.

Аннотации (атрибуты узлов):
  go_type       - тип выражения в Go (или None, если не удалось вывести)
  py_name       - имя переменной/функции в Python (после переименования при затенении)
  is_var        - идентификатор/вызов относится к локальной или глобальной переменной
  pkg           - имя пакета для pkg.Func(...) и pkg.Const
  iota_value    - значение iota внутри const-группы
  py_params, py_recv, assigned_globals - у FunctionDecl
У составных литералов с опущенным типом заполняется поле `type`.
"""
from typing import Any, Callable, Dict, List, Optional, Tuple

from golang_python_translator import parser as G
from golang_python_translator.constructions.gotypes import (
    ARRAY_RE, BOOL_OPS, COMPLEXES, CONVERSIONS, FLOATS, INTS, CodeGenError,
    py_ident, split_map, split_tuple, strip_ptr,
)
from golang_python_translator.constructions.stdlib import PKG_CONSTS, pkg_call_type
from golang_python_translator.constructions.symbols import Symbols, param_types


class Analyzer:
    def __init__(self, sym: Symbols, warn: Callable[[str], None]) -> None:
        self.sym = sym
        self.T = sym.types
        self.warn = warn
        # имя Go -> (тип Go, имя Python); scopes[0] - глобальные
        self.scopes: List[Dict[str, Tuple[Optional[str], str]]] = [{}]
        self.cur_ret: Optional[str] = None
        self.cur_iota: Optional[int] = None
        self.assigned: set = set()
        self._shadow = 0

    def run(self, program: G.Program) -> None:
        # глобальные var/const первыми: функциям нужны их типы
        for d in program.declarations:
            if isinstance(d, (G.VarDecl, G.ConstDecl, G.DeclGroup)):
                self.stmt(d)
        for fn in program.functions:
            self.function(fn)

    # ------------------------------------------------------------ области видимости
    def push(self) -> None:
        self.scopes.append({})

    def pop(self) -> None:
        self.scopes.pop()

    def declare(self, name: Optional[str], t: Optional[str], force_py: Optional[str] = None) -> str:
        if not name or name == "_":
            return "_"
        cur = self.scopes[-1]
        if name in cur and force_py is None:
            py = cur[name][1]  # повторное объявление в той же области (a, err := ...)
        elif force_py is not None:
            py = force_py
        else:
            py = py_ident(name)
            if any(name in s for s in self.scopes[:-1]):
                self._shadow += 1  # затенение: во внутренней области - новая переменная
                py = f"{py}_{self._shadow}"
        cur[name] = (t, py)
        return py

    def lookup(self, name: Optional[str]) -> Optional[Tuple[int, Optional[str], str]]:
        for i in range(len(self.scopes) - 1, -1, -1):
            if name in self.scopes[i]:
                t, py = self.scopes[i][name]
                return i, t, py
        return None

    def ref(self, n: G.Identifier, store: bool = False) -> Optional[str]:
        r = self.lookup(n.name)
        if r:
            idx, t, py = r
            n.py_name, n.is_var, n.go_type = py, True, t
            if store and idx == 0 and len(self.scopes) > 1:
                self.assigned.add(py)
        else:
            n.py_name, n.is_var = py_ident(n.name), False
            n.go_type = "bool" if n.name in ("true", "false") else None
            if n.name == "iota" and self.cur_iota is not None:
                n.iota_value = self.cur_iota
        return n.go_type

    def target(self, n: Any) -> Optional[str]:
        """Цель присваивания."""
        if isinstance(n, G.Identifier):
            return self.ref(n, store=True)
        return self.expr(n)

    # ------------------------------------------------------------ функции
    def function(self, n: G.FunctionDecl) -> None:
        recv = n.receiver
        self.push()
        saved, self.assigned = self.assigned, set()
        n.py_recv = None
        if recv is not None:
            as_method = strip_ptr(recv.type) in self.T.structs
            n.py_recv = self.declare(recv.name, recv.type, force_py="self" if as_method else None)
        n.py_params = []
        for i, (p, t) in enumerate(zip(n.params, param_types(n.params))):
            n.py_params.append(self.declare(p.name, t) if p.name != "_" else f"_p{i}")
        self.cur_ret = n.return_type
        self.stmts(n.body.statements)
        n.assigned_globals = sorted(self.assigned)
        self.assigned, self.cur_ret = saved, None
        self.pop()

    # ------------------------------------------------------------ инструкции
    def stmts(self, items: List[Any]) -> None:
        for s in items:
            self.stmt(s)

    def scoped(self, block: Any) -> None:
        self.push()
        self.stmts(getattr(block, "statements", block) or [])
        self.pop()

    def stmt(self, n: Any) -> None:
        h = getattr(self, "s_" + type(n).__name__, None)
        if h is None:
            raise CodeGenError(f"Неизвестный узел-инструкция: {type(n).__name__}")
        h(n)

    def s_Block(self, n: G.Block) -> None:
        self.scoped(n)

    def s_DeclGroup(self, n: G.DeclGroup) -> None:
        self.stmts(n.decls)

    def s_StructDecl(self, n: Any) -> None:
        pass

    s_InterfaceDecl = s_TypeDecl = s_BreakStatement = s_ContinueStatement = s_StructDecl

    def s_ExpressionStatement(self, n: G.ExpressionStatement) -> None:
        self.expr(n.expression)

    def s_VarDecl(self, n: G.VarDecl) -> None:
        vt = n.type
        if n.value is not None:
            t = self.expr(n.value, n.type)
            vt = n.type or t
        n.py_name = self.declare(n.name, vt)

    def s_ConstDecl(self, n: G.ConstDecl) -> None:
        self.cur_iota = n.iota
        vt = n.type
        if n.value is not None:
            t = self.expr(n.value, n.type)
            vt = n.type or t
        self.cur_iota = None
        n.py_name = self.declare(n.name, vt)

    def s_Assignment(self, n: G.Assignment) -> None:
        if n.op not in ("assign", "declare"):
            self.target(n.target)
            self.expr(n.value, n.target.go_type)
            return
        declare = n.op == "declare"
        targets = list(n.target.items) if isinstance(n.target, G.TupleExpr) else [n.target]
        values = list(n.value.items) if isinstance(n.value, G.TupleExpr) else [n.value]
        ttypes: List[Optional[str]] = [None] * len(targets) if declare else [self.target(t) for t in targets]

        if len(values) == len(targets):
            vtypes = [self.expr(v, tt) for v, tt in zip(values, ttypes)]
            types = [tt or vt for tt, vt in zip(ttypes, vtypes)]
        elif len(values) == 1:
            v = values[0]
            vt = self.expr(v)
            guess: List[Optional[str]] = [None] * len(targets)
            if len(targets) == 2 and isinstance(v, G.IndexExpr) and self.T.is_map(v.base.go_type):
                guess = [split_map(self.T.base(v.base.go_type))[1], "bool"]
            else:
                parts = split_tuple(vt)
                if parts and len(parts) == len(targets):
                    guess = list(parts)
            types = [tt or g for tt, g in zip(ttypes, guess)]
        else:
            raise CodeGenError("Число переменных и значений не совпадает")

        if declare:
            for t, ty in zip(targets, types):
                if not isinstance(t, G.Identifier):
                    raise CodeGenError("В := слева допустимы только идентификаторы")
                t.py_name, t.go_type, t.is_var = self.declare(t.name, ty), ty, True

    def s_IfStatement(self, n: G.IfStatement) -> None:
        self.push()
        if n.init is not None:
            self.stmt(n.init)
        self.expr(n.condition)
        self.scoped(n.body)
        if isinstance(n.orelse, G.IfStatement):
            self.stmt(n.orelse)
        elif n.orelse is not None:
            self.scoped(n.orelse)
        self.pop()

    def s_ForStatement(self, n: G.ForStatement) -> None:
        self.push()
        if n.init is not None:
            self.stmt(n.init)
        if n.condition is not None:
            self.expr(n.condition)
        if n.post is not None:
            self.stmt(n.post)
        self.scoped(n.body)
        self.pop()

    def s_RangeStatement(self, n: G.RangeStatement) -> None:
        t = self.T.base(self.expr(n.iterable))
        if t == "string":
            kt, vt = "int", "rune"
        elif t in INTS:
            kt, vt = n.iterable.go_type, None
        elif t is not None and (t.startswith("[]") or ARRAY_RE.match(t)):
            kt, vt = "int", self.T.elem(t)
        elif t is not None and t.startswith("map["):
            kt, vt = split_map(t)
        else:
            kt = vt = None
            self.warn("range по значению неизвестного типа: используется _go_range")
        self.push()
        for node, ty in ((n.key, kt), (n.value, vt)):
            if node is None:
                continue
            if n.define:
                if not isinstance(node, G.Identifier):
                    raise CodeGenError("В range с := допустимы только идентификаторы")
                node.py_name, node.go_type, node.is_var = self.declare(node.name, ty), ty, True
            else:
                self.target(node)
        self.scoped(n.body)
        self.pop()

    def s_SwitchStatement(self, n: G.SwitchStatement) -> None:
        self.push()
        if n.init is not None:
            self.stmt(n.init)
        st = self.expr(n.expression) if n.expression is not None else None
        for c in n.cases:
            for v in c.values:
                self.expr(v, st)
            self.scoped(c.body)
        self.pop()

    def s_ReturnStatement(self, n: G.ReturnStatement) -> None:
        if n.value is None:
            return
        rets = split_tuple(self.cur_ret)
        if isinstance(n.value, G.TupleExpr):
            for i, v in enumerate(n.value.items):
                self.expr(v, rets[i] if rets and i < len(rets) else None)
        else:
            self.expr(n.value, None if rets else self.cur_ret)

    # ------------------------------------------------------------ выражения
    def expr(self, n: Any, expected: Optional[str] = None) -> Optional[str]:
        h = getattr(self, "e_" + type(n).__name__, None)
        if h is None:
            raise CodeGenError(f"Неизвестный узел-выражение: {type(n).__name__}")
        n.go_type = h(n, expected)
        return n.go_type

    def e_NumberLiteral(self, n: G.NumberLiteral, exp: Optional[str]) -> str:
        if isinstance(n.value, int):
            return "int"
        return "float64" if isinstance(n.value, float) else "complex128"

    def e_RuneLiteral(self, n: Any, exp: Optional[str]) -> str:
        return "rune"

    def e_StringLiteral(self, n: Any, exp: Optional[str]) -> str:
        return "string"

    def e_TypeExpr(self, n: Any, exp: Optional[str]) -> None:
        return None

    def e_Identifier(self, n: G.Identifier, exp: Optional[str]) -> Optional[str]:
        return self.ref(n)

    def e_TupleExpr(self, n: G.TupleExpr, exp: Optional[str]) -> None:
        for i in n.items:
            self.expr(i)
        return None

    def e_UnaryOp(self, n: G.UnaryOp, exp: Optional[str]) -> Optional[str]:
        t = self.expr(n.operand)
        if n.op == "not":
            return "bool"
        if n.op == "bitand":
            return "*" + t if t else None
        if n.op == "times":
            return t[1:] if t and t.startswith("*") else None
        return t

    def e_BinaryOp(self, n: G.BinaryOp, exp: Optional[str]) -> Optional[str]:
        lt, rt = self.expr(n.left), self.expr(n.right)
        if n.op in BOOL_OPS:
            return "bool"
        if n.op in ("shl", "shr"):
            return lt
        if self.T.under(lt) in FLOATS:
            return lt
        if self.T.under(rt) in FLOATS:
            return rt
        if lt == rt:
            return lt
        if lt is None or rt is None:
            return None
        if isinstance(n.left, (G.NumberLiteral, G.RuneLiteral)):
            return rt
        return lt

    def e_FunctionCall(self, n: G.FunctionCall, exp: Optional[str]) -> Optional[str]:
        nm = n.name
        r = self.lookup(nm)
        n.is_var = r is not None
        n.py_name = r[2] if r else py_ident(nm)
        info = None if n.is_var else self.sym.funcs.get(nm)
        ptypes = info[0] if info else []
        ats = [self.expr(a, ptypes[i] if i < len(ptypes) else None) for i, a in enumerate(n.args)]
        if n.is_var:
            return None
        if info:
            return info[1]
        if nm in CONVERSIONS or nm in COMPLEXES or nm == "string" or nm in self.T.aliases:
            return nm
        if nm in ("len", "cap"):
            return "int"
        if nm in ("append", "min", "max"):
            return ats[0] if ats else None
        if nm in ("make", "new") and n.args:
            a0 = n.args[0]
            t = a0.type if isinstance(a0, G.TypeExpr) else (a0.name if isinstance(a0, G.Identifier) else None)
            return ("*" + t if t else None) if nm == "new" else t
        return None

    def e_MethodCall(self, n: G.MethodCall, exp: Optional[str]) -> Optional[str]:
        r = n.receiver
        if isinstance(r, G.Identifier) and r.name in self.sym.packages and not self.lookup(r.name):
            n.pkg = r.name
            for a in n.args:
                self.expr(a)
            return pkg_call_type(r.name, n.method)
        n.pkg = None
        base = self.T.base(self.expr(r))
        info = self.sym.methods.get(base or "", {}).get(n.method)
        ptypes = info[0] if info else []
        for i, a in enumerate(n.args):
            self.expr(a, ptypes[i] if i < len(ptypes) else None)
        if info:
            return info[1]
        if n.method == "Error" and base in (None, "error"):
            return "string"
        return None

    def e_FieldExpr(self, n: G.FieldExpr, exp: Optional[str]) -> Optional[str]:
        b = n.base
        if isinstance(b, G.Identifier) and b.name in self.sym.packages and not self.lookup(b.name):
            n.pkg = b.name
            c = PKG_CONSTS.get(f"{b.name}.{n.name}")
            return c[2] if c else None
        n.pkg = None
        st = self.T.structs.get(self.T.base(self.expr(b)) or "")
        if st:
            for f in st.fields:
                if f.name == n.name:
                    return f.type
        return None

    def e_IndexExpr(self, n: G.IndexExpr, exp: Optional[str]) -> Optional[str]:
        bt = self.expr(n.base)
        self.expr(n.index, split_map(self.T.base(bt))[0] if self.T.is_map(bt) else None)
        return self.T.elem(bt)

    def e_SliceExpr(self, n: G.SliceExpr, exp: Optional[str]) -> Optional[str]:
        for part in (n.low, n.high):
            if part is not None:
                self.expr(part)
        return self.expr(n.base)

    def e_CompositeLit(self, n: G.CompositeLit, exp: Optional[str]) -> str:
        if n.type is None:
            n.type = exp  # тип вложенного литерала берётся из контекста
        if n.type is None:
            raise CodeGenError("Не удалось определить тип составного литерала")
        u = self.T.base(n.type)
        if u in self.T.structs:
            fields = self.T.structs[u].fields
            ftypes = {f.name: f.type for f in fields}
            for i, el in enumerate(n.elements):
                if isinstance(el, G.KeyedElement):
                    if not isinstance(el.key, G.Identifier):
                        raise CodeGenError("Ключ поля структуры должен быть именем")
                    self.expr(el.value, ftypes.get(el.key.name))
                else:
                    self.expr(el, fields[i].type if i < len(fields) else None)
        elif u is not None and (u.startswith("[]") or ARRAY_RE.match(u)):
            et = self.T.elem(u)
            for el in n.elements:
                if isinstance(el, G.KeyedElement):
                    raise CodeGenError("Элементы слайса с индексом-ключом не поддерживаются")
                self.expr(el, et)
        elif u is not None and u.startswith("map["):
            kt, vt = split_map(u)
            for el in n.elements:
                if not isinstance(el, G.KeyedElement):
                    raise CodeGenError("В литерале map нужны пары ключ: значение")
                self.expr(el.key, kt)
                self.expr(el.value, vt)
        else:
            raise CodeGenError(f"Составной литерал для типа {n.type} не поддержан")
        return n.type
