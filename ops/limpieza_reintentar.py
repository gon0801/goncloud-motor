"""Orden manual para volver a intentar los agotados de un encargo cerrado.

    python3 ops/limpieza_reintentar.py <encargo> --base <base> --clave <clave>

No contacta GitHub ni borra archivos: el supervisor atiende los recursos pendientes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from motor.encargos.entrega.limpieza import reintentar_agotados  # noqa: E402
from motor.encargos.registro import BaseProhibida, Registro, exigir_base  # noqa: E402
from motor.encargos.ciclo import ahora_iso  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="limpieza_reintentar")
    ap.add_argument("encargo")
    ap.add_argument("--base", type=Path, required=True)
    ap.add_argument("--clave", required=True)
    args = ap.parse_args(argv)
    try:
        registro = Registro(exigir_base(args.base))
        try:
            salida = reintentar_agotados(registro, args.encargo, args.clave, ahora_iso())
        finally:
            registro.close()
    except (BaseProhibida, OSError, ValueError) as exc:
        print(f"no se pudo reiniciar la limpieza: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(json.dumps(salida, ensure_ascii=False, sort_keys=True))
    return 2 if "rechazada" in salida else 0


if __name__ == "__main__":
    raise SystemExit(main())
