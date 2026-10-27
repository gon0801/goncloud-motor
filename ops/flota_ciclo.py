"""Entrada única para el cron publicador o una réplica, sin reiniciar servicios."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motor.flota.actualizaciones import actualizar, estado, preparar
from motor.flota.catalogos import descubrir_modelos, publicar_politica_modelos
from motor.flota.cerrojo import _cerrojo_flota
from motor.flota.compartida import exportar, importar, leer_fuente
from motor.flota.manifiesto import cargar, proyectar_tabla


def ciclo(
    home: Path,
    *,
    replica: Path | None = None,
    salida: Path | None = None,
    motor_origen: Path | None = None,
    repos: dict[str, Path] | None = None,
) -> dict:
    opciones = {"motor_origen": motor_origen, "repos": repos}
    if replica is not None:
        importar(home, replica)
    with _cerrojo_flota(home):
        fuente = leer_fuente(home)
        if fuente is not None:
            return {
                "modo": "replica",
                "actualizacion": actualizar(home, **opciones),
                "estado": estado(home),
            }
        if not (home / "manifiesto.toml").is_file():
            preparar(home, **opciones)
        manifiesto = home / "manifiesto.toml"
        tabla = proyectar_tabla(cargar(manifiesto))
        catalogos = descubrir_modelos(tabla)
        modelos = publicar_politica_modelos(
            manifiesto, tabla, catalogos, hermes_repo=(repos or {}).get("hermes")
        )
        resultado = actualizar(home, **opciones)
        publicada = exportar(home, salida or home / "publicada.json")
        return {
            "modo": "publicador",
            "modelos": modelos["recibos"],
            "actualizacion": resultado,
            "publicacion": publicada,
            "estado": estado(home),
        }


def main() -> int:
    if os.name == "nt":
        for stream in (sys.stdin, sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--home", required=True, type=Path)
    ap.add_argument("--replica", type=Path, help="snapshot del publicador autenticado")
    ap.add_argument("--salida", type=Path)
    ap.add_argument("--motor-origen", type=Path)
    ap.add_argument("--json", action="store_true", help="diagnostico estructurado")
    for repo in ("hermes", "claw", "conf"):
        ap.add_argument("--repo-" + repo, type=Path)
    args = ap.parse_args()
    repos = {
        n: getattr(args, "repo_" + n)
        for n in ("hermes", "claw", "conf")
        if getattr(args, "repo_" + n) is not None
    }
    try:
        resultado = ciclo(
            args.home.resolve(),
            replica=args.replica,
            salida=args.salida,
            motor_origen=args.motor_origen,
            repos=repos or None,
        )
        if args.json:
            print(json.dumps(resultado, ensure_ascii=False))
        elif resultado["actualizacion"]["accion"] == "ocupada":
            print(
                "Hay una actualizacion preparada. Se aplicara cuando terminen los trabajos en curso."
            )
        elif resultado["actualizacion"]["accion"] == "aplicada":
            print("Herramientas actualizadas y comprobadas.")
        elif any(
            r.get("estado") == "pendiente_comprobacion"
            for r in resultado.get("modelos", [])
        ):
            print(
                "Sigo con los modelos actuales; una actualizacion queda pendiente de comprobacion."
            )
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
