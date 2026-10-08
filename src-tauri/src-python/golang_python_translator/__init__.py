from anyio.from_thread import start_blocking_portal
from golang_python_translator.lexer import GoLexer, LexicalError
from golang_python_translator.parser import Parser
from golang_python_translator.codegen import PythonCodeGenerator, CodeGenError
from golang_python_translator.pty_shell import PtyShell
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


class CodeRequest(BaseModel):
    code: str
    
class TranslateResponce(BaseModel):
    error: str | None
    code: str


@commands.command()
async def translate(
    body: CodeRequest,
    lexer: Annotated[GoLexer, State()]
) -> TranslateResponce:
    try:
        tokens = lexer.tokenize(body.code)
    except LexicalError as e:
        return TranslateResponce(error=f"\n❌ Lexical Error (line {e.lineno}): {e.message}", code="")
    parser = Parser(tokens)
    try:
        tree = parser.parse()
    except SyntaxError as e:
        return TranslateResponce(error=f"\n❌ Syntax Error: {e}", code="")
    
    generator = PythonCodeGenerator()
    try:
        py_code = generator.generate(tree)
    except CodeGenError as e:
        return TranslateResponce(error=f"\n❌ Code Generation Error: {e}", code="")
    
    return TranslateResponce(error=None, code=py_code)

class CommandRequest(BaseModel):
    command: str
    
class CommandResult(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    cwd: str

@commands.command()
async def execute_command(body: CommandRequest, sh: Annotated[PtyShell, State()]) -> CommandResult:
    result = await sh.execute(body.command)
    return CommandResult(
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        cwd=result.cwd
    )

def main() -> int:
    lexer = GoLexer()
    lexer.build()
    
    sh = PtyShell()
    assert sh is PtyShell()
    
    with start_blocking_portal("asyncio") as portal:  # or `trio`
        app = builder_factory().build(
            context=context_factory(),
            invoke_handler=commands.generate_handler(portal),
        )
        
        Manager.manage(app, lexer)
        Manager.manage(app, sh)
        
        exit_code = app.run_return()
        return exit_code
