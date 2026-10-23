"""Implementador simulado. Hace lo que dicen las líneas `escribe: ruta=contenido` del encargo.

    fake_agent.py --modo <modo> --prompt-file <encargo>

Modos (cada uno es una falla del catálogo):
  ok              trabaja normal
  cuota_primero   la primera vez sale como si se acabara la cuota (429 con hora de reinicio)
  mentiroso       trabaja pero reporta un SHA que no existe
  stdin           lee de la terminal antes de trabajar (como un prompt de permiso)
  cuelga          no hace nada y no muestra avance
  rompe_primero   la primera entrega rompe las pruebas automáticas; la siguiente lo arregla
  pstack_*        trabaja normal y deja un registro con o sin poteto-mode (ver fake_log.py)
  panel*          llama a `kimi` por el shim del motor y guarda lo que contestó (panel_anida: la llamada
                  intenta, desde dentro, llamar a otro CLI)
  repro_malo_primero / repro_roto_primero / sin_repro_primero
                  la primera entrega trae un `repro` que ya pasa en la base / que no pasa ni en la punta /
                  no trae `repro`
                  (el `repro` bueno viene de una fila del encargo: `| repro: <comando>`)
  commits_sin_push   la primera vez deja dos commits locales sin push y un archivo sin commitear, y se corta;
                  la siguiente anota lo que ve en la carpeta (`retoma.json`) y entrega normal
  commits_limpios igual que commits_sin_push pero sin archivo sin commitear: la carpeta queda limpia
  borra_viejo     la primera vez borra `viejo.py` sin commitear y se corta; la siguiente anota lo que ve
  entrega_y_sigue entrega (push y result.json) y sigue imprimiendo hasta que lo detienen
  parcial_lento   en el bloque A, la primera vez deja un archivo sin commitear y se queda trabajando
                  (para simular un apagón a la mitad); la siguiente termina
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import tomllib
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

    if modo.startswith(
        "pstack"
    ):  # deja en el registro lo que hizo con poteto-mode (fake_log.py)
        from fake_log import emite

        emite(modo, primera, brief)

    if modo.startswith(
        "panel"
    ):  # pide una segunda opinión a otro CLI de modelos, por el shim del motor
        r = subprocess.run(
            [shutil.which("kimi") or "kimi", "--model", "k3", "-p", "hola"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        (
            Path(os.environ["FAKE_STATE"]) / f"panel-{os.environ['MOTOR_MODEL']}.json"
        ).write_text(json.dumps({"rc": r.returncode, "out": r.stdout, "err": r.stderr}))

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
    if modo in ("commits_sin_push", "commits_limpios", "borra_viejo"):
        if primera:
            if modo != "borra_viejo":
                for i in (1, 2):
                    Path(f"local{i}.txt").write_text(f"{i}\n")
                    git("add", "-A")
                    git("commit", "-q", "-m", f"local {i}")
                if modo == "commits_sin_push":
                    Path("sin_commit.txt").write_text("a medias\n")
            else:
                Path("viejo.py").unlink()
            sys.stdout.flush()
            os._exit(1)  # se corta sin entregar
        (Path(os.environ["FAKE_STATE"]) / "retoma.json").write_text(
            json.dumps(
                {
                    "log": git("log", "--format=%s").stdout.split("\n"),
                    "status": git("status", "--porcelain").stdout,
                    "viejo": Path("viejo.py").exists(),
                }
            )
        )
    if Path("parcial.txt").exists():
        git("add", "parcial.txt")
        git("commit", "-q", "-m", "continúa lo que quedó a medias")
    repro = ""
    for linea in brief.splitlines():
        rr = re.search(r"\| repro: (.*?)(?: \| |$)", linea)
        if rr:
            repro = rr.group(1).strip()
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
    if modo == "repro_malo_primero" and primera:
        repro = "test -f README.md"  # ya pasa en la base: no demuestra nada
    if modo == "repro_roto_primero" and primera:
        repro = "test -f nada-de-nada.txt"  # falla en la base, pero también en la punta
    if modo == "sin_repro_primero" and primera:
        repro = ""
    resultado = {"sha": sha, "resumen": "hecho"}
    if repro:
        resultado["repro"] = repro
    Path(os.environ["MOTOR_RESULT"]).write_text(json.dumps(resultado))
    if modo.startswith("pstack_real_pausa:"):
        # su cuenta queda en pausa al entregar: un regreso no podría repetir con ella
        d = Path(os.environ["MOTOR_RESULT"]).parent
        home = d.parents[2]
        conf = Path(tomllib.loads((home / "maquina.toml").read_text())["conf"])
        cuenta = tomllib.loads((conf / "rutas.toml").read_text())["rutas"][
            os.environ["MOTOR_MODEL"]
        ]["cuenta"]
        db = sqlite3.connect(home / "motor.db", isolation_level=None, timeout=30)
        db.execute(
            "INSERT INTO events (ts, plan, block, kind, data) VALUES (?,?,?,?,?)",
            (
                time.time(),
                d.parent.name,
                "",
                "Cooldown",
                json.dumps(
                    {
                        "cuenta": cuenta,
                        "hasta": time.time() + 3600,
                        "modelo": os.environ["MOTOR_MODEL"],
                    },
                    sort_keys=True,
                ),
            ),
        )
        db.close()
    print("listo")
    if modo == "entrega_y_sigue":
        for i in range(600):
            print(f"sigo {i}", flush=True)
            time.sleep(0.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
