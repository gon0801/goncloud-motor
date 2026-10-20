"""Lo que cambia entre sistemas operativos, detrás de una sola interfaz.

- `boot_id()`: identifica el arranque actual. Un intento lanzado en otro arranque murió por
  apagón o reinicio ("corte"), aunque su pid exista hoy (los pids se reciclan).
- `lanzar_separado()`: corre un proceso que sobrevive al daemon y no comparte su terminal.
- `estado_proc()`: "vivo" | "muerto" | "corte".
- `kwargs_grupo()` / `contener()` / `matar_grupo()`: para que runwrap pueda detener al agente y a sus hijos.
"""

from __future__ import annotations

import os

if os.name == "nt":  # pragma: no cover - se prueba en la PC
    from .windows import boot_id_sistema, contener, kwargs_grupo, matar_grupo, pid_vivo
else:
    from .posix import boot_id_sistema, contener, kwargs_grupo, matar_grupo, pid_vivo

import subprocess
from pathlib import Path


def boot_id() -> str:
    """MOTOR_BOOT_ID permite simular un reinicio en las pruebas."""
    return os.environ.get("MOTOR_BOOT_ID") or boot_id_sistema()


def lanzar_separado(cmd: list[str], cwd: Path, env: dict, log: Path) -> dict:
    with open(log, "ab") as out:
        p = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            **kwargs_grupo(),
        )
    return {"pid": p.pid, "boot_id": boot_id()}


def estado_proc(proc: dict) -> str:
    if proc.get("boot_id") != boot_id():
        return "corte"
    return "vivo" if pid_vivo(int(proc["pid"])) else "muerto"


__all__ = [
    "boot_id",
    "lanzar_separado",
    "estado_proc",
    "kwargs_grupo",
    "contener",
    "matar_grupo",
]
