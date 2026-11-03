"""Cliente del trabajador. Vive en el espacio y no importa el motor.

El agente pide una operación al supervisor y espera su respuesta. La identidad
de la sesión la escribe el host en `sesion.json`; el cliente no la inventa ni
puede cambiarla. Solo stdlib: corre dentro de la jaula del trabajador.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
SESION = RAIZ / "sesion.json"
PETICIONES = RAIZ / "peticiones"
RESPUESTAS = RAIZ / "respuestas"
CATALOGO = RAIZ / "catalogo.json"

ESPERA_S = 300.0
INTERVALO_S = 0.5

PENDIENTE = 3
FALLO = 2


def _publica(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _sesion() -> dict:
    try:
        doc = json.loads(SESION.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SystemExit(
            "no hay sesion.json: este directorio no es un espacio de encargo con el puente abierto"
        )
    if not isinstance(doc, dict) or not doc.get("conductor"):
        raise SystemExit("sesion.json no tiene la identidad que concedio el host")
    return doc


def _esperar(ident: str, segundos: float) -> dict | None:
    fin = time.time() + max(0.0, segundos)
    while True:
        path = RESPUESTAS / f"{ident}.json"
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                pass
        if time.time() >= fin:
            return None
        time.sleep(INTERVALO_S)


def _imprimir(doc: dict) -> int:
    print(json.dumps(doc, ensure_ascii=False, sort_keys=True))
    estado = str(doc.get("estado") or "")
    if estado in ("resuelta", "registrada"):
        return 0
    if estado == "pendiente":
        return PENDIENTE
    return FALLO


def pedir(args) -> int:
    sesion = _sesion()
    ident = args.id or "p_" + uuid.uuid4().hex[:12]
    if not all(c.isalnum() or c in "-_" for c in ident) or len(ident) > 64:
        print(json.dumps({"estado": "error", "motivo": "id invalido"}))
        return FALLO
    anterior = PETICIONES / f"{ident}.json"
    usada = PETICIONES / f"{ident}.hecha"
    if anterior.is_file() or usada.is_file():
        print(
            json.dumps(
                {
                    "estado": "error",
                    "motivo": "ese id ya se usó; el resultado anterior sigue en respuestas/",
                    "siguiente": "cliente.py ver " + ident,
                }
            )
        )
        return FALLO
    solicitud = {
        "id": ident,
        "encargo": sesion["encargo"],
        "fase": sesion.get("fase") or "",
        "intento": sesion.get("intento") or "",
        "generacion": sesion.get("generacion") or 0,
        "conductor": sesion["conductor"],
        "operacion": args.operacion,
        "recurso": args.recurso or "",
        "parametros": dict(args.parametro or []),
        "motivo": args.motivo or "",
        "hora": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _publica(anterior, solicitud)
    respuesta = _esperar(ident, args.espera)
    if respuesta is None:
        print(
            json.dumps(
                {
                    "estado": "pendiente",
                    "id": ident,
                    "siguiente": "revisar con: cliente.py ver " + ident,
                },
                ensure_ascii=False,
            )
        )
        return PENDIENTE
    return _imprimir(respuesta)


def ver(args) -> int:
    _sesion()
    path = RESPUESTAS / f"{args.id}.json"
    if not path.is_file():
        print(json.dumps({"estado": "pendiente", "id": args.id}, ensure_ascii=False))
        return PENDIENTE
    return _imprimir(json.loads(path.read_text(encoding="utf-8")))


def catalogo(args) -> int:
    _sesion()
    try:
        doc = json.loads(CATALOGO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {"recursos": [], "nota": "el host no concedio capacidades externas"}
    print(json.dumps(doc, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cliente")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("pedir")
    s.add_argument("--operacion", required=True)
    s.add_argument("--recurso", default="")
    s.add_argument("--parametro", action="append", default=[])
    s.add_argument("--motivo", default="")
    s.add_argument("--id", default="")
    s.add_argument("--espera", type=float, default=ESPERA_S)
    s.set_defaults(fn=pedir)
    s = sub.add_parser("ver")
    s.add_argument("id")
    s.set_defaults(fn=ver)
    s = sub.add_parser("catalogo")
    s.set_defaults(fn=catalogo)
    args = ap.parse_args(argv)
    if args.cmd == "pedir":
        pares = []
        for item in args.parametro:
            clave, _, valor = item.partition("=")
            if not clave:
                print(json.dumps({"estado": "error", "motivo": "parametro sin nombre"}))
                return FALLO
            pares.append((clave, valor))
        args.parametro = pares
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
