"""Corre UN agente sin ventana y deja su resultado en disco. Vive aparte del daemon.

    python -m taller.runwrap --dir <intento> --cwd <worktree> --timeout S --sin-avance S
        [--wip-push <rama>] -- <argv del agente...>

- stdin = /dev/null y sesión propia: ningún prompt de permiso puede colgarlo
  (lee EOF y falla, o el tiempo lo mata).
- Detecta falta de avance: ni el log ni el worktree cambian en `--sin-avance` s.
- Con `--wip-push`, al terminar por la razón que sea, commitea y empuja lo que haya
  en el worktree (`wip:`): un relevo o una caída nunca pierde trabajo.
- Escribe `outcome.json` con rename atómico. Si el daemon muere, este proceso
  sigue y el daemon nuevo encuentra el resultado.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


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


def _wip_push(cwd: Path, rama: str) -> str:
    def git(*a: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)
    if git("status", "--porcelain").stdout.strip():
        git("add", "-A")
        git("commit", "-q", "-m", "wip: trabajo guardado por taller al terminar el intento")
    r = git("push", "-q", "origin", f"HEAD:refs/heads/{rama}")
    return "" if r.returncode == 0 else r.stderr[-300:]


def _mata_grupo(p: subprocess.Popen) -> None:
    """TERM al grupo; KILL solo si sigue vivo. En macOS, KILL a un grupo ya muerto da EPERM."""
    for sig, espera in ((signal.SIGTERM, 2.0), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(p.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        fin = time.time() + espera
        while p.poll() is None and time.time() < fin:
            time.sleep(0.05)
        if p.poll() is not None:
            return
    p.wait()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--timeout", type=float, required=True)
    ap.add_argument("--sin-avance", type=float, required=True)
    ap.add_argument("--wip-push", default="")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    d, cwd = Path(a.dir), Path(a.cwd)
    log = d / "log.txt"
    inicio = time.time()
    with open(log, "ab") as out:
        p = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out,
                             stderr=subprocess.STDOUT, start_new_session=True,
                             env={**os.environ, "TALLER_ATTEMPT_DIR": str(d)})
        (d / "agent.pid").write_text(str(p.pid))
        ultimo = (log.stat().st_size, _mtime_arbol(cwd))
        ultimo_cambio = time.time()
        timed_out = no_progress = False
        while p.poll() is None:
            time.sleep(0.2)
            ahora = (log.stat().st_size, _mtime_arbol(cwd))
            if ahora != ultimo:
                ultimo, ultimo_cambio = ahora, time.time()
            if time.time() - inicio > a.timeout:
                timed_out = True
            elif time.time() - ultimo_cambio > a.sin_avance:
                no_progress = True
            if timed_out or no_progress:
                _mata_grupo(p)
                break
    wip = _wip_push(cwd, a.wip_push) if a.wip_push else ""
    tail = log.read_text(errors="replace")[-2000:]
    outcome = {"exit": p.returncode, "timed_out": timed_out, "no_progress": no_progress,
               "tail": tail, "wip_error": wip, "segundos": round(time.time() - inicio, 1)}
    tmp = d / "outcome.json.tmp"
    tmp.write_text(json.dumps(outcome, ensure_ascii=False))
    os.replace(tmp, d / "outcome.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
