"""CLI de otro modelo, falso: lo que está detrás del shim del motor (`kimi`, `claude`...).

    fake_cli.py <nombre> [args...]

Imprime una respuesta y cuenta cuántos agentes y llamadas vivos ve en el libro de cuentas del motor.
Con FAKE_ANIDA=1 intenta, desde dentro de esta llamada, llamar a otro CLI de modelos (debe rechazarse).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    nombre = sys.argv[1]
    print(f"{nombre}-respuesta")
    cupos = Path(os.environ.get("MOTOR_ATTEMPT_DIR", ".")) / "cupos.json"
    if cupos.is_file():
        home = Path(json.loads(cupos.read_text())["home"])
        vivos = [
            json.loads(f.read_text()) for f in (home / "llamadas").glob("agente-*.json")
        ] + [
            json.loads(f.read_text())
            for f in (home / "llamadas").glob("llamada-*.json")
        ]
        print("libro:" + ",".join(sorted(f"{r['tipo']}/{r['cuenta']}" for r in vivos)))
    if os.environ.get("FAKE_ANIDA"):
        r = subprocess.run(
            ["claude", "-p", "hola"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        print(f"anidada-rc:{r.returncode}")
        print(f"anidada-err:{r.stderr.strip()}")
    time.sleep(float(os.environ.get("FAKE_CLI_ESPERA", "0")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
