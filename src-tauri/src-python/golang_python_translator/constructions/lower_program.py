"""Этап 2: сборка модуля Python (типы, функции, глобальные, заголовок, рантайм)."""
import ast
from typing import List

from golang_python_translator import parser as G
from golang_python_translator.constructions import pyast as P
from golang_python_translator.constructions import runtime
from golang_python_translator.constructions.gotypes import py_ident, strip_ptr
from golang_python_translator.constructions.lower_stmt import StmtLowerer
from golang_python_translator.constructions.symbols import param_types


class ProgramLowerer(StmtLowerer):
    def lower(self, program: G.Program) -> ast.Module:
        globals_: List[ast.stmt] = []
        for d in program.declarations:
            if isinstance(d, (G.VarDecl, G.ConstDecl, G.DeclGroup)):
                globals_ += self.s(d)

        types: List[ast.stmt] = []
        types += [self.struct_class(n) for n in self.sym.struct_nodes]
        types += [self.interface_class(n) for n in self.sym.iface_nodes]
        types += [P.assign(P.name(py_ident(n.name), True), self.ann(n.base)) for n in self.sym.alias_nodes]

        funcs: List[ast.stmt] = []
        for fn in program.functions:
            if fn.receiver is not None and strip_ptr(fn.receiver.type) in self.T.structs:
                continue  # метод генерируется внутри класса
            funcs.append(self.lower_function(fn))

        tail: List[ast.stmt] = []
        if "main" in self.sym.funcs:
            tail = ast.parse('if __name__ == "__main__":\n    main()').body

        helpers = runtime.closure(self.helpers)
        self.imports |= runtime.imports_for(helpers)
        body = self.header() + runtime.runtime_ast(helpers) + types + funcs + globals_ + tail
        return ast.Module(body=body, type_ignores=[])

    def header(self) -> List[ast.stmt]:
        out: List[ast.stmt] = [P.import_from("__future__", ["annotations"])]
        out += [P.import_(m) for m in sorted(self.imports)]
        if self.typing:
            out.append(P.import_from("typing", sorted(self.typing)))
        if self.need_dataclass:
            out.append(P.import_from("dataclasses", ["dataclass", "field"]))
        return out

    def struct_class(self, n: G.StructDecl) -> ast.ClassDef:
        self.need_dataclass = True
        body: List[ast.stmt] = [
            P.ann_assign(P.name(py_ident(f.name), True), self.ann(f.type), self.field_default(f.type))
            for f in n.fields
        ]
        body += [self.lower_function(m) for m in self.sym.method_nodes.get(n.name or "", [])]
        return P.classdef(py_ident(n.name), body or [ast.Pass()], decorators=[P.name("dataclass")])

    def interface_class(self, n: G.InterfaceDecl) -> ast.ClassDef:
        self.typing.update({"Protocol", "Any"})
        body: List[ast.stmt] = []
        for m in n.methods:
            args = [("self", None)]
            for i, (p, t) in enumerate(zip(m.params, param_types(m.params))):
                args.append((py_ident(p.name) if p.name != "_" else f"_p{i}", self.ann(t)))
            ret = self.ann(m.return_type) if m.return_type else P.const(None)
            body.append(P.funcdef(py_ident(m.name), args, [P.expr_stmt(P.const(Ellipsis))], ret))
        return P.classdef(py_ident(n.name), body or [ast.Pass()], bases=[P.name("Protocol")])
