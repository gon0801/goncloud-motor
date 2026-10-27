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


def crear_proceso(cmd, propiedad, **kwargs):  # noqa: ARG001  la propiedad no cambia el grupo POSIX
    from . import ProcesoCreado

    opciones = dict(kwargs)
    opciones.pop("creationflags", None)
    opciones["start_new_session"] = True
    return ProcesoCreado(subprocess.Popen(cmd, **opciones), "grupo", "")


def pid_vivo(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    ps = "/bin/ps" if os.path.exists("/bin/ps") else "ps"
    try:
        r = subprocess.run(
            [ps, "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            errors="replace",
            stdin=subprocess.DEVNULL,
        )
        if r.stdout.strip().startswith("Z"):
            return False
    except OSError:
        pass
    try:
        r, _ = os.waitpid(pid, os.WNOHANG)  # un hijo terminado sin recoger
        return r == 0
    except ChildProcessError:
        return True


def sincroniza_directorio(path) -> None:
    """Deja durable el nombre recién escrito. Un error real de disco sigue subiendo."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


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


def _miembros_grupo(pgid: int) -> list[int] | None:
    """Pids del grupo. None si no se pudo leer la lista."""
    ps = "/bin/ps" if os.path.exists("/bin/ps") else "ps"
    try:
        r = subprocess.run(
            [ps, "-ax", "-o", "pid=,pgid="],
            capture_output=True,
            text=True,
            errors="replace",
            stdin=subprocess.DEVNULL,
            env={**os.environ, "LC_ALL": "C"},
        )
    except OSError:
        return None
    if r.returncode != 0:
        return None
    miembros = []
    for line in r.stdout.splitlines():
        partes = line.split()
        if len(partes) != 2:
            return None
        try:
            pid_i, pgid_i = int(partes[0]), int(partes[1])
        except ValueError:
            return None
        if pgid_i == pgid:
            miembros.append(pid_i)
    return miembros


def presencia_grupo(pid: int, inicio: str, job: str = "") -> str:  # noqa: ARG001
    """`vivo` si queda alguien del grupo, `ausente` si no, `incierto` si no se puede mirar.

    Un líder muerto no es ausencia. El pid reusado no cuenta como descendiente.
    """
    if not inicio or pid <= 1:
        return "incierto"
    if pid_vivo(pid) and hora_inicio(pid) == inicio:
        return "vivo"
    miembros = _miembros_grupo(pid)
    if miembros is None:
        return "incierto"
    reusado = pid_vivo(pid) and hora_inicio(pid) != inicio
    otros = [m for m in miembros if not (m == pid and (reusado or not pid_vivo(pid)))]
    if otros:
        return "vivo"
    return "ausente"


def matar_grupo_pid(pid: int, inicio: str = "") -> None:
    """TERM y luego KILL al grupo. La muerte del líder no cancela el KILL de quien quedó."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if inicio and pid_vivo(pid) and hora_inicio(pid) != inicio:
            return
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        if sig == signal.SIGKILL:
            return
        fin = time.time() + 2
        while time.time() < fin and presencia_grupo(pid, inicio) == "vivo":
            time.sleep(0.05)
        if inicio and presencia_grupo(pid, inicio) != "vivo":
            return
        if not inicio and not pid_vivo(pid):
            return


def cerrar_huerfanos(pid: int, inicio: str, job: str = "") -> None:  # noqa: ARG001
    """KILL del grupo cuando el líder ya no es el proceso lanzado. No toca un pid reusado."""
    if not inicio or pid <= 1:
        return
    if pid_vivo(pid) and hora_inicio(pid) == inicio:
        return
    if pid_vivo(pid):
        miembros = _miembros_grupo(pid)
        if miembros is None:
            return
        for miembro in miembros:
            if miembro == pid:
                continue
            try:
                if os.getpgid(miembro) != pid:
                    continue
                os.kill(miembro, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                continue
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
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
