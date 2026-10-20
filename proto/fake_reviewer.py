"""Revisor simulado. Reglas por bloque en FAKE_REVIEW, por ejemplo "A=extra,B=ruido".

  extra  pide un archivo `<bloque>-extra.txt`; bloquea con comando de reproducción
  ruido  devuelve CAMBIOS sin comando de reproducción (la regla 4 lo trata como aprobado)
  (otro) APROBADO
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


def main() -> int:
    brief = Path(sys.argv[sys.argv.index("--prompt-file") + 1]).read_text()
    m = re.search(r"bloque (\S+) · SHA ([0-9a-f]{40})", brief)
    bloque, sha = m.group(1), m.group(2)
    reglas = dict(x.split("=") for x in os.environ.get("FAKE_REVIEW", "").split(",") if "=" in x)
    regla = reglas.get(bloque, "")
    out = {"sha": sha, "veredicto": "APROBADO", "bloqueantes": [], "notas": []}
    if regla == "extra" and not Path(f"{bloque}-extra.txt").exists():
        out = {"sha": sha, "veredicto": "CAMBIOS",
               "bloqueantes": [{"texto": f"falta {bloque}-extra.txt", "repro": f"test -f {bloque}-extra.txt",
                                "arreglo": f"escribe: {bloque}-extra.txt=ok"}],
               "notas": []}
    elif regla == "ruido":
        out = {"sha": sha, "veredicto": "CAMBIOS",
               "bloqueantes": [{"texto": "no me gusta el nombre", "repro": ""}], "notas": []}
    Path(os.environ["TALLER_RESULT"]).write_text(json.dumps(out))
    print(out["veredicto"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
