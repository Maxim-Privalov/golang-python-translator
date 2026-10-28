"""Таблицы соответствия стандартной библиотеки Go -> Python."""
from typing import Dict, NamedTuple, Optional, Tuple


class Std(NamedTuple):
    template: str            # python-выражение, {0} {1} ... - аргументы
    module: Optional[str]    # какой модуль импортировать
    ret: Optional[str]       # тип результата в Go
    helper: Optional[str] = None  # какой рантайм-хелпер нужен


STDLIB: Dict[str, Std] = {
    "strings.ToUpper": Std("{0}.upper()", None, "string"),
    "strings.ToLower": Std("{0}.lower()", None, "string"),
    "strings.TrimSpace": Std("{0}.strip()", None, "string"),
    "strings.Trim": Std("{0}.strip({1})", None, "string"),
    "strings.TrimLeft": Std("{0}.lstrip({1})", None, "string"),
    "strings.TrimRight": Std("{0}.rstrip({1})", None, "string"),
    "strings.TrimPrefix": Std("{0}.removeprefix({1})", None, "string"),
    "strings.TrimSuffix": Std("{0}.removesuffix({1})", None, "string"),
    "strings.Contains": Std("({1} in {0})", None, "bool"),
    "strings.ContainsRune": Std("(chr({1}) in {0})", None, "bool"),
    "strings.HasPrefix": Std("{0}.startswith({1})", None, "bool"),
    "strings.HasSuffix": Std("{0}.endswith({1})", None, "bool"),
    "strings.EqualFold": Std("({0}.casefold() == {1}.casefold())", None, "bool"),
    "strings.Repeat": Std("({0} * {1})", None, "string"),
    "strings.Count": Std("{0}.count({1})", None, "int"),
    "strings.Split": Std("_go_split({0}, {1})", None, "[]string", "_go_split"),
    "strings.Fields": Std("{0}.split()", None, "[]string"),
    "strings.Join": Std("{1}.join({0})", None, "string"),
    "strings.Replace": Std("{0}.replace({1}, {2}, {3})", None, "string"),
    "strings.ReplaceAll": Std("{0}.replace({1}, {2})", None, "string"),
    "strings.Index": Std("_go_index({0}, {1})", None, "int", "_go_index"),
    "strings.LastIndex": Std("{0}.rfind({1})", None, "int"),
    "math.Sqrt": Std("math.sqrt({0})", "math", "float64"),
    "math.Pow": Std("math.pow({0}, {1})", "math", "float64"),
    "math.Abs": Std("abs({0})", None, "float64"),
    "math.Floor": Std("float(math.floor({0}))", "math", "float64"),
    "math.Ceil": Std("float(math.ceil({0}))", "math", "float64"),
    "math.Trunc": Std("float(math.trunc({0}))", "math", "float64"),
    "math.Round": Std("_go_round({0})", None, "float64", "_go_round"),
    "math.Max": Std("max({0}, {1})", None, "float64"),
    "math.Min": Std("min({0}, {1})", None, "float64"),
    "math.Sin": Std("math.sin({0})", "math", "float64"),
    "math.Cos": Std("math.cos({0})", "math", "float64"),
    "math.Tan": Std("math.tan({0})", "math", "float64"),
    "math.Atan": Std("math.atan({0})", "math", "float64"),
    "math.Atan2": Std("math.atan2({0}, {1})", "math", "float64"),
    "math.Log": Std("math.log({0})", "math", "float64"),
    "math.Log2": Std("math.log2({0})", "math", "float64"),
    "math.Log10": Std("math.log10({0})", "math", "float64"),
    "math.Exp": Std("math.exp({0})", "math", "float64"),
    "math.Hypot": Std("math.hypot({0}, {1})", "math", "float64"),
    "math.Mod": Std("math.fmod({0}, {1})", "math", "float64"),
    "math.IsNaN": Std("math.isnan({0})", "math", "bool"),
    "math.Inf": Std("(math.inf if {0} >= 0 else -math.inf)", "math", "float64"),
    "strconv.Itoa": Std("str({0})", None, "string"),
    "strconv.Atoi": Std("_go_atoi({0})", None, "(int,error)", "_go_atoi"),
    "strconv.ParseFloat": Std("_go_parse_float({0})", None, "(float64,error)", "_go_parse_float"),
    "errors.New": Std("RuntimeError({0})", None, "error"),
    "os.Exit": Std("sys.exit({0})", "sys", None),
    "sort.Ints": Std("{0}.sort()", None, None),
    "sort.Strings": Std("{0}.sort()", None, None),
    "sort.Float64s": Std("{0}.sort()", None, None),
    "rand.Intn": Std("random.randrange({0})", "random", "int"),
    "rand.Int": Std("random.getrandbits(63)", "random", "int"),
    "rand.Float64": Std("random.random()", "random", "float64"),
    "rand.Seed": Std("random.seed({0})", "random", None),
    "unicode.IsUpper": Std("chr({0}).isupper()", None, "bool"),
    "unicode.IsLower": Std("chr({0}).islower()", None, "bool"),
    "unicode.IsDigit": Std("chr({0}).isdigit()", None, "bool"),
    "unicode.IsLetter": Std("chr({0}).isalpha()", None, "bool"),
    "unicode.IsSpace": Std("chr({0}).isspace()", None, "bool"),
    "unicode.ToUpper": Std("ord(chr({0}).upper())", None, "rune"),
    "unicode.ToLower": Std("ord(chr({0}).lower())", None, "rune"),
    "utf8.RuneCountInString": Std("len({0})", None, "int"),
}

# pkg.Const -> (python-выражение, модуль, тип)
PKG_CONSTS: Dict[str, Tuple[str, Optional[str], str]] = {
    "math.Pi": ("math.pi", "math", "float64"),
    "math.E": ("math.e", "math", "float64"),
    "math.MaxInt": ("9223372036854775807", None, "int"),
    "math.MinInt": ("(-9223372036854775808)", None, "int"),
    "math.MaxInt64": ("9223372036854775807", None, "int"),
    "math.MinInt64": ("(-9223372036854775808)", None, "int"),
    "math.MaxInt32": ("2147483647", None, "int"),
    "math.MinInt32": ("(-2147483648)", None, "int"),
    "math.MaxUint32": ("4294967295", None, "int"),
    "math.MaxUint8": ("255", None, "int"),
    "math.MaxFloat64": ("sys.float_info.max", "sys", "float64"),
}


def pkg_call_type(pkg: str, method: str) -> Optional[str]:
    """Тип результата вызова pkg.method(...) в Go."""
    if pkg == "fmt":
        if method.startswith("Sprint"):
            return "string"
        return "error" if method == "Errorf" else None
    entry = STDLIB.get(f"{pkg}.{method}")
    return entry.ret if entry else None
