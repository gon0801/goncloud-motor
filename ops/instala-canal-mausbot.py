"""Instala el canal `mausbot` en el `canal.json` de la PC.

Sólo toca la clave `mausbot`: `telegram`, `hermes` y cualquier otra quedan
exactamente como estaban. Volver a correrlo deja el mismo archivo.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

BASE = r"C:\Users\ehven\.motor-encargos"
PYTHON = (
    r"C:\Users\ehven\motor-runtime\cpython-3.11.16-windows-x86_64-none"
    r"\python.exe"
)
TRANSPORTE = (
    r"C:\Users\ehven\motor-sucesor\motor\integraciones\transporte_mausbot.py"
)


def _entrada(python: str, transporte: str) -> dict:
    return {
        "comando": [python, transporte, "send"],
        "conciliacion": [python, transporte, "check"],
        "idempotente": True,
    }


def instalar(base: Path, python: str, transporte: str) -> dict:
    ruta = base / "canal.json"
    try:
        doc = json.loads(ruta.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        doc = {}
    if not isinstance(doc, dict):
        raise SystemExit("canal.json no es un objeto JSON")
    doc["mausbot"] = _entrada(python, transporte)
    base.mkdir(parents=True, exist_ok=True)
    descriptor, temporal = tempfile.mkstemp(
        dir=base, prefix="canal-", suffix=".json"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as archivo:
            archivo.write(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
        Path(temporal).replace(ruta)
    except BaseException:
        Path(temporal).unlink(missing_ok=True)
        raise
    return doc["mausbot"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Instala el canal mausbot")
    ap.add_argument("--base", type=Path, default=Path(BASE))
    ap.add_argument("--python", default=PYTHON)
    ap.add_argument("--transporte", default=TRANSPORTE)
    args = ap.parse_args(argv)
    entrada = instalar(args.base, args.python, args.transporte)
    print(json.dumps(entrada, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
