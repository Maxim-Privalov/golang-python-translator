"""Этап 0: таблица символов программы (функции, методы, типы, импорты)."""
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

from golang_python_translator import parser as G
from golang_python_translator.constructions.gotypes import TypeEnv, strip_ptr

FuncInfo = Tuple[List[Optional[str]], Optional[str]]  # (типы параметров, тип результата)


def walk(n: Any, seen: Optional[Set[int]] = None) -> Iterator[G.Node]:
    """Обход всех узлов поддерева."""
    seen = set() if seen is None else seen
    if isinstance(n, G.Node):
        if id(n) in seen:
            return
        seen.add(id(n))
        yield n
        for v in vars(n).values():
            yield from walk(v, seen)
    elif isinstance(n, (list, tuple)):
        for x in n:
            yield from walk(x, seen)


def param_types(params: List[G.Param]) -> List[Optional[str]]:
    """Go: func f(a, b int) - тип b относится и к a."""
    types: List[Optional[str]] = []
    next_t: Optional[str] = None
    for p in reversed(params):
        if p.type is not None:
            next_t = p.type
        types.append(p.type if p.type is not None else next_t)
    types.reverse()
    return types


class Symbols:
    def __init__(self, program: G.Program, warn: Callable[[str], None]) -> None:
        self.types = TypeEnv()
        self.packages: Set[str] = set()
        self.funcs: Dict[str, FuncInfo] = {}
        self.methods: Dict[str, Dict[str, FuncInfo]] = {}
        self.method_nodes: Dict[str, List[G.FunctionDecl]] = {}
        self.struct_nodes: List[G.StructDecl] = []
        self.iface_nodes: List[G.InterfaceDecl] = []
        self.alias_nodes: List[G.TypeDecl] = []

        for imp in program.imports:
            self.packages.add(str(imp).split("/")[-1])
        for n in walk(program):
            if isinstance(n, G.FunctionDecl) and n.name:
                info: FuncInfo = (param_types(n.params), n.return_type)
                if n.receiver is not None:
                    rt = strip_ptr(n.receiver.type) or "?"
                    self.methods.setdefault(rt, {})[n.name] = info
                    self.method_nodes.setdefault(rt, []).append(n)
                else:
                    self.funcs[n.name] = info
            elif isinstance(n, G.StructDecl) and n.name:
                if n.name in self.types.structs:
                    warn(f"структура {n.name} объявлена дважды - использовано первое объявление")
                    continue
                self.types.structs[n.name] = n
                self.struct_nodes.append(n)
            elif isinstance(n, G.InterfaceDecl):
                self.iface_nodes.append(n)
            elif isinstance(n, G.TypeDecl) and n.name:
                self.alias_nodes.append(n)
                self.types.aliases[n.name] = n.base or "int"
