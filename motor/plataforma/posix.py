"""macOS y Linux (incluido WSL)."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time


def boot_id_sistema() -> str:
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
        ],  # ruta fija: el PATH del servicio no trae /usr/sbin
        capture_output=True,
        text=True,
        errors="replace",
    )
    m = re.search(r"sec\s*=\s*(\d+)", r.stdout)
    return m.group(1) if m else "desconocido"


def kwargs_grupo() -> dict:
    return {"start_new_session": True}


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
