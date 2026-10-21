"""Implementador simulado. Hace lo que dicen las líneas `escribe: ruta=contenido` del encargo.

    fake_agent.py --modo <modo> --prompt-file <encargo>

Modos (cada uno es una falla del catálogo):
  ok              trabaja normal
  cuota_primero   la primera vez sale como si se acabara la cuota (429 con hora de reinicio)
  mentiroso       trabaja pero reporta un SHA que no existe
  stdin           lee de la terminal antes de trabajar (como un prompt de permiso)
  cuelga          no hace nada y no muestra avance
  rompe_primero   la primera entrega rompe las pruebas automáticas; la siguiente lo arregla
  parcial_lento   en el bloque A, la primera vez deja un archivo sin commitear y se queda trabajando
                  (para simular un apagón a la mitad); la siguiente termina
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


def git(*a: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *a], capture_output=True, text=True, stdin=subprocess.DEVNULL
    )


def main() -> int:
    modo = sys.argv[sys.argv.index("--modo") + 1]
    brief = Path(sys.argv[sys.argv.index("--prompt-file") + 1]).read_text()
    marca = (
        Path(os.environ["FAKE_STATE"])
        / f"{os.environ['MOTOR_MODEL']}-{os.environ['MOTOR_BLOCK']}"
    )
    marca.parent.mkdir(parents=True, exist_ok=True)
    primera = not marca.exists()
    marca.write_text("1")
    if os.environ.get("FAKE_LLAVE"):  # variable que puso la ruta (sale de secretos.env)
        (marca.parent / f"llave-{os.environ['MOTOR_MODEL']}").write_text(
            os.environ["FAKE_LLAVE"]
        )

    if modo == "cuota_primero" and primera:
        reset = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        print(f"Error: 429 Too Many Requests: weekly limit reached, resets at {reset}")
        return 1
    if modo == "stdin":
        print(
            f"stdin leído: {len(sys.stdin.read())} bytes"
        )  # con /dev/null: "" al instante
    if modo == "cuelga":
        time.sleep(3600)
        return 0
    if (
        modo == "parcial_lento" and primera and os.environ["MOTOR_BLOCK"] == "A"
    ):  # solo el bloque A
        Path("parcial.txt").write_text("trabajo a medias que no debe perderse\n")
        for i in range(600):  # sigue "trabajando" (muestra avance) hasta que lo maten
            print(f"pensando {i}", flush=True)
            time.sleep(0.5)
        return 0

    rama = re.search(r"en la rama `([^`]+)`", brief).group(1)
    git("config", "user.email", "agente@local")
    git("config", "user.name", os.environ["MOTOR_MODEL"])
    if Path("parcial.txt").exists():
        git("add", "parcial.txt")
        git("commit", "-q", "-m", "continúa lo que quedó a medias")
    for linea in brief.splitlines():
        mm = re.search(r"(?:^- (\S+): )?.*?escribe: (\S+?)=(.*)$", linea)
        if not mm:
            continue
        fila, ruta, contenido = (
            mm.group(1) or "arreglo",
            mm.group(2),
            mm.group(3).strip().split(" | ")[0],
        )
        p = Path(ruta)
        p.parent.mkdir(parents=True, exist_ok=True)
        nuevo = contenido.replace("\\n", "\n") + "\n"
        if not p.exists() or p.read_text() != nuevo:
            p.write_text(nuevo)
            git("add", "-A")
            git("commit", "-q", "-m", f"{fila}: {ruta}")
    if (
        modo == "despliegue_roto_primero"
        and primera
        and os.environ["MOTOR_BLOCK"] == "D"
    ):
        Path("ops/desplegar.sh").write_text(
            "exit 3\n"
        )  # se mergea, el despliegue falla y hay que arreglar
        git("add", "-A")
        git("commit", "-q", "-m", "D.1: desplegar (con un error)")
    if modo == "rompe_primero":
        roto = Path("roto.txt")
        if primera:
            roto.write_text("ROMPE\n")
        elif roto.exists():
            roto.unlink()
        git("add", "-A")
        git("commit", "-q", "-m", "ajuste")
    push = git("push", "-q", "origin", f"HEAD:refs/heads/{rama}")
    if push.returncode != 0:
        print(push.stderr)
        return 1
    sha = git("rev-parse", "HEAD").stdout.strip()
    if modo == "mentiroso":
        sha = "0123456789abcdef0123456789abcdef01234567"
    Path(os.environ["MOTOR_RESULT"]).write_text(
        json.dumps({"sha": sha, "resumen": "hecho"})
    )
    print("listo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
