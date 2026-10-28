"""Генератор кода: Go AST -> (анализ) -> Python AST -> текст.

Этапы:
  1. analyzer.Analyzer       - типы и имена на узлах Go AST
  2. lower_program.ProgramLowerer - Go AST -> Python AST
  3. ast.unparse            - печать (скобки и отступы расставляет стандартная библиотека)
"""
import ast
from typing import List

from golang_python_translator import parser as G
from golang_python_translator.analyzer import Analyzer
from golang_python_translator.constructions.gotypes import CodeGenError, py_string  # noqa: F401 (реэкспорт)
from golang_python_translator.constructions.lower_program import ProgramLowerer
from golang_python_translator.constructions.symbols import Symbols


class PythonCodeGenerator:
    def __init__(self, indent: str = "    ") -> None:
        self.warnings: List[str] = []

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def to_ast(self, program: G.Program) -> ast.Module:
        """Этапы 1-2: Python AST без печати (удобно для тестов и отладки)."""
        self.warnings = []
        sym = Symbols(program, self.warn)
        Analyzer(sym, self.warn).run(program)
        module = ProgramLowerer(sym, self.warn).lower(program)
        return ast.fix_missing_locations(module)

    def generate(self, program: G.Program) -> str:
        module = self.to_ast(program)
        text = (f"# Автоматически сгенерировано из Go (package {program.package})\n"
                + ast.unparse(module) + "\n")
        try:
            compile(text, "<generated>", "exec")  # результат обязан быть валидным Python
        except SyntaxError as e:
            raise CodeGenError(f"Сгенерирован некорректный Python: {e}")
        return text
