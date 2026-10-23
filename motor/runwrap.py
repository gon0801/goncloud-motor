"""Corre UN agente sin ventana y deja su resultado en disco. Vive aparte del daemon.

    python -m motor.runwrap --dir <intento> --cwd <carpeta> --timeout S --sin-avance S
        [--wip-rama <rama> --wip-cada S] [--home H --cuenta C] -- <argv del agente...>

- stdin cerrado y grupo de procesos propio: ningún prompt de permiso puede colgarlo.
- Falta de avance: ni el log ni la carpeta cambian en `--sin-avance` s -> se detiene.
- Ya entregó: si el agente escribió `result.json` y en `--cierre` s no vuelve a tocar nada, se
  cierra y cuenta como terminado. Grok dejaba su resultado y el proceso no salía; a los 45 min
  se mataba "sin avance" y la entrega se perdía (INGESTA 01, bloque D, dos veces).
- Con `--wip-rama`: cada `--wip-cada` s y al terminar, sube una foto del trabajo a medias
  (ver workspace.foto_wip). Un corte de luz pierde a lo mucho esos segundos.
- Con `--home` y `--cuenta`: anota al agente en el libro de cuentas (motor/llamadas.py), para que
  los shims de los CLIs de modelos cuenten su lugar en los topes.
- Escribe `outcome.json` con rename atómico. Si el daemon muere, este proceso sigue; si la
  máquina se apaga, el daemon nuevo ve un arranque distinto y lo marca "corte".
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import llamadas, plataforma
from .plataforma import contener, kwargs_grupo, matar_grupo
from .workspace import foto_wip

SHIMS = Path(__file__).resolve().parent / "shims"


_SALTA = {".git", ".venv", "node_modules", "__pycache__"}


def _mtime_arbol(raiz: Path) -> float:
    m = 0.0
    for dirpath, dirnames, filenames in os.walk(raiz):
        dirnames[:] = [d for d in dirnames if d not in _SALTA]
        for f in filenames:
            try:
                m = max(m, os.stat(os.path.join(dirpath, f)).st_mtime)
            except OSError:
                pass
    return m


def _sin_shims(cmd: list[str], env: dict) -> list[str]:
    """El agente se lanza con su CLI real: con los shims primero en el PATH, `claude` caería en el shim."""
    path = os.pathsep.join(
        p
        for p in env.get("PATH", "").split(os.pathsep)
        if os.path.abspath(p or ".") != str(SHIMS)
    )
    env_real = {**env, "PATH": path}
    if (
        os.name == "nt"
    ):  # los CLIs de npm son `.cmd`: CreateProcess no los ejecuta directo
        from .plataforma.windows import argv_windows

        return argv_windows(cmd, env_real)
    real = shutil.which(cmd[0], path=path) if cmd else None
    return [real, *cmd[1:]] if real else cmd


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--timeout", type=float, required=True)
    ap.add_argument("--sin-avance", type=float, required=True)
    ap.add_argument("--cierre", type=float, default=120)
    ap.add_argument("--wip-rama", default="")
    ap.add_argument("--wip-cada", type=float, default=300)
    ap.add_argument(
        "--wip-local",
        action="store_true",
        help="repo público: la foto no sale de esta máquina",
    )
    ap.add_argument("--home", default="")
    ap.add_argument("--cuenta", default="")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    d, cwd = Path(a.dir), Path(a.cwd)
    log = d / "log.txt"
    fotos: list[str] = []
    inicio = time.time()
    with open(log, "ab") as out:
        env_agente = {**os.environ, "MOTOR_ATTEMPT_DIR": str(d)}
        cmd = _sin_shims(cmd, env_agente)
        p = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            env=env_agente,
            **kwargs_grupo(),
        )
        contener(p)
        # la hora de inicio va primero: con agent.pid presente ya hay con qué confirmar que sigue siendo él
        (d / "agent.inicio").write_text(plataforma.hora_inicio(p.pid), encoding="utf-8")
        (d / "agent.pid").write_text(str(p.pid), encoding="utf-8")
        clave = f"agente-{os.getpid()}"
        if a.home and a.cuenta:
            llamadas.registra(
                Path(a.home),
                clave,
                {
                    "tipo": "agente",
                    "cuenta": a.cuenta,
                    "pid": os.getpid(),
                    "inicio": inicio,
                },
            )
        ultimo = (log.stat().st_size, _mtime_arbol(cwd))
        ultimo_cambio = ultimo_escaneo = time.time()
        timed_out = no_progress = cerrado = False
        resultado = d / "result.json"
        foto_lock = threading.Lock()
        foto_stop = threading.Event()

        def _foto() -> None:
            with foto_lock:
                try:
                    fotos.append(foto_wip(cwd, a.wip_rama, a.wip_local))
                except Exception as e:  # noqa: BLE001
                    fotos.append(f"error: {e}")

        def _fotos_periodicas() -> None:
            while not foto_stop.wait(a.wip_cada):
                _foto()

        if a.wip_rama:
            threading.Thread(target=_fotos_periodicas, daemon=True).start()
        while p.poll() is None:
            time.sleep(0.2)
            if time.time() - ultimo_escaneo >= 10:
                ultimo_escaneo = time.time()
                ahora = (log.stat().st_size, _mtime_arbol(cwd))
                if ahora != ultimo:
                    ultimo, ultimo_cambio = ahora, time.time()
            try:
                entrego = resultado.stat().st_mtime
            except OSError:
                entrego = 0.0
            if (
                entrego >= inicio
                and time.time() - max(entrego, ultimo_cambio) > a.cierre
            ):
                cerrado = True
                matar_grupo(p)
                break
            if time.time() - inicio > a.timeout:
                timed_out = True
            elif time.time() - ultimo_cambio > a.sin_avance:
                no_progress = True
            if timed_out or no_progress:
                matar_grupo(p)
                break
    if a.home and a.cuenta:
        llamadas.quita(Path(a.home), clave)
    if a.wip_rama:
        foto_stop.set()
        hilo_final = threading.Thread(target=_foto, daemon=True)
        hilo_final.start()
        hilo_final.join(timeout=5)
    outcome = {
        "exit": 0 if cerrado else p.returncode,
        "cerrado_tras_resultado": cerrado,
        "timed_out": timed_out,
        "no_progress": no_progress,
        "tail": log.read_text(errors="replace", encoding="utf-8")[-2000:],
        "fotos": fotos[-5:],
        "segundos": round(time.time() - inicio, 1),
    }
    tmp = d / "outcome.json.tmp"
    tmp.write_text(json.dumps(outcome, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, d / "outcome.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
