"""Cota superior de borrados al activar ENTREGA_V4, sin tocar el registro."""

from __future__ import annotations

import argparse
import io
import os
import sys
from collections import Counter
from contextlib import redirect_stderr
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from motor.encargos.dominio import reducir
from motor.encargos.entrega.limpieza import candidatos, _vence
from tools.plegado import leer_encargos, ultimo_evento


@dataclass(frozen=True)
class BaseSoloLectura:
    base: Path


def _tamano(ruta: Path) -> int:
    if not ruta.exists() and not ruta.is_symlink():
        return 0
    if not ruta.is_dir() or ruta.is_symlink():
        return ruta.lstat().st_size
    total = 0
    for raiz, dirs, archivos in os.walk(ruta, followlinks=False):
        for nombre in (*dirs, *archivos):
            try:
                total += (Path(raiz) / nombre).lstat().st_size
            except OSError:
                continue
    return total


def contar(base: Path, hora: str) -> tuple[Counter, Counter, int]:
    conteos, tamanos = Counter(), Counter()
    ilegibles = 0
    registro = base / "registro.sqlite"
    for _, eventos in leer_encargos(registro, ultimo_evento(registro)):
        enc = None
        try:
            for evento in eventos:
                enc = reducir(enc, {"kind": evento["kind"], "data": evento["data"]},
                              nuevo=False)
        except Exception:
            ilegibles += 1
            continue
        if enc is None:
            continue
        for recurso in candidatos(BaseSoloLectura(base), enc):
            if recurso.tipo in ("pr", "rama"):
                if recurso.tipo == "rama" and not (
                    enc.estado == "cancelado"
                    or any(p.pr.estado in ("mergeado", "cerrado") for p in enc.prs)
                ):
                    continue
                if recurso.tipo == "pr" and not (
                    enc.estado == "cancelado" or recurso.fase in enc.canceladas
                ):
                    continue
                conteos[recurso.tipo] += 1
                continue
            if (recurso.tipo in ("espacio", "ejecucion")
                and not recurso.clave.endswith("/actual") and not _vence(enc, hora)):
                continue
            ruta = (base / "despliegues" / recurso.clave if recurso.tipo == "despliegue"
                    else base / "runtime" / recurso.clave if recurso.tipo == "credenciales"
                    else base / recurso.tipo / recurso.clave)
            if ruta.exists() or ruta.is_symlink():
                conteos[recurso.tipo] += 1
                tamanos[recurso.tipo] += _tamano(ruta)
    return conteos, tamanos, ilegibles


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path)
    args = parser.parse_args()
    base = args.base.resolve()
    if not (base / "registro.sqlite").is_file():
        parser.error("no hay registro legible en la base")
    try:
        # El lector crudo incluye rutas en sus errores; no exponerlas en un reporte.
        with redirect_stderr(io.StringIO()):
            conteos, tamanos, ilegibles = contar(
                base, datetime.now(timezone.utc).isoformat(),
            )
    except (Exception, SystemExit):
        print("no se pudo leer el registro", file=sys.stderr)
        return 2
    for recurso in ("rama", "pr", "espacio", "ejecucion",
                    "runtime", "credenciales", "despliegue"):
        print(f"{recurso}: {conteos[recurso]} recursos; {tamanos[recurso]} bytes")
    print(f"encargos ilegibles: {ilegibles}")
    print("Cota superior: no se consultó GitHub; un PR ajeno puede impedir borrar una rama.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
