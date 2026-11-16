"""Comprueba la aplicación de todos los kinds sobre cada prefijo legible del corpus."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from motor.encargos.dominio import reducir  # noqa: E402
from motor.encargos.reductor import TABLA  # noqa: E402
from tools.plegado import leer_encargos, ultimo_evento  # noqa: E402


def comprobar(corpus: Path) -> int:
    kinds = ("entrega", *TABLA)
    casos = 0
    prefijos = 0
    truncados = 0
    fallos: Counter[tuple[str, str]] = Counter()
    for ruta in sorted((corpus / "registros").glob("*.sqlite")):
        for _, eventos in leer_encargos(ruta, ultimo_evento(ruta)):
            estado = None
            for evento in eventos:
                try:
                    estado = reducir(estado, evento, nuevo=False)
                except Exception:
                    truncados += 1
                    break
                prefijos += 1
                for kind in kinds:
                    casos += 1
                    try:
                        reducir(estado, {"kind": kind, "data": {}}, nuevo=False)
                    except Exception as exc:
                        fallos[kind, type(exc).__name__] += 1
    print(f"{len(kinds)} kinds; {prefijos} prefijos legibles; {casos} aplicaciones; "
          f"{truncados} registros truncados")
    for (kind, tipo), n in sorted(fallos.items()):
        print(f"{kind}: {tipo} en {n} prefijos")
    return 1 if fallos or truncados or not casos else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    args = parser.parse_args()
    sys.exit(comprobar(args.corpus))
