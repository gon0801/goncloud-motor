from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from motor.encargos.registro import Registro, exigir_base
from motor.encargos.seleccion import solicitar_modelos

from .directores import (
    capacidades_openbot,
    cerrar_sesion,
    conciliar_autoridad,
    consultar_canonico,
    drenar,
    encargar,
    guardar_capacidades,
    hora,
    promover,
    registrar_identidad,
    relay,
    responder_canal,
    resultado_directo,
)
from motor.encargos.ciclo import resolver_entrega


def _json(datos: dict) -> int:
    print(json.dumps(datos, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="motor director")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("encargar")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--director", choices=("hermes", "claw", "openbot"), required=True)
    s.add_argument("--via", choices=("directa", "durable"), required=True)
    s.add_argument("--canal", required=True)
    s.add_argument("--clave", required=True)
    s.add_argument("--texto", default="")
    s.add_argument("--archivo", type=Path)
    s.add_argument("--pid", type=int)
    s.add_argument("--inicio", default="")
    s.add_argument("--boot-id", default="")
    s.add_argument("--host", default="")
    s.add_argument("--sesion", default="")
    s.add_argument(
        "--entrega",
        choices=("terminado", "pr", "integrado", "desplegado"),
        default=None,
    )
    s.add_argument("--repo", default="")
    s.add_argument("--rama", default="")
    s.add_argument("--base-rama", default="")
    s.add_argument("--responsable", default="")
    s.add_argument("--revisor", default="")
    s.add_argument("--restriccion", action="append", default=[])

    s = sub.add_parser("registrar-sesion")
    s.add_argument("encargo")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--pid", type=int)
    s.add_argument("--inicio", default="")
    s.add_argument("--boot-id", default="")
    s.add_argument("--host", default="")
    s.add_argument("--sesion", default="")

    s = sub.add_parser("ver")
    s.add_argument("encargo")
    s.add_argument("--base", type=Path, required=True)

    for nombre in ("parar", "continuar", "cancelar"):
        s = sub.add_parser(nombre)
        s.add_argument("encargo")
        s.add_argument("--base", type=Path, required=True)
        s.add_argument("--clave", required=True)

    s = sub.add_parser("promover")
    s.add_argument("encargo")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--clave", required=True)

    s = sub.add_parser("cerrar-sesion")
    s.add_argument("encargo")
    s.add_argument("--base", type=Path, required=True)

    s = sub.add_parser("resultado")
    s.add_argument("encargo")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--texto", required=True)

    s = sub.add_parser("avisar")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--cortar", default="")

    s = sub.add_parser("responder")
    s.add_argument("encargo")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--solicitud", required=True)
    s.add_argument("--respuesta", required=True)
    s.add_argument("--clave", required=True)
    s.add_argument("--canal", required=True)
    s.add_argument("--sesion", default="")

    s = sub.add_parser("conciliar-autoridad")
    s.add_argument("--base", type=Path, required=True)

    s = sub.add_parser("capacidades-openbot")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--cli", required=True)

    args = ap.parse_args(argv)
    try:
        return _correr(args)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _registro(base: Path) -> Registro:
    return Registro(exigir_base(base))


def _correr(args) -> int:
    if args.cmd == "capacidades-openbot":
        doc = capacidades_openbot(args.cli.split())
        base = exigir_base(args.base)
        guardar_capacidades(base, doc)
        return _json(doc)
    reg = _registro(args.base)
    try:
        marca = hora()
        if args.cmd == "encargar":
            texto = args.texto
            adjuntos: tuple[str, ...] = ()
            if args.archivo:
                texto = args.archivo.read_text(encoding="utf-8")
                adjuntos = (str(args.archivo.resolve()),)
            if not texto.strip():
                print("falta el pedido", file=sys.stderr)
                return 2
            identidad = None
            if (
                args.pid is not None
                or args.inicio
                or args.boot_id
                or args.host
                or args.sesion
            ):
                identidad = {
                    "pid": args.pid,
                    "inicio": args.inicio,
                    "boot_id": args.boot_id,
                    "host": args.host,
                    "sesion": args.sesion,
                }
            return _json(
                encargar(
                    reg,
                    texto,
                    args.clave,
                    marca,
                    args.director,
                    args.canal,
                    args.via,
                    adjuntos,
                    identidad,
                    entrega_nivel=resolver_entrega(args.entrega, args.repo, args.via),
                    repo=args.repo,
                    rama=args.rama,
                    base_rama=args.base_rama,
                    modelos_solicitados=solicitar_modelos(args.responsable, args.revisor),
                    restricciones=tuple(args.restriccion),
                )
            )
        if args.cmd == "registrar-sesion":
            identidad = {
                "pid": args.pid,
                "inicio": args.inicio,
                "boot_id": args.boot_id,
                "host": args.host,
                "sesion": args.sesion,
            }
            return _json(registrar_identidad(reg, args.encargo, identidad))
        if args.cmd == "ver":
            return _json(consultar_canonico(reg, args.encargo))
        if args.cmd in ("parar", "continuar", "cancelar"):
            return _json(relay(reg, args.encargo, args.cmd, args.clave, marca))
        if args.cmd == "promover":
            return _json(promover(reg, args.encargo, args.clave, marca))
        if args.cmd == "cerrar-sesion":
            return _json(cerrar_sesion(reg, args.encargo))
        if args.cmd == "resultado":
            return _json(resultado_directo(reg, args.encargo, args.texto, marca))
        if args.cmd == "avisar":
            return _json(drenar(reg, args.cortar))
        if args.cmd == "responder":
            salida = responder_canal(
                reg,
                args.encargo,
                args.solicitud,
                args.respuesta,
                args.clave,
                args.canal,
                marca,
                args.sesion,
            )
            if salida.get("rechazada"):
                print(json.dumps(salida, ensure_ascii=False))
                return 2
            return _json(salida)
        if args.cmd == "conciliar-autoridad":
            return _json(conciliar_autoridad(reg))
    finally:
        reg.close()
    return 2
