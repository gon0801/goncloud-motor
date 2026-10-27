"""Entrada única para el cron publicador o una réplica, sin reiniciar servicios."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motor.flota.actualizaciones import actualizar, estado, preparar
from motor.flota.catalogos import descubrir_modelos, publicar_politica_modelos
from motor.flota.cerrojo import _cerrojo_flota
from motor.flota.compartida import exportar, importar, leer_fuente
from motor.flota.manifiesto import cargar, proyectar_tabla


def ciclo(
    home: Path, *, replica: Path | None = None, salida: Path | None = None
) -> dict:
    if replica is not None:
        importar(home, replica)
    with _cerrojo_flota(home):
        fuente = leer_fuente(home)
        if fuente is not None:
            return {
                "modo": "replica",
                "actualizacion": actualizar(home),
                "estado": estado(home),
            }
        if not (home / "manifiesto.toml").is_file():
            preparar(home)
        manifiesto = home / "manifiesto.toml"
        tabla = proyectar_tabla(cargar(manifiesto))
        catalogos = descubrir_modelos(tabla)
        modelos = publicar_politica_modelos(manifiesto, tabla, catalogos)
        resultado = actualizar(home)
        publicada = exportar(home, salida or home / "publicada.json")
        return {
            "modo": "publicador",
            "modelos": modelos["recibos"],
            "actualizacion": resultado,
            "publicacion": publicada,
            "estado": estado(home),
        }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--home", required=True, type=Path)
    ap.add_argument("--replica", type=Path, help="snapshot del publicador autenticado")
    ap.add_argument("--salida", type=Path)
    args = ap.parse_args()
    try:
        print(
            json.dumps(
                ciclo(args.home.resolve(), replica=args.replica, salida=args.salida),
                ensure_ascii=False,
            )
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
