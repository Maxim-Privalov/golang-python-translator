from __future__ import annotations

import asyncio
import atexit
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Optional

# На Windows нет signal.SIGKILL, поэтому используем свои коды для _signal()
_SIG_TERM = 15
_SIG_KILL = 9


class VariableIsNone(Exception):
    """Переменная инициализирована со значением None"""


@dataclass
class CommandResult:
    stdout: str
    stderr: str
    exit_code: int
    cwd: str


class PtyShell:
    """
    Постоянная сессия cmd.exe (Windows).

    Настоящего pty здесь нет: shell общается через обычные пайпы, а чтение
    идёт в фоновых потоках, которые будят asyncio-цикл через call_soon_threadsafe
    (add_reader для пайпов на Windows не работает).
    """

    _instance: Optional[PtyShell] = None

    # ---------- синглтон ----------
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            inst = super().__new__(cls)
            inst._initialized = False
            cls._instance = inst
        return cls._instance

    def __init__(self, shell: str = "cmd.exe", cwd: Optional[str] = None):
        if self._initialized:  # повторные PtyShell() ничего не пересоздают
            return
        self._initialized = True
        self._shell = shell
        self._cwd = os.path.abspath(cwd or os.getcwd())
        self._closed = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock: Optional[asyncio.Lock] = None
        self._event: Optional[asyncio.Event] = None
        self._attached = False
        self._ready = False
        self._readers: list[threading.Thread] = []
        self._spawn(self._cwd)
        atexit.register(self._kill_sync)

    @classmethod
    def instance(cls, **kwargs) -> "PtyShell":
        return cls(**kwargs)

    # ---------- создание / уничтожение процесса ----------
    def _spawn(self, cwd: str) -> None:
        # /Q - без эха команд, /D - не читать AutoRun из реестра
        env = dict(os.environ, PROMPT="")
        self._proc = subprocess.Popen(
            [self._shell, "/Q", "/D"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
            bufsize=0,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
        )
        self._out = bytearray()
        self._err = bytearray()
        self._ready = False  # перед первой командой нужно выставить кодировку
        self._readers = [
            threading.Thread(target=self._reader, args=(self._proc.stdout, self._out), daemon=True),
            threading.Thread(target=self._reader, args=(self._proc.stderr, self._err), daemon=True),
        ]
        for t in self._readers:
            t.start()

    def _attach(self) -> None:
        loop = asyncio.get_running_loop()
        if self._event is None or self._loop is not loop:
            self._event = asyncio.Event()
            self._lock = asyncio.Lock()
        self._loop = loop
        self._attached = True

    def _detach(self) -> None:
        self._attached = False

    def _close_fds(self) -> None:
        for t in self._readers:
            t.join(timeout=1.0)
        for f in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
            try:
                if f is not None:
                    f.close()
            except OSError:
                pass

    def _signal(self, sig: int) -> None:
        """_SIG_TERM - мягкая попытка, _SIG_KILL - принудительно, оба раза со всем деревом процессов."""
        if self._proc.poll() is not None:
            return
        args = ["taskkill", "/T", "/PID", str(self._proc.pid)]
        if sig == _SIG_KILL:
            args.insert(1, "/F")
        try:
            subprocess.run(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
        except OSError:
            pass
        if sig == _SIG_KILL:
            try:
                self._proc.kill()
            except OSError:
                pass

    def _kill_sync(self) -> None:
        """Страховка на выход интерпретатора."""
        try:
            if self._proc.poll() is None:
                self._signal(_SIG_KILL)
                self._proc.wait(timeout=1)
        except Exception:
            pass

    async def _respawn(self, cwd: str) -> None:
        self._detach()
        self._signal(_SIG_KILL)
        await self._wait_exit(2.0)
        self._close_fds()
        self._spawn(cwd)
        self._attach()

    async def _wait_exit(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                return True
            await asyncio.sleep(0.05)
        return self._proc.poll() is not None

    # ---------- чтение ----------
    def _reader(self, stream, buf: bytearray) -> None:
        """Поток: читает пайп в буфер и будит цикл. Завершается по EOF."""
        try:
            while True:
                data = stream.read(65536)
                if not data:
                    break
                buf.extend(data)
                self._notify()
        except (OSError, ValueError):  # пайп закрыт
            pass
        finally:
            self._notify()  # разбудить ожидающего, чтобы он увидел смерть процесса

    def _notify(self) -> None:
        loop, event = self._loop, self._event
        if not self._attached or loop is None or event is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:  # цикл уже закрыт
            pass

    def _ensure_started(self) -> None:
        if self._closed:
            raise RuntimeError("PtyShell закрыт")
        running = asyncio.get_running_loop()
        if not self._attached or self._loop is not running:
            self._detach()
            self._attach()

    @staticmethod
    def _decode(data: bytes | bytearray) -> str:
        return bytes(data).decode("utf-8", "replace").replace("\r\n", "\n")

    async def _roundtrip(self, command: str, timeout: Optional[float]):
        """Отправляет команду с маркерами, ждёт их в обоих потоках. Возвращает (stdout, stderr, rc, cwd)."""
        if not self._event:
            raise VariableIsNone("asyncio event doesn't exist")

        token = uuid.uuid4().hex
        tb = token.encode()
        self._out.clear()
        self._err.clear()
        self._event.clear()

        # stdout: "<token>:<rc>\r\n<cwd>\r\n", stderr: "<token>\r\n".
        # cwd печатаем отдельной командой `cd`, чтобы пути с & и т.п. не ломали разбор строки.
        script = (
            f"{command}\r\n"
            f"echo {token}:%ERRORLEVEL%\r\n"
            f"cd\r\n"
            f">&2 echo {token}\r\n"
        )
        assert self._proc.stdin is not None
        self._proc.stdin.write(script.encode("utf-8"))
        self._proc.stdin.flush()

        out_mark = tb + b":"
        deadline = None if timeout is None else time.monotonic() + timeout

        while True:
            oi = self._out.find(out_mark)
            rc_end = self._out.find(b"\n", oi) if oi != -1 else -1
            cwd_end = self._out.find(b"\n", rc_end + 1) if rc_end != -1 else -1
            ei = self._err.find(tb)
            ee = self._err.find(b"\n", ei) if ei != -1 else -1
            if cwd_end != -1 and ee != -1:
                break

            if self._proc.poll() is not None:
                raise RuntimeError(
                    f"Shell завершился (код {self._proc.returncode}) во время выполнения команды"
                )
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                await self._respawn(self._cwd)  # зависшую команду проще убить вместе с shell
                raise asyncio.TimeoutError(f"Команда не завершилась за {timeout} с: {command!r}")
            self._event.clear()
            try:
                await asyncio.wait_for(self._event.wait(), remaining)
            except asyncio.TimeoutError:
                pass  # проверим дедлайн на следующей итерации

        stdout = self._decode(self._out[:oi])
        rc_str = self._out[oi + len(out_mark):rc_end].decode("utf-8", "replace").strip()
        cwd = self._out[rc_end + 1:cwd_end].decode("utf-8", "replace").strip()
        stderr = self._decode(self._err[:ei])
        return stdout, stderr, int(rc_str or 0), cwd

    # ---------- публичное API ----------
    async def execute(self, command: str, timeout: Optional[float] = 60.0) -> CommandResult:
        """Выполняет команду в постоянной сессии, возвращает stdout, stderr, код и cwd."""

        self._ensure_started()
        if not self._lock:
            raise VariableIsNone("asyncio lock undefined")

        async with self._lock:
            if self._proc.poll() is not None:  # shell умер - поднимаем заново
                await self._respawn(self._cwd)

            if not self._ready:
                # Рукопожатие: отбрасывает баннер cmd и переключает кодовую страницу на UTF-8,
                # иначе русский вывод придёт в cp866.
                await self._roundtrip("chcp 65001 >nul", 10.0)
                self._ready = True

            stdout, stderr, rc, cwd = await self._roundtrip(command, timeout)
            self._cwd = cwd or self._cwd
            return CommandResult(stdout, stderr, rc, self._cwd)

    async def get_cwd(self) -> str:
        """Текущая папка shell-сессии."""
        return (await self.execute("rem", timeout=10)).cwd

    async def close(self, timeout: float = 3.0) -> None:
        """Корректное завершение: exit -> taskkill /T -> taskkill /T /F."""
        if self._closed:
            return
        self._closed = True
        self._detach()
        if self._proc.poll() is None:
            try:
                assert self._proc.stdin is not None
                self._proc.stdin.write(b"exit\r\n")
                self._proc.stdin.flush()
            except (OSError, ValueError):
                pass
            if not await self._wait_exit(timeout):
                self._signal(_SIG_TERM)
                if not await self._wait_exit(timeout):
                    self._signal(_SIG_KILL)
                    await self._wait_exit(timeout)
        self._close_fds()
        atexit.unregister(self._kill_sync)
        PtyShell._instance = None  # следующий PtyShell() создаст новую сессию

    async def __aenter__(self) -> PtyShell:
        self._ensure_started()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()


# ---------- пример ----------
async def main() -> None:
    sh = PtyShell()
    assert sh is PtyShell()  # синглтон

    print("cwd:", await sh.get_cwd())

    r = await sh.execute("echo hello & >&2 echo oops & cd /d %TEMP%")
    print(repr(r))

    r = await sh.execute("cd & dir C:\\nonexistent & cmd /c exit 1")
    print(repr(r))

    try:
        # не `timeout /t 10`: он падает при перенаправленном stdin
        await sh.execute("ping -n 11 127.0.0.1 >nul", timeout=1)
    except asyncio.TimeoutError as e:
        print("timeout:", e)
    print("cwd после таймаута:", await sh.get_cwd())

    await sh.close()


if __name__ == "__main__":
    asyncio.run(main())
