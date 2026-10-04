from typing import Any, Dict, List, Optional, Tuple
import ply.lex as lex  # type: ignore


class LexicalError(Exception):
    def __init__(self, message: str, lineno: Optional[int] = None) -> None:
        self.message: str = message
        self.lineno: Optional[int] = lineno
        super().__init__(message)


class Token:
    """Обёртка над токеном PLY для удобной работы в парсере."""
    def __init__(self, type_: str, value: Any, lineno: int) -> None:
        self.type: str = type_
        self.value: Any = value
        self.lineno: int = lineno

    def __repr__(self) -> str:
        return f"Token({self.type!r}, {self.value!r})"


class GoLexer:
    """Лексический анализатор для упрощённого Go."""

    keywords: Dict[str, str] = {
        "package": "package",
        "import": "import",
        "func": "func",
        "var": "var",
        "const": "const",
        "if": "if",
        "else": "else",
        "for": "for",
        "range": "range",
        "switch": "switch",
        "select": "select",
        "case": "case",
        "default": "default",
        "return": "return",
        "break": "break",
        "continue": "continue",
        "struct": "struct",
        "interface": "interface",
        "map": "map",
        "type": "type",
        "true": "true",
        "false": "false",
    }

    tokens: Tuple[str, ...] = (
        "identifier",
        "number",
        "string",
        "assign",
        "declare",
        "plus_assign",
        "minus_assign",
        "times_assign",
        "divide_assign",
        "mod_assign",
        "and_assign",
        "or_assign",
        "xor_assign",
        "shl_assign",
        "shr_assign",
        "andnot_assign",
        "plus",
        "minus",
        "times",
        "divide",
        "mod",
        "increment",
        "decrement",
        "eq",
        "ne",
        "lt",
        "le",
        "gt",
        "ge",
        "and",
        "or",
        "not",
        "bitand",
        "bitor",
        "bitxor",
        "shl",
        "shr",
        "andnot",
        "lparen",
        "rparen",
        "lbrace",
        "rbrace",
        "lbracket",
        "rbracket",
        "comma",
        "dot",
        "semicolon",
        "colon",
        "package",
        "import",
        "func",
        "var",
        "const",
        "if",
        "else",
        "for",
        "range",
        "switch",
        "select",
        "case",
        "default",
        "return",
        "break",
        "continue",
        "struct",
        "interface",
        "map",
        "type",
        "true",
        "false",
    )

    t_shl_assign: str = r"<<="
    t_shr_assign: str = r">>="
    t_andnot_assign: str = r"&\^="

    t_declare: str = r":="
    t_eq: str = r"=="
    t_ne: str = r"!="
    t_le: str = r"<="
    t_ge: str = r">="
    t_and: str = r"&&"
    t_or: str = r"\|\|"
    t_shl: str = r"<<"
    t_shr: str = r">>"
    t_andnot: str = r"&\^"
    t_increment: str = r"\+\+"
    t_decrement: str = r"--"
    t_plus_assign: str = r"\+="
    t_minus_assign: str = r"-="
    t_times_assign: str = r"\*="
    t_divide_assign: str = r"/="
    t_mod_assign: str = r"%="
    t_and_assign: str = r"&="
    t_or_assign: str = r"\|="
    t_xor_assign: str = r"\^="

    t_assign: str = r"="
    t_plus: str = r"\+"
    t_minus: str = r"-"
    t_times: str = r"\*"
    t_divide: str = r"/"
    t_mod: str = r"%"

    t_bitand: str = r"&"
    t_bitor: str = r"\|"
    t_bitxor: str = r"\^"

    t_lt: str = r"<"
    t_gt: str = r">"
    t_not: str = r"!"

    t_lparen: str = r"\("
    t_rparen: str = r"\)"
    t_lbrace: str = r"\{"
    t_rbrace: str = r"\}"
    t_lbracket: str = r"\["
    t_rbracket: str = r"\]"

    t_comma: str = r","
    t_dot: str = r"\."
    t_semicolon: str = r";"
    t_colon: str = r":"

    t_ignore: str = " \t\r"

    def __init__(self) -> None:
        self.lexer: Optional[lex.Lexer] = None

    def t_identifier(self, t: lex.LexToken) -> lex.LexToken:
        r"[A-Za-z_][A-Za-z0-9_]*"
        t.type = self.keywords.get(t.value, "identifier")
        return t

    def t_invalid_number(self, t: lex.LexToken) -> None:
        r"\d+(\.\d*)?([eE][+-]?\d*)?[A-Za-z_]+(\.\d*)?|\d+\.\d*\.\d*"
        raise LexicalError(
            f"Некорректно задана числовая лексема '{t.value}'",
            t.lexer.lineno
        )

    def t_number(self, t: lex.LexToken) -> lex.LexToken:
        r"\d+(\.\d+)?([eE][+-]?\d+)?[i]?"
        value: str = t.value
        if value.endswith("i"):
            t.value = value
        elif "." in value or "e" in value or "E" in value:
            t.value = float(value)
        else:
            t.value = int(value)
        return t

    def t_string(self, t: lex.LexToken) -> lex.LexToken:
        r'"(\\.|[^\\\n"])*"'
        t.value = t.value[1:-1]
        return t

    def t_unterminated_string(self, t: lex.LexToken) -> None:
        r'"(\\.|[^\\\n"])*'
        raise LexicalError(
            "Не встречена закрывающая двойная кавычка при чтении строкового литерала",
            t.lexer.lineno
        )

    def t_raw_string(self, t: lex.LexToken) -> lex.LexToken:
        r"`[^`]*`"
        t.type = "string"
        t.value = t.value[1:-1]
        return t

    def t_unterminated_raw_string(self, t: lex.LexToken) -> None:
        r"`[^`]*"
        raise LexicalError(
            "Не встречена закрывающая обратная кавычка при чтении raw-строки",
            t.lexer.lineno
        )

    def t_rune(self, t: lex.LexToken) -> lex.LexToken:
        r"'(\\.|[^\\\n'])'"
        t.type = "number"
        t.value = t.value[1:-1]
        return t

    def t_unterminated_rune(self, t: lex.LexToken) -> None:
        r"'(\\.|[^\\\n'])*"
        raise LexicalError(
            "Не встречена закрывающая одинарная кавычка при чтении символьного литерала",
            t.lexer.lineno
        )

    def t_comment(self, t: lex.LexToken) -> None:
        r"//.*"
        pass

    def t_multiline_comment(self, t: lex.LexToken) -> None:
        r"/\*([^*]|\*(?!/))*\*/"
        t.lexer.lineno += t.value.count("\n")

    def t_newline(self, t: lex.LexToken) -> None:
        r"\n+"
        t.lexer.lineno += len(t.value)

    def t_error(self, t: lex.LexToken) -> None:
        raise LexicalError(
            f"Недопустимый символ '{t.value[0]}'",
            t.lexer.lineno
        )

    def build(self, **kwargs: Any) -> None:
        self.lexer = lex.lex(module=self, **kwargs)

    def tokenize(self, data: str) -> List[Token]:
        if not self.lexer:
            raise RuntimeError("Лексер не инициализирован. Вызовите build().")
        self.lexer.input(data)

        result: List[Token] = []
        while True:
            tok = self.lexer.token()
            if not tok:
                break
            result.append(Token(type_=tok.type, value=tok.value, lineno=tok.lineno))

        return result