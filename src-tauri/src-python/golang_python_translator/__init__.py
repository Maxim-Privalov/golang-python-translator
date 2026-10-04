from anyio.from_thread import start_blocking_portal
from golang_python_translator.lexer import GoLexer, LexicalError
from golang_python_translator.parser import Parser
from golang_python_translator.codegen import PythonCodeGenerator, CodeGenError
from pydantic import BaseModel
from typing import Annotated
from pytauri import (
    Commands,
    builder_factory,
    context_factory,
    State,
    Manager
)

commands: Commands = Commands()


class CodeObject(BaseModel):
    code: str



@commands.command()
async def translate(
    body: CodeObject,
    lexer: Annotated[GoLexer, State()]
) -> str:
    try:
        tokens = lexer.tokenize(body.code)
    except LexicalError as e:
        return f"\n❌ Lexical Error (line {e.lineno}): {e.message}"
        
    parser = Parser(tokens)
    try:
        tree = parser.parse()
    except SyntaxError as e:
        return f"\n❌ Syntax Error: {e}"
    
    generator = PythonCodeGenerator()
    try:
        py_code = generator.generate(tree)
    except CodeGenError as e:
        return f"\n❌ Code Generation Error: {e}"
    
    return py_code



def main() -> int:
    lexer = GoLexer()
    lexer.build()
    
    with start_blocking_portal("asyncio") as portal:  # or `trio`
        app = builder_factory().build(
            context=context_factory(),
            invoke_handler=commands.generate_handler(portal),
        )
        
        Manager.manage(app, lexer)
        
        exit_code = app.run_return()
        return exit_code
