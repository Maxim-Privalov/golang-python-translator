import sys

from codegen import CodeGenError, PythonCodeGenerator
from lexer import GoLexer, LexicalError
from parser import Parser


def read_file(filename: str) -> str:
    with open(filename, "r", encoding="utf-8") as f:
        return f.read()


def main() -> None:

    code = read_file(src)

    print("=" * 50)
    print("ИСХОДНЫЙ GO-КОД")
    print("=" * 50)
    print(code)

    lexer = GoLexer()
    lexer.build()

    try:
        tokens = lexer.tokenize(code)
    except LexicalError as e:
        print(f"\n❌ ЛЕКСИЧЕСКАЯ ОШИБКА (строка {e.lineno}): {e.message}")
        return

    print("\n" + "=" * 50)
    print("ТОКЕНЫ")
    print("=" * 50)
    print(tokens)

    parser = Parser(tokens)

    try:
        tree = parser.parse()
    except SyntaxError as e:
        print("\n❌ ОШИБКА СИНТАКСИСА:")
        print(e)
        return

    print("\n✔ Программа корректна по грамматике Go")

    generator = PythonCodeGenerator()
    try:
        py_code = generator.generate(tree)
    except CodeGenError as e:
        print("\n❌ ОШИБКА ГЕНЕРАЦИИ КОДА:")
        print(e)
        return

    print("\n" + "=" * 50)
    print("СГЕНЕРИРОВАННЫЙ PYTHON-КОД")
    print("=" * 50)
    print(py_code)

    for w in generator.warnings:
        print(f"⚠ {w}")

    with open(dst, "w", encoding="utf-8") as f:
        f.write(py_code)
    print(f"✔ Код записан в {dst}")


