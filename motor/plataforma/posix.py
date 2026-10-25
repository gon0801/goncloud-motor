"""macOS y Linux (incluido WSL)."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time


TOLERANCIA_ARRANQUE_S = 120

_cache_boottime: list = [None, None]


def _sec_boottime(texto: str) -> int | None:
    m = re.search(r"sec\s*=\s*(\d+)", texto or "")
    if m:
        return int(m.group(1))
    texto = (texto or "").strip()
    return int(texto) if texto.isdigit() else None


def boot_id_desde_boottime(texto: str) -> str:
    sec = _sec_boottime(texto)
    if sec is None:
        return "desconocido"
    previo_sec, previo_id = _cache_boottime
    if previo_sec is not None and abs(sec - previo_sec) < TOLERANCIA_ARRANQUE_S:
        return previo_id
    _cache_boottime[:] = [sec, str(sec)]
    return str(sec)


def boot_id_sistema(lectura=None) -> str:
    if lectura is not None:
        return boot_id_desde_boottime(lectura())
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        pass
    r = subprocess.run(
        [
            "/usr/sbin/sysctl",
            "-n",
            "kern.boottime",
        ],
        capture_output=True,
        text=True,
        errors="replace",
    )
    return boot_id_desde_boottime(r.stdout)


def kwargs_grupo() -> dict:
    return {"start_new_session": True}


def popen_grupo(*args, **kwargs) -> subprocess.Popen:
    return subprocess.Popen(*args, **{**kwargs, **kwargs_grupo()})


def pid_vivo(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        r, _ = os.waitpid(pid, os.WNOHANG)  # un hijo terminado sin recoger
        return r == 0
    except ChildProcessError:
        return True


def hora_inicio(pid: int) -> str:
    """Hora de inicio del proceso tal como la da `ps` (""  si no existe). Distingue un pid reusado."""
    ps = "/bin/ps" if os.path.exists("/bin/ps") else "ps"
    try:
        r = subprocess.run(
            [ps, "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            errors="replace",
            stdin=subprocess.DEVNULL,
            env={**os.environ, "LC_ALL": "C"},
        )
    except OSError:
        return ""
    return " ".join(r.stdout.split())


def matar_grupo_pid(pid: int) -> None:
    """TERM y luego KILL al grupo de `pid` (el agente y el supervisor corren en su propia sesión)."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        fin = time.time() + 2
        while time.time() < fin and pid_vivo(pid):
            time.sleep(0.05)
        if not pid_vivo(pid):
            return


def candado_exclusivo(f) -> bool:
    """Toma el candado exclusivo de un archivo abierto, sin esperar. False si otro lo tiene."""
    import fcntl

    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def contener(p: subprocess.Popen) -> None:
    """Nada que hacer: `start_new_session` ya dio grupo propio."""


def matar_grupo(p: subprocess.Popen) -> None:
    """TERM al grupo; KILL solo si sigue vivo (en macOS, KILL a un grupo ya muerto da EPERM)."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(p.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        fin = time.time() + 2
        while p.poll() is None and time.time() < fin:
            time.sleep(0.05)
        if p.poll() is not None:
            return
    try:
        p.wait(timeout=5)
    except Exception:  # noqa: BLE001
        pass
