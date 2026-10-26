"""Identidad de procesos del intento. El avance no se mide por el tamaño del log."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from motor import plataforma


class Marca:
    def __init__(self, directorio: Path, proc: dict | None):
        self.dir = str(directorio)
        self.proc = proc or {}


def observar(directorio: Path, proc: dict | None) -> str:
    if not proc:
        return "ausente"
    from motor.effects import observar_arbol

    return observar_arbol(Marca(directorio, proc))


def herramienta_en_curso(directorio: Path) -> bool:
    path = Path(directorio) / "herramienta.json"
    if not path.exists():
        return False
    try:
        datos = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    pid = datos.get("pid")
    inicio = datos.get("inicio")
    if not pid or not inicio:
        return True
    try:
        estado = plataforma.estado_proc(
            {"pid": int(pid), "inicio": str(inicio), "boot_id": plataforma.boot_id()}
        )
    except (OSError, TypeError, ValueError):
        return True
    return estado == "vivo"


def lanzar(cmd: list[str], cwd: Path, log: Path, env: dict | None = None) -> dict:
    log.parent.mkdir(parents=True, exist_ok=True)
    proc = plataforma.lanzar_separado(cmd, cwd, env or os.environ.copy(), log)
    if not proc.get("inicio"):
        time.sleep(0.05)
        proc["inicio"] = plataforma.hora_inicio(int(proc["pid"]))
    return proc


def cerrar(directorio: Path, proc: dict | None) -> str:
    from motor.effects import _senales, observar_arbol

    att = Marca(directorio, proc)
    for ident in _senales(att):
        plataforma.matar_proc(int(ident["pid"]), str(ident.get("inicio") or ""))
        plataforma.cerrar_huerfanos(
            int(ident["pid"]),
            str(ident.get("inicio") or ""),
            str(ident.get("job") or ""),
        )
    return observar_arbol(att)


def crecer_log(directorio: Path, texto: str) -> None:
    """Utilidad del ensayo: agrandar el log no es un hecho."""
    path = Path(directorio) / "log.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(texto)
