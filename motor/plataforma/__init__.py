"""Lo que cambia entre sistemas operativos, detrás de una sola interfaz.

- `boot_id()`: identifica el arranque actual. Un intento lanzado en otro arranque murió por
  apagón o reinicio ("corte"), aunque su pid exista hoy (los pids se reciclan).
- `lanzar_separado()`: corre un proceso que sobrevive al daemon y no comparte su terminal.
- `estado_proc()`: "vivo" | "muerto" | "corte". Un pid reusado por otro proceso cuenta como muerto: se
  compara la hora de inicio que guardó `lanzar_separado`.
- `matar_proc()`: termina el grupo de un pid, solo si sigue siendo el proceso que se lanzó.
- `candado()`: candado exclusivo de un archivo (un solo daemon por home).
- `kwargs_grupo()` / `popen_grupo()` / `contener()` / `matar_grupo()`: para lanzar con grupo
  propio y poder detener al agente junto a sus hijos.
"""

from __future__ import annotations

import os

if os.name == "nt":  # pragma: no cover - se prueba en la PC
    from .windows import (
        boot_id_sistema,
        candado_exclusivo,
        contener,
        hora_inicio,
        kwargs_grupo,
        matar_grupo,
        matar_grupo_pid,
        pid_vivo,
        popen_grupo,
    )
else:
    from .posix import (
        boot_id_sistema,
        candado_exclusivo,
        contener,
        hora_inicio,
        kwargs_grupo,
        matar_grupo,
        matar_grupo_pid,
        pid_vivo,
        popen_grupo,
    )

import subprocess
from pathlib import Path


def _clase_marca(x) -> str:
    from datetime import datetime

    try:
        int(str(x))
        return "numero"
    except ValueError:
        pass
    try:
        datetime.fromisoformat(str(x).replace("Z", "+00:00"))
        return "fecha"
    except ValueError:
        return "opaca"


def mismo_arranque(a, b) -> bool:
    if a == b:
        return True
    if a in (None, "", "desconocido") or b in (None, "", "desconocido"):
        return True
    clase_a, clase_b = _clase_marca(a), _clase_marca(b)
    if clase_a != clase_b:
        return True
    if clase_a == "opaca":
        return False
    if clase_a == "numero":
        return abs(int(str(a)) - int(str(b))) < 120
    from datetime import datetime

    fa = datetime.fromisoformat(str(a).replace("Z", "+00:00"))
    fb = datetime.fromisoformat(str(b).replace("Z", "+00:00"))
    return abs((fa - fb).total_seconds()) < 120


def boot_id() -> str:
    """MOTOR_BOOT_ID permite simular un reinicio en las pruebas."""
    return os.environ.get("MOTOR_BOOT_ID") or boot_id_sistema()


def lanzar_separado(cmd: list[str], cwd: Path, env: dict, log: Path) -> dict:
    with open(log, "ab") as out:
        p = popen_grupo(
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
    return {"pid": p.pid, "boot_id": boot_id(), "inicio": hora_inicio(p.pid)}


def estado_proc(proc: dict) -> str:
    if not mismo_arranque(proc.get("boot_id"), boot_id()):
        return "corte"
    if not pid_vivo(int(proc["pid"])):
        return "muerto"
    guardada = proc.get("inicio")
    if guardada and hora_inicio(int(proc["pid"])) != guardada:
        return "muerto"  # el pid lo tiene hoy otro proceso
    return "vivo"


def matar_proc(pid: int, inicio: str) -> bool:
    """Termina el grupo de `pid` si sigue vivo y empezó a la hora `inicio`. Sin hora guardada no se
    toca nada: matar a ciegas por pid podría llevarse a un proceso ajeno."""
    if not inicio or not pid_vivo(pid) or hora_inicio(pid) != inicio:
        return False
    matar_grupo_pid(pid)
    return True


def candado(ruta: Path):
    """Abre `ruta` y toma su candado exclusivo. Devuelve el archivo (mientras viva, el candado es
    tuyo) o None si otro proceso ya lo tiene."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    f = open(ruta, "a+", encoding="utf-8")  # noqa: SIM115  se queda abierto mientras viva el daemon
    if not candado_exclusivo(f):
        f.close()
        return None
    f.seek(0)
    f.truncate()
    f.write(str(os.getpid()))
    f.flush()
    return f


__all__ = [
    "boot_id",
    "lanzar_separado",
    "estado_proc",
    "hora_inicio",
    "matar_proc",
    "candado",
    "kwargs_grupo",
    "popen_grupo",
    "contener",
    "matar_grupo",
]
