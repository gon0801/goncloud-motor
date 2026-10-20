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
        ["sysctl", "-n", "kern.boottime"], capture_output=True, text=True
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
    p.wait()
