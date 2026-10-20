"""Corre UN agente sin ventana y deja su resultado en disco. Vive aparte del daemon.

    python -m motor.runwrap --dir <intento> --cwd <carpeta> --timeout S --sin-avance S
        [--wip-rama <rama> --wip-cada S] -- <argv del agente...>

- stdin cerrado y grupo de procesos propio: ningún prompt de permiso puede colgarlo.
- Falta de avance: ni el log ni la carpeta cambian en `--sin-avance` s -> se detiene.
- Con `--wip-rama`: cada `--wip-cada` s y al terminar, sube una foto del trabajo a medias
  (ver workspace.foto_wip). Un corte de luz pierde a lo mucho esos segundos.
- Escribe `outcome.json` con rename atómico. Si el daemon muere, este proceso sigue; si la
  máquina se apaga, el daemon nuevo ve un arranque distinto y lo marca "corte".
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .plataforma import kwargs_grupo, matar_grupo
from .workspace import foto_wip


def _mtime_arbol(raiz: Path) -> float:
    m = 0.0
    for dirpath, dirnames, filenames in os.walk(raiz):
        if ".git" in dirnames:
            dirnames.remove(".git")
        for f in filenames:
            try:
                m = max(m, os.stat(os.path.join(dirpath, f)).st_mtime)
            except OSError:
                pass
    return m


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--timeout", type=float, required=True)
    ap.add_argument("--sin-avance", type=float, required=True)
    ap.add_argument("--wip-rama", default="")
    ap.add_argument("--wip-cada", type=float, default=300)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    d, cwd = Path(a.dir), Path(a.cwd)
    log = d / "log.txt"
    fotos: list[str] = []
    inicio = ultima_foto = time.time()
    with open(log, "ab") as out:
        p = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                             env={**os.environ, "MOTOR_ATTEMPT_DIR": str(d)}, **kwargs_grupo())
        (d / "agent.pid").write_text(str(p.pid))
        ultimo = (log.stat().st_size, _mtime_arbol(cwd))
        ultimo_cambio = time.time()
        timed_out = no_progress = False
        while p.poll() is None:
            time.sleep(0.2)
            ahora = (log.stat().st_size, _mtime_arbol(cwd))
            if ahora != ultimo:
                ultimo, ultimo_cambio = ahora, time.time()
            if a.wip_rama and time.time() - ultima_foto >= a.wip_cada:
                fotos.append(foto_wip(cwd, a.wip_rama))
                ultima_foto = time.time()
            if time.time() - inicio > a.timeout:
                timed_out = True
            elif time.time() - ultimo_cambio > a.sin_avance:
                no_progress = True
            if timed_out or no_progress:
                matar_grupo(p)
                break
    if a.wip_rama:
        fotos.append(foto_wip(cwd, a.wip_rama))
    outcome = {"exit": p.returncode, "timed_out": timed_out, "no_progress": no_progress,
               "tail": log.read_text(errors="replace")[-2000:], "fotos": fotos[-5:],
               "segundos": round(time.time() - inicio, 1)}
    tmp = d / "outcome.json.tmp"
    tmp.write_text(json.dumps(outcome, ensure_ascii=False))
    os.replace(tmp, d / "outcome.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
