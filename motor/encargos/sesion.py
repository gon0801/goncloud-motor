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


def _base_de(cwd: Path) -> Path | None:
    """El trabajador del ciclo vive en base/espacio/<encargo>. Otro cwd no se enjaula aquí."""
    cwd = Path(cwd).resolve()
    if cwd.parent.name == "espacio":
        return cwd.parent.parent
    return None


def _enjaular(cmd: list[str], cwd: Path, log: Path) -> tuple[list[str], str]:
    """Niega la escritura del destino y del registro. Sin sandbox-exec no hay jaula."""
    sandbox = "/usr/bin/sandbox-exec"
    if not os.path.exists(sandbox):
        return cmd, "insuficiente"
    raiz = _base_de(cwd)
    if raiz is None:
        return cmd, "insuficiente"
    destino = raiz / "destino"
    destino.mkdir(parents=True, exist_ok=True)
    destino_real = os.path.realpath(destino)
    sqlite = os.path.realpath(raiz / "registro.sqlite")
    perfil = (
        "(version 1)\n(allow default)\n"
        f'(deny file-write* (subpath "{destino_real}"))\n'
        f'(deny file-write* (literal "{sqlite}"))\n'
        f'(deny file-write* (literal "{sqlite}-wal"))\n'
        f'(deny file-write* (literal "{sqlite}-shm"))\n'
    )
    path = Path(log).parent / "jaula.sb"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(perfil, encoding="utf-8")
    return [sandbox, "-f", str(path), *cmd], "jaula"


def lanzar(cmd: list[str], cwd: Path, log: Path, env: dict | None = None) -> dict:
    log.parent.mkdir(parents=True, exist_ok=True)
    envuelto, aislamiento = _enjaular(list(cmd), Path(cwd), log)
    proc = plataforma.lanzar_separado(envuelto, cwd, env or os.environ.copy(), log)
    if not proc.get("inicio"):
        time.sleep(0.05)
        proc["inicio"] = plataforma.hora_inicio(int(proc["pid"]))
    proc["aislamiento"] = aislamiento
    return proc


def _cerrar_identidad(ident: dict) -> None:
    if not ident.get("pid") or not ident.get("inicio"):
        return
    pid = int(ident["pid"])
    inicio = str(ident.get("inicio") or "")
    job = str(ident.get("job") or "")
    plataforma.matar_proc(pid, inicio)
    plataforma.cerrar_huerfanos(pid, inicio, job)


def cerrar(directorio: Path, proc: dict | None) -> str:
    """Cierra al padre y al grupo aunque el padre ya no esté en la lista de vivos."""
    from motor.effects import _senales, observar_arbol

    if proc:
        _cerrar_identidad(proc)
    att = Marca(directorio, proc)
    for ident in _senales(att):
        if proc and int(ident.get("pid") or 0) == int(proc.get("pid") or 0):
            continue
        _cerrar_identidad(ident)
    fin = time.time() + 2
    visto = observar_arbol(att)
    while visto == "vivo" and time.time() < fin:
        time.sleep(0.05)
        if proc:
            plataforma.cerrar_huerfanos(
                int(proc["pid"]),
                str(proc.get("inicio") or ""),
                str(proc.get("job") or ""),
            )
        visto = observar_arbol(att)
    return visto


def crecer_log(directorio: Path, texto: str) -> None:
    """Utilidad del ensayo: agrandar el log no es un hecho."""
    path = Path(directorio) / "log.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(texto)
