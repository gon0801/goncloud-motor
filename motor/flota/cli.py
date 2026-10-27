from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .control import (
    anotar_respaldo,
    anotar_trabajo,
    aplicar,
    asignar,
    cabeza,
    discrepancia,
    observar_ejecutable,
    publicar_proyeccion,
    recuperar,
    revertir,
)
from .manifiesto import cargar, guardar, misma_cadena, sembrar_tabla


def _json(datos: dict) -> int:
    print(json.dumps(datos, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="motor flota")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sembrar")
    s.add_argument("--tabla", type=Path, required=True)
    s.add_argument("--salida", type=Path, required=True)
    s.add_argument("--generacion", required=True)

    s = sub.add_parser("proyectar")
    s.add_argument("--manifiesto", type=Path, required=True)
    s.add_argument("--paquete", type=Path, required=True)
    s.add_argument("--home", type=Path, required=True)

    s = sub.add_parser("aplicar")
    s.add_argument("--home", type=Path, required=True)
    s.add_argument("--destino", required=True)
    s.add_argument("--corte", default="")

    s = sub.add_parser("recuperar")
    s.add_argument("--home", type=Path, required=True)

    s = sub.add_parser("revertir")
    s.add_argument("--home", type=Path, required=True)

    s = sub.add_parser("trabajo")
    s.add_argument("--home", type=Path, required=True)
    s.add_argument("--id", required=True)
    s.add_argument("--generacion", required=True)
    s.add_argument("--herramienta", required=True)

    s = sub.add_parser("asignar")
    s.add_argument("--home", type=Path, required=True)

    s = sub.add_parser("respaldo")
    s.add_argument("--home", type=Path, required=True)
    s.add_argument("--manifiesto", type=Path, required=True)
    s.add_argument("--funcion", required=True)
    s.add_argument("--modelo", required=True)
    s.add_argument("--esfuerzo", required=True)
    s.add_argument("--hasta", required=True)
    s.add_argument("--motivo", required=True)

    s = sub.add_parser("discrepancia")
    s.add_argument("--config", required=True)
    s.add_argument("--cli", required=True)

    s = sub.add_parser("observar")
    s.add_argument("--nombre", required=True)
    s.add_argument("argv", nargs=argparse.REMAINDER)

    s = sub.add_parser("cabeza")
    s.add_argument("--home", type=Path, required=True)

    s = sub.add_parser("comparar")
    s.add_argument("--manifiesto", type=Path, required=True)
    s.add_argument("--izquierda", required=True)
    s.add_argument("--derecha", required=True)

    s = sub.add_parser("descubrir")
    s.add_argument("--componentes", nargs="+", default=None)
    s.add_argument("--home", type=Path)

    for nombre in ("preparar", "actualizar"):
        s = sub.add_parser(nombre)
        s.add_argument("--home", type=Path, required=True)
        s.add_argument("--componentes", nargs="+", default=None)
        s.add_argument("--catalogo", type=Path)
        s.add_argument("--motor-origen", type=Path)
        for repo in ("hermes", "claw", "conf"):
            s.add_argument("--repo-" + repo, type=Path)

    s = sub.add_parser("estado")
    s.add_argument("--home", type=Path, required=True)

    for nombre in ("catalogos", "actualizar-modelos"):
        s = sub.add_parser(nombre)
        s.add_argument("--home", type=Path, required=True)
        s.add_argument("--tabla", type=Path)
        s.add_argument("--manifiesto", type=Path)
        s.add_argument("--repo-hermes", type=Path)

    args = ap.parse_args(argv)
    try:
        return _correr(args)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _correr(args) -> int:
    if args.cmd in ("descubrir", "preparar", "actualizar", "estado"):
        from . import actualizaciones
        from .control import _atomico

        if args.cmd == "descubrir":
            resultado = actualizaciones.descubrir(args.componentes)
            if args.home is not None:
                _atomico(args.home / "descubrimiento.json", resultado)
            return _json(resultado)
        if args.cmd == "estado":
            return _json(actualizaciones.estado(args.home))
        catalogo = (
            json.loads(args.catalogo.read_text(encoding="utf-8"))
            if args.catalogo is not None
            else actualizaciones.descubrir(args.componentes)
        )
        repos = {
            nombre: getattr(args, "repo_" + nombre)
            for nombre in ("hermes", "claw", "conf")
            if getattr(args, "repo_" + nombre) is not None
        }
        funcion = (
            actualizaciones.preparar
            if args.cmd == "preparar"
            else actualizaciones.actualizar
        )
        return _json(
            funcion(
                args.home, catalogo, motor_origen=args.motor_origen, repos=repos or None
            )
        )
    if args.cmd in ("catalogos", "actualizar-modelos"):
        from .catalogos import descubrir_modelos, publicar_politica_modelos
        from .control import _atomico
        from .manifiesto import proyectar_tabla

        manifiesto = args.manifiesto or args.home / "manifiesto.toml"
        if args.tabla is not None:
            tabla = json.loads(args.tabla.read_text(encoding="utf-8"))
        elif manifiesto.is_file():
            tabla = proyectar_tabla(cargar(manifiesto))
        else:
            raise ValueError("la politica comun aun no esta preparada")
        descubrimiento = descubrir_modelos(tabla)
        _atomico(args.home / "catalogos-modelos.json", descubrimiento)
        if args.cmd == "catalogos":
            return _json(descubrimiento)
        from .cerrojo import _cerrojo_flota

        with _cerrojo_flota(args.home):
            vigente = proyectar_tabla(cargar(manifiesto))
            if vigente["chains"] != tabla["chains"]:
                descubrimiento = descubrir_modelos(vigente)
            return _json(
                publicar_politica_modelos(
                    manifiesto, vigente, descubrimiento, hermes_repo=args.repo_hermes
                )
            )
    if args.cmd == "sembrar":
        tabla = json.loads(args.tabla.read_text(encoding="utf-8"))
        politica = sembrar_tabla(tabla, args.generacion)
        guardar(args.salida, politica)
        return _json(
            {"generacion": politica.generacion, "puestos": len(politica.puestos)}
        )
    if args.cmd == "proyectar":
        return _json(
            publicar_proyeccion(args.home, cargar(args.manifiesto), args.paquete)
        )
    if args.cmd == "aplicar":
        return _json(aplicar(args.home, args.destino, args.corte))
    if args.cmd == "recuperar":
        return _json(recuperar(args.home))
    if args.cmd == "revertir":
        return _json(revertir(args.home))
    if args.cmd == "trabajo":
        anotar_trabajo(args.home, args.id, args.generacion, args.herramienta)
        return _json({"trabajo": args.id, "generacion": args.generacion})
    if args.cmd == "asignar":
        return _json({"generacion": asignar(args.home)})
    if args.cmd == "respaldo":
        politica = anotar_respaldo(
            args.home,
            cargar(args.manifiesto),
            {
                "funcion": args.funcion,
                "modelo": args.modelo,
                "esfuerzo": args.esfuerzo,
                "hasta": args.hasta,
                "motivo": args.motivo,
            },
        )
        return _json(
            {
                "puestos": len(politica.puestos),
                "respaldo": politica.respaldo.modelo if politica.respaldo else "",
            }
        )
    if args.cmd == "discrepancia":
        return _json({"discrepancia": discrepancia(args.config, args.cli)})
    if args.cmd == "observar":
        argv = list(args.argv)
        if argv and argv[0] == "--":
            argv = argv[1:]
        return _json(observar_ejecutable(args.nombre, argv))
    if args.cmd == "cabeza":
        return _json({"cabeza": cabeza(args.home)})
    if args.cmd == "comparar":
        return _json(
            {
                "iguales": misma_cadena(
                    cargar(args.manifiesto), args.izquierda, args.derecha
                )
            }
        )
    return 2
