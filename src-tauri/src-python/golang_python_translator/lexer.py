from typing import Any, Dict, List, Optional, Tuple
import ply.lex as lex  # type: ignore


class GoTranslatorError(Exception):
    """Общий базовый класс ошибок лексера, парсера и генератора."""

    def __init__(self, message: str, lineno: Optional[int] = None) -> None:
        super().__init__(message)
        self.message: str = message
        self.lineno: Optional[int] = lineno


class LexicalError(GoTranslatorError):
    pass


class Token:
    """Обёртка над токеном PLY для удобной работы в парсере."""
    def __init__(self, type_: str, value: Any, lineno: int) -> None:
        self.type: str = type_
        self.value: Any = value
        self.lineno: int = lineno

    def __repr__(self) -> str:
        return f"Token({self.type!r}, {self.value!r})"


_RUNE_ESCAPES: Dict[str, str] = {
    "n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"',
    "a": "\a", "b": "\b", "f": "\f", "v": "\v",
}

# Токены, после которых перевод строки превращается в ';' (правило Go)
_SEMICOLON_AFTER = frozenset({
    "identifier", "number", "rune", "string",
    "break", "continue", "return", "fallthrough",
    "increment", "decrement", "rparen", "rbracket", "rbrace",
})


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
        # Слова Go, которые транслятор не поддерживает: лексер их знает,
        # чтобы парсер мог выдать понятную ошибку, а не принять за идентификатор.
        "go": "go",
        "defer": "defer",
        "chan": "chan",
        "goto": "goto",
        "fallthrough": "fallthrough",
    }
    # true/false - не ключевые слова, а предопределённые идентификаторы Go

    _base_tokens: Tuple[str, ...] = (
        "identifier", "number", "rune", "string",
        "assign", "declare",
        "plus_assign", "minus_assign", "times_assign", "divide_assign", "mod_assign",
        "and_assign", "or_assign", "xor_assign", "shl_assign", "shr_assign",
        "andnot_assign",
        "plus", "minus", "times", "divide", "mod",
        "increment", "decrement",
        "eq", "ne", "lt", "le", "gt", "ge",
        "and", "or", "not",
        "bitand", "bitor", "bitxor", "shl", "shr", "andnot",
        "arrow",
        "lparen", "rparen", "lbrace", "rbrace", "lbracket", "rbracket",
        "comma", "dot", "semicolon", "colon",
    )
    tokens: Tuple[str, ...] = _base_tokens + tuple(keywords.values())

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
    t_arrow: str = r"<-"
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

    # Функции-правила PLY применяются в порядке объявления, до строковых.

    def t_comment(self, t: lex.LexToken) -> None:
        r"//.*"

    def t_multiline_comment(self, t: lex.LexToken) -> None:
        r"/\*(?:[^*]|\*(?!/))*\*/"
        t.lexer.lineno += t.value.count("\n")

    def t_unterminated_comment(self, t: lex.LexToken) -> None:
        r"/\*"
        raise LexicalError("Не закрыт многострочный комментарий", t.lexer.lineno)

    def t_identifier(self, t: lex.LexToken) -> lex.LexToken:
        r"[^\W\d]\w*"
        t.type = self.keywords.get(t.value, "identifier")
        return t

    # t_number обязан стоять раньше t_invalid_number: иначе 1e5 и 3i
    # перехватываются правилом ошибки.
    def t_number(self, t: lex.LexToken) -> lex.LexToken:
        r"(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|(?:\d[\d_]*\.[\d_]*|\.\d[\d_]*|\d[\d_]*)(?:[eE][+-]?\d+)?i?)(?![A-Za-z_0-9]|\.\d)"
        v: str = t.value.replace("_", "")
        low = v.lower()
        try:
            if low.endswith("i"):
                t.value = v  # комплексный литерал остаётся строкой '3i'
            elif low.startswith(("0x", "0b", "0o")):
                t.value = int(v, 0)
            elif "." in v or "e" in low:
                t.value = float(v)
            elif len(v) > 1 and v[0] == "0":
                t.value = int(v, 8)
            else:
                t.value = int(v)
        except ValueError:
            raise LexicalError(f"Некорректно задана числовая лексема '{t.value}'", t.lexer.lineno)
        return t

    def t_invalid_number(self, t: lex.LexToken) -> None:
        r"(?:\d|\.\d)[\w.]*"
        raise LexicalError(
            f"Некорректно задана числовая лексема '{t.value}'",
            t.lexer.lineno
        )

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
        # В raw-строке обратная косая - обычный символ. Удваиваем её, чтобы
        # дальше значение обрабатывалось как обычная строка с escape-последовательностями.
        t.value = t.value[1:-1].replace("\r", "").replace("\\", "\\\\")
        t.lexer.lineno += t.value.count("\n")
        return t

    def t_unterminated_raw_string(self, t: lex.LexToken) -> None:
        r"`[^`]*"
        raise LexicalError(
            "Не встречена закрывающая обратная кавычка при чтении raw-строки",
            t.lexer.lineno + t.value.count("\n")
        )

    def t_rune(self, t: lex.LexToken) -> lex.LexToken:
        r"'(?:\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|[0-7]{3}|.)|[^\\\n'])'"
        body: str = t.value[1:-1]
        if body[0] == "\\":
            c = body[1]
            if c in "xuU":
                ch = chr(int(body[2:], 16))
            elif c in "01234567" and len(body) == 4:
                ch = chr(int(body[1:], 8))
            elif c in _RUNE_ESCAPES:
                ch = _RUNE_ESCAPES[c]
            else:
                raise LexicalError(f"Неизвестная escape-последовательность '{t.value}'", t.lexer.lineno)
        else:
            ch = body
        t.value = ord(ch)  # руна - целое число (кодовая точка)
        return t

    def t_unterminated_rune(self, t: lex.LexToken) -> None:
        r"'(?:\\.|[^\\\n'])*"
        raise LexicalError(
            "Не встречена закрывающая одинарная кавычка при чтении символьного литерала",
            t.lexer.lineno
        )

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
        """Токены с автоматической вставкой ';' в конце строк (как в Go)."""
        if not self.lexer:
            raise RuntimeError("Лексер не инициализирован. Вызовите build().")
        self.lexer.lineno = 1  # input() номер строки не сбрасывает
        self.lexer.input(data)

        result: List[Token] = []
        prev_type: Optional[str] = None
        prev_end: int = 1
        while True:
            tok = self.lexer.token()
            if not tok:
                break
            if prev_type in _SEMICOLON_AFTER and tok.lineno > prev_end:
                result.append(Token("semicolon", ";", prev_end))
            result.append(Token(type_=tok.type, value=tok.value, lineno=tok.lineno))
            prev_type = tok.type
            prev_end = self.lexer.lineno  # строка, на которой токен закончился
        if prev_type in _SEMICOLON_AFTER:
            result.append(Token("semicolon", ";", prev_end))
        return result
