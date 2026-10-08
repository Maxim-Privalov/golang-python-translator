import asyncio
import atexit
import fcntl
import os
import pty
import signal
import subprocess
import termios
import time
import tty
import uuid
from dataclasses import dataclass
from typing import Optional
 
 
class VariableIsNone(Exception):
    """Переменная инициализирована со значением None"""
 
@dataclass
class CommandResult:
    stdout: str
    stderr: str
    exit_code: int
    cwd: str
 
 
class PtyShell:
    _instance: Optional[PtyShell] = None
 
    # ---------- синглтон ----------
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            inst = super().__new__(cls)
            inst._initialized = False
            cls._instance = inst
        return cls._instance
 
    def __init__(self, shell: str = "bash", cwd: Optional[str] = None):
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
        self._spawn(self._cwd)
        atexit.register(self._kill_sync)
 
    @classmethod
    def instance(cls, **kwargs) -> "PtyShell":
        return cls(**kwargs)
 
    # ---------- создание / уничтожение процесса ----------
    def _spawn(self, cwd: str) -> None:
        master, slave = pty.openpty()
        tty.setraw(slave)  # без эха, без построчной буферизации, без \r\n
        err_r, err_w = os.pipe()
 
        def _child_setup():
            os.setsid()
            try:  # делаем pty управляющим терминалом
                fcntl.ioctl(0, termios.TIOCSCTTY, 0)
            except OSError:
                pass
 
        env = dict(os.environ, TERM="dumb", PS1="", PS2="")
        self._proc = subprocess.Popen(
            [self._shell, "--noprofile", "--norc"],
            stdin=slave,
            stdout=slave,
            stderr=err_w,
            cwd=cwd,
            env=env,
            preexec_fn=_child_setup,
            close_fds=True,
        )
        os.close(slave)
        os.close(err_w)
        os.set_blocking(master, False)
        os.set_blocking(err_r, False)
        self._master, self._err_r = master, err_r
        self._out = bytearray()
        self._err = bytearray()
 
    def _attach(self) -> None:
        loop = asyncio.get_running_loop()
        self._loop = loop
        if self._event is None:
            self._event = asyncio.Event()
            self._lock = asyncio.Lock()
        loop.add_reader(self._master, self._on_readable, self._master, self._out)
        loop.add_reader(self._err_r, self._on_readable, self._err_r, self._err)
        self._attached = True
 
    def _detach(self) -> None:
        if self._attached and self._loop is not None:
            for fd in (self._master, self._err_r):
                try:
                    self._loop.remove_reader(fd)
                except Exception:
                    pass
        self._attached = False
 
    def _close_fds(self) -> None:
        for fd in (self._master, self._err_r):
            try:
                os.close(fd)
            except OSError:
                pass
 
    def _signal(self, sig: int) -> None:
        try:
            os.killpg(self._proc.pid, sig)  # pgid == pid благодаря setsid
        except (ProcessLookupError, PermissionError):
            pass
 
    def _kill_sync(self) -> None:
        """Страховка на выход интерпретатора."""
        try:
            if self._proc.poll() is None:
                self._signal(signal.SIGKILL)
                self._proc.wait(timeout=1)
        except Exception:
            pass
 
    async def _respawn(self, cwd: str) -> None:
        self._detach()
        self._signal(signal.SIGKILL)
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
    def _on_readable(self, fd: int, buf: bytearray) -> None:
        if not self._event:
            raise VariableIsNone("asyncio event doesn't exist")
        
        if not self._loop:
            raise VariableIsNone("asyncio loop doesn't started")
        
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            return
        except OSError:  # EIO на pty, когда процесс завершился
            data = b""
        if not data:
            try:
                self._loop.remove_reader(fd)
            except Exception:
                pass
        else:
            buf.extend(data)
        self._event.set()
 
    def _ensure_started(self) -> None:
        if self._closed:
            raise RuntimeError("PtyShell закрыт")
        running = asyncio.get_running_loop()
        if not self._attached or self._loop is not running:
            self._detach()
            self._attach()
 
    # ---------- публичное API ----------
    async def execute(self, command: str, timeout: Optional[float] = 60.0) -> CommandResult:
        """Выполняет команду в постоянной сессии, возвращает stdout, stderr, код и cwd."""
        
        self._ensure_started()
        if not self._lock:
            raise VariableIsNone("asyncio lock undefined")
        
        async with self._lock:
            if self._proc.poll() is not None:  # shell умер - поднимаем заново
                await self._respawn(self._cwd)

            if not self._event:
                raise VariableIsNone("asyncio event doesn't exist")
            
            token = uuid.uuid4().hex
            self._out.clear()
            self._err.clear()
            self._event.clear()
 
            script = (
                f"{command}\n"
                f"__rc=$?\n"
                f"printf '\\n{token}:%s:%s\\n' \"$__rc\" \"$PWD\"\n"
                f"printf '\\n{token}\\n' >&2\n"
            )
            os.write(self._master, script.encode())
 
            out_mark = b"\n" + token.encode() + b":"
            err_mark = b"\n" + token.encode() + b"\n"
            deadline = None if timeout is None else time.monotonic() + timeout
 
            while True:
                oi = self._out.find(out_mark)
                nl = self._out.find(b"\n", oi + 1 + len(token)) if oi != -1 else -1
                ei = self._err.find(err_mark)
                if oi != -1 and nl != -1 and ei != -1:
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
 
            stdout = bytes(self._out[:oi]).decode("utf-8", "replace")
            rc_str, _, cwd = self._out[oi + len(out_mark):nl].decode("utf-8", "replace").partition(":")
            stderr = bytes(self._err[:ei]).decode("utf-8", "replace")
 
            self._cwd = cwd or self._cwd
            return CommandResult(stdout, stderr, int(rc_str or 0), self._cwd)
 
    async def get_cwd(self) -> str:
        """Текущая папка shell-сессии."""
        return (await self.execute(":", timeout=10)).cwd
 
    async def close(self, timeout: float = 3.0) -> None:
        """Корректное завершение: exit -> SIGTERM -> SIGKILL."""
        if self._closed:
            return
        self._closed = True
        self._detach()
        if self._proc.poll() is None:
            try:
                os.write(self._master, b"exit\n")
            except OSError:
                pass
            if not await self._wait_exit(timeout):
                self._signal(signal.SIGTERM)
                if not await self._wait_exit(timeout):
                    self._signal(signal.SIGKILL)
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
 
    r = await sh.execute("echo hello; echo oops >&2; cd /tmp")
    print(repr(r))
 
    r = await sh.execute("pwd; ls /nonexistent; false")
    print(repr(r))
 
    try:
        await sh.execute("sleep 10", timeout=1)
    except asyncio.TimeoutError as e:
        print("timeout:", e)
    print("cwd после таймаута:", await sh.get_cwd())
 
    await sh.close()
 
 
if __name__ == "__main__":
    asyncio.run(main())