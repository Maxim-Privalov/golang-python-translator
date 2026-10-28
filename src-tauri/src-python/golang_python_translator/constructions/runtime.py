"""Рантайм-хелперы: подключаются к результату только когда реально нужны."""
import ast
from typing import Dict, List, Set, Tuple

# имя -> (исходник, зависимости, импорты)
HELPERS: Dict[str, Tuple[str, Tuple[str, ...], Tuple[str, ...]]] = {
    '_go_div': (r'''
def _go_div(a, b):
    """Целочисленное деление Go (округление к нулю)."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q
''', (), ()),
    '_go_mod': (r'''
def _go_mod(a, b):
    """Остаток Go (знак как у делимого)."""
    return a - _go_div(a, b) * b
''', ('_go_div',), ()),
    '_go_wrap': (r'''
def _go_wrap(v, bits, signed):
    """Переполнение целых фиксированной ширины."""
    v &= (1 << bits) - 1
    if signed and v >> (bits - 1):
        v -= 1 << bits
    return v
''', (), ()),
    '_go_float': (r'''
def _go_float(v):
    """Печать float как %v в Go (формат %g, кратчайшее представление)."""
    if v != v:
        return "NaN"
    if v in (math.inf, -math.inf):
        return "+Inf" if v > 0 else "-Inf"
    if v == 0:
        return "-0" if math.copysign(1.0, v) < 0 else "0"
    sign = "-" if v < 0 else ""
    d = decimal.Decimal(repr(abs(v))).as_tuple()
    exp = len(d.digits) + d.exponent - 1
    digits = "".join(map(str, d.digits)).rstrip("0") or "0"
    if exp < -4 or exp >= 6:
        mant = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
        return "%s%se%s%02d" % (sign, mant, "-" if exp < 0 else "+", abs(exp))
    if exp < 0:
        return sign + "0." + "0" * (-exp - 1) + digits
    if len(digits) <= exp + 1:
        return sign + digits.ljust(exp + 1, "0")
    return sign + digits[:exp + 1] + "." + digits[exp + 1:]
''', (), ('math', 'decimal')),
    '_go_str': (r'''
def _go_str(v):
    """Представление значения как при %v / Println в Go."""
    if v is None:
        return "<nil>"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, float):
        return _go_float(v)
    if isinstance(v, complex):
        return "(" + _go_float(v.real) + ("+" if v.imag >= 0 else "") + _go_float(v.imag) + "i)"
    if isinstance(v, (list, tuple)):
        return "[" + " ".join(_go_str(x) for x in v) + "]"
    if isinstance(v, dict):
        try:
            keys = sorted(v)
        except TypeError:
            keys = list(v)
        return "map[" + " ".join(_go_str(k) + ":" + _go_str(v[k]) for k in keys) + "]"
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return "{" + " ".join(_go_str(getattr(v, f.name)) for f in dataclasses.fields(v)) + "}"
    return str(v)
''', ('_go_float',), ('dataclasses',)),
    '_go_printf': (r'''
_GO_VERB = re.compile(r"%([-+# 0]*)(\d*)(?:\.(\d+))?([a-zA-Z%])")


def _go_printf(fmt, *args):
    """fmt.Sprintf из Go."""
    it = iter(args)

    def pad(s, flags, width):
        if not width:
            return s
        return s.ljust(int(width)) if "-" in flags else s.rjust(int(width))

    def repl(m):
        flags, width, prec, verb = m.groups()
        if verb == "%":
            return "%"
        try:
            a = next(it)
        except StopIteration:
            return "%!" + verb + "(MISSING)"
        if verb in "vsw":
            s = _go_str(a)
            if prec is not None and verb == "s":
                s = s[:int(prec)]
            return pad(s, flags, width)
        if verb == "t":
            return pad(_go_str(bool(a)), flags, width)
        if verb == "q":
            if isinstance(a, int):
                return pad("'" + chr(a) + "'", flags, width)
            s = str(a).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
            return pad('"' + s + '"', flags, width)
        if verb == "c":
            return pad(chr(a), flags, width)
        if verb == "T":
            names = {int: "int", float: "float64", str: "string", bool: "bool",
                     list: "[]interface {}", dict: "map", type(None): "<nil>"}
            return pad(names.get(type(a), type(a).__name__), flags, width)
        if verb == "b":
            s = format(a, "b")
            return s.rjust(int(width), "0" if "0" in flags else " ") if width else s
        if verb in "dxXoeEfFgG":
            if verb == "d":
                a = int(a)
            spec = "%" + flags + width + ("." + prec if prec is not None else "") + verb
            return spec % a
        return "%!" + verb + "(" + _go_str(a) + ")"

    return _GO_VERB.sub(repl, fmt)
''', ('_go_str',), ('re',)),
    '_go_range_str': (r'''
def _go_range_str(s):
    """range по строке: (байтовое смещение, руна)."""
    i = 0
    for ch in s:
        yield i, ord(ch)
        i += len(ch.encode("utf-8"))
''', (), ()),
    '_go_range': (r'''
def _go_range(x):
    """range по значению неизвестного типа."""
    if isinstance(x, dict):
        return list(x.items())
    if isinstance(x, str):
        return list(_go_range_str(x))
    if isinstance(x, int):
        return [(i, None) for i in range(x)]
    return list(enumerate(x))
''', ('_go_range_str',), ()),
    '_go_index': (r'''
def _go_index(s, sub):
    """strings.Index: байтовый индекс."""
    i = s.find(sub)
    return -1 if i < 0 else len(s[:i].encode("utf-8"))
''', (), ()),
    '_go_split': (r'''
def _go_split(s, sep):
    return s.split(sep) if sep else list(s)
''', (), ()),
    '_go_atoi': (r'''
def _go_atoi(s):
    """strconv.Atoi -> (число, ошибка)."""
    if s.strip() != s or "_" in s:
        return 0, ValueError('strconv.Atoi: parsing "%s": invalid syntax' % s)
    try:
        return int(s), None
    except ValueError:
        return 0, ValueError('strconv.Atoi: parsing "%s": invalid syntax' % s)
''', (), ()),
    '_go_parse_float': (r'''
def _go_parse_float(s):
    """strconv.ParseFloat -> (число, ошибка)."""
    if s.strip() != s or "_" in s:
        return 0.0, ValueError('strconv.ParseFloat: parsing "%s": invalid syntax' % s)
    try:
        return float(s), None
    except ValueError:
        return 0.0, ValueError('strconv.ParseFloat: parsing "%s": invalid syntax' % s)
''', (), ()),
    '_go_round': (r'''
def _go_round(x):
    """math.Round: half away from zero."""
    return math.copysign(math.floor(abs(x) + 0.5), x)
''', (), ('math',)),
    '_go_unsupported': (r'''
def _go_unsupported(name):
    raise NotImplementedError("нет соответствия в Python: " + name)
''', (), ()),
}
ORDER: List[str] = list(HELPERS)


def closure(used: Set[str]) -> List[str]:
    """Нужные хелперы вместе с зависимостями, в порядке определения."""
    need: Set[str] = set()

    def add(n: str) -> None:
        if n not in need:
            need.add(n)
            for d in HELPERS[n][1]:
                add(d)

    for u in used:
        add(u)
    return [h for h in ORDER if h in need]


def imports_for(names: List[str]) -> Set[str]:
    return {i for n in names for i in HELPERS[n][2]}


def runtime_ast(names: List[str]) -> List[ast.stmt]:
    body: List[ast.stmt] = []
    for n in names:
        body.extend(ast.parse(HELPERS[n][0]).body)
    return body
