"""Revisor simulado.  fake_reviewer.py --modo <ok|cuota_primero> --reglas "A=extra,B=ruido" --prompt-file <f>

extra  pide un archivo `<bloque>-extra.txt` con comando que lo reproduce
ruido  CAMBIOS sin comando de reproducción (la regla 4 lo trata como aprobado)
modo repro_falso  CAMBIOS con un bloqueante cuyo `repro` sale 0 (el motor lo baja a nota)
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path


def main() -> int:
    modo = sys.argv[sys.argv.index("--modo") + 1]
    reglas = dict(
        x.split("=")
        for x in sys.argv[sys.argv.index("--reglas") + 1].split(",")
        if "=" in x
    )
    brief = Path(sys.argv[sys.argv.index("--prompt-file") + 1]).read_text()
    m = re.search(r"bloque (\S+) · SHA ([0-9a-f]{40})", brief)
    bloque, sha = m.group(1), m.group(2)
    marca = Path(os.environ["FAKE_STATE"]) / f"rev-{os.environ['MOTOR_MODEL']}"
    marca.parent.mkdir(parents=True, exist_ok=True)
    if modo.startswith(
        "pstack"
    ):  # deja en el registro lo que hizo con poteto-mode (fake_log.py)
        from fake_log import emite

        emite(modo, not marca.exists(), brief)
        marca.write_text("1")
    if modo == "cuota_primero" and not marca.exists():
        marca.write_text("1")
        reset = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=3)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        print(f"usage limit reached, resets at {reset}")
        return 1
    out = {"sha": sha, "veredicto": "APROBADO", "bloqueantes": [], "notas": []}
    if reglas.get(bloque) == "extra" and not Path(f"{bloque}-extra.txt").exists():
        out = {
            "sha": sha,
            "veredicto": "CAMBIOS",
            "notas": [],
            "bloqueantes": [
                {
                    "texto": f"falta {bloque}-extra.txt",
                    "repro": f"test -f {bloque}-extra.txt",
                    "arreglo": f"escribe: {bloque}-extra.txt=ok",
                }
            ],
        }
    elif modo == "repro_falso":  # un bloqueante cuyo comando sale 0: no reprodujo nada
        out = {
            "sha": sha,
            "veredicto": "CAMBIOS",
            "notas": [],
            "bloqueantes": [
                {"texto": "dice que falta algo", "repro": "true", "arreglo": "?"}
            ],
        }
    elif reglas.get(bloque) == "ruido":
        out = {
            "sha": sha,
            "veredicto": "CAMBIOS",
            "notas": [],
            "bloqueantes": [{"texto": "no me gusta", "repro": ""}],
        }
    Path(os.environ["MOTOR_RESULT"]).write_text(json.dumps(out))
    print(out["veredicto"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
