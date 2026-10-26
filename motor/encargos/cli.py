"""Comandos del encargo. El texto del pedido no entra al argv remoto."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .ciclo import (
    ahora_iso,
    afirmar,
    barrer,
    consultar,
    corregir,
    marcar_espera,
    marcar_pregunta,
    ordenar,
    parada_local,
    recibir,
    reparar_motor06,
)
from .frontera import DestinoEnsayo, despachar
from .registro import BaseProhibida, Registro, exigir_base


def _json(datos: dict) -> int:
    print(json.dumps(datos, ensure_ascii=False, sort_keys=True))
    if datos.get("conflicto") or datos.get("rechazada"):
        return 2
    return 0


def _registro(base: Path) -> Registro:
    return Registro(exigir_base(base))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="motor")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("encargar")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--texto", default="")
    s.add_argument("--archivo", type=Path)
    s.add_argument("--clave", required=True)
    s.add_argument("--director", default="cli")
    s.add_argument("--restriccion", action="append", default=[])
    s.add_argument("--canal", default="")

    s = sub.add_parser("ver")
    s.add_argument("encargo")
    s.add_argument("--base", type=Path, required=True)

    for nombre in ("parar", "continuar", "cancelar"):
        s = sub.add_parser(nombre)
        s.add_argument("encargo")
        s.add_argument("--base", type=Path, required=True)
        s.add_argument("--clave", required=True)

    s = sub.add_parser("corregir")
    s.add_argument("encargo")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--clave", required=True)
    s.add_argument("--texto", required=True)

    s = sub.add_parser("efecto")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--encargo", required=True)
    s.add_argument("--id", required=True)
    s.add_argument("--operacion", required=True)
    s.add_argument("--clave-destino", required=True)
    s.add_argument("--cuerpo", default="")

    s = sub.add_parser("espera")
    s.add_argument("encargo")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--motivo", required=True)
    s.add_argument("--reanuda-en", required=True)

    s = sub.add_parser("pregunta")
    s.add_argument("encargo")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--texto", required=True)
    s.add_argument("--bloquea", action="store_true")

    s = sub.add_parser("afirmacion")
    s.add_argument("encargo")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--texto", required=True)

    s = sub.add_parser("parada-local")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--clave", required=True)

    s = sub.add_parser("supervisor")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--una-vez", action="store_true")
    s.add_argument("--intervalo", type=float, default=1.0)
    s.add_argument("--umbral", type=float, default=30.0)
    s.add_argument("--agente", default="")

    s = sub.add_parser("motor06")
    s.add_argument("--base", type=Path, required=True)
    s.add_argument("--encargo", required=True)
    s.add_argument("--producto", type=Path, required=True)
    s.add_argument("--salida", required=True)
    s.add_argument("--efecto", required=True)

    s = sub.add_parser("encargos-aplicar")
    s.add_argument("--base", type=Path)

    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        return _correr(args)
    except BaseProhibida as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001  la CLI es el borde
        print(str(exc), file=sys.stderr)
        return 1


def _correr(args) -> int:
    hora = ahora_iso()
    if args.cmd == "encargos-aplicar":
        return _aplicar_stdin(args.base)
    reg = _registro(args.base)
    try:
        return _con_registro(args, reg, hora)
    finally:
        reg.close()


def _con_registro(args, reg: Registro, hora: str) -> int:
    if args.cmd == "encargar":
        texto = args.texto
        adjuntos: tuple[str, ...] = ()
        if args.archivo:
            texto = args.archivo.read_text(encoding="utf-8")
            adjuntos = (str(args.archivo),)
        if not texto.strip():
            print("falta el pedido", file=sys.stderr)
            return 2
        return _json(
            recibir(
                reg,
                texto,
                args.clave,
                hora,
                director=args.director,
                adjuntos=adjuntos,
                restricciones=tuple(args.restriccion),
                canal=args.canal,
            )
        )
    if args.cmd == "ver":
        return _json(consultar(reg, args.encargo))
    if args.cmd in ("parar", "continuar", "cancelar"):
        salida = ordenar(reg, args.encargo, args.cmd, args.clave, hora)
        if args.cmd in ("parar", "cancelar") and not salida.get("retransmision") and not salida.get(
            "rechazada"
        ):
            recibo = salida["recibo"]
            fin = time.time() + 8
            while time.time() < fin:
                barrer(reg, ahora_iso(), time.time(), None, umbral_s=10**9)
                actual = consultar(reg, args.encargo)
                actual["recibo"] = recibo
                salida = actual
                if actual["estado"] in ("detenido", "cancelado"):
                    break
                time.sleep(0.2)
        return _json(salida)
    if args.cmd == "corregir":
        return _json(corregir(reg, args.encargo, args.texto, args.clave, hora))
    if args.cmd == "efecto":
        destino = DestinoEnsayo(reg.base / "destino")
        return _json(
            despachar(
                reg,
                args.encargo,
                destino,
                args.id,
                args.operacion,
                args.clave_destino,
                args.cuerpo,
                hora,
            )
        )
    if args.cmd == "espera":
        return _json(marcar_espera(reg, args.encargo, args.motivo, args.reanuda_en, hora))
    if args.cmd == "pregunta":
        return _json(marcar_pregunta(reg, args.encargo, args.texto, args.bloquea, hora))
    if args.cmd == "afirmacion":
        return _json(afirmar(reg, args.encargo, args.texto, hora))
    if args.cmd == "parada-local":
        return _json(parada_local(reg.base, args.clave, hora))
    if args.cmd == "motor06":
        destino = DestinoEnsayo(reg.base / "destino")
        return _json(
            reparar_motor06(
                reg, args.encargo, args.producto, args.salida, args.efecto, destino, hora
            )
        )
    if args.cmd == "supervisor":
        return _supervisor(reg, args)
    print(f"comando desconocido: {args.cmd}", file=sys.stderr)
    return 2


def _supervisor(reg: Registro, args) -> int:
    lanzador = _lanzador(args.agente) if args.agente else None

    def paso() -> list[dict]:
        hora = ahora_iso()
        return barrer(reg, hora, time.time(), lanzador, umbral_s=args.umbral)

    if args.una_vez:
        print(json.dumps(paso(), ensure_ascii=False))
        return 0
    while True:
        paso()
        time.sleep(args.intervalo)


def _lanzador(agente: str):
    def dormir(enc, prompt: Path, espacio: Path, sesion: str):
        del enc, prompt, sesion
        return ["/bin/bash", "-c", "sleep 3600"]

    def grok(enc, prompt: Path, espacio: Path, sesion: str):
        del enc
        return [
            "grok",
            "--cwd",
            str(espacio),
            "--session-id",
            sesion,
            "--permission-mode",
            "acceptEdits",
            "--always-approve",
            "--no-plan",
            "--max-turns",
            "24",
            "--disable-web-search",
            "--output-format",
            "plain",
            "--prompt-file",
            str(prompt),
        ]

    if agente == "sleep":
        return dormir
    if agente == "grok":
        return grok
    return None


def _aplicar_stdin(base_fija: Path | None) -> int:
    import os

    crudo = sys.stdin.read()
    try:
        msg = json.loads(crudo)
    except json.JSONDecodeError:
        print(json.dumps({"error": "stdin no es json"}))
        return 2
    env = os.environ.get("MOTOR_ENCARGO_BASE")
    if env:
        base = exigir_base(Path(env))
        if msg.get("base") and Path(msg["base"]).expanduser().resolve() != base:
            print(json.dumps({"error": "la base no es la de este comando"}))
            return 2
    elif base_fija:
        base = exigir_base(base_fija)
    else:
        if not msg.get("base"):
            print(json.dumps({"error": "falta la base"}))
            return 2
        base = exigir_base(Path(msg["base"]))
    op = msg.get("op")
    argv = [op or ""]
    if op == "encargar":
        argv = [
            "encargar",
            "--base",
            str(base),
            "--clave",
            msg["clave"],
            "--texto",
            msg.get("texto") or "",
            "--director",
            msg.get("director") or "remoto",
        ]
        for r in msg.get("restricciones") or []:
            argv.extend(["--restriccion", r])
    elif op == "ver":
        argv = ["ver", msg["encargo"], "--base", str(base)]
    elif op in ("parar", "continuar", "cancelar"):
        argv = [op, msg["encargo"], "--base", str(base), "--clave", msg["clave"]]
    elif op == "efecto":
        argv = [
            "efecto",
            "--base",
            str(base),
            "--encargo",
            msg["encargo"],
            "--id",
            msg["id"],
            "--operacion",
            msg["operacion"],
            "--clave-destino",
            msg["clave_destino"],
            "--cuerpo",
            msg.get("cuerpo") or "",
        ]
    else:
        print(json.dumps({"error": f"op desconocida: {op}"}))
        return 2
    return main(argv)
