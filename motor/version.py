"""Qué versión del motor corre, y si la conf compartida pide otra.

La conf (`general.toml`, `[motor]`) puede fijar `version` y/o `sha`. Si lo que corre no coincide, el
daemon sale con 75: el supervisor (`servicio.supervisor`) lo relanza ya, y el arranque siguiente corre
el código que haya en disco (la instalación lo actualiza antes de relanzar).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import __version__

RAIZ = Path(__file__).resolve().parent.parent
SALIDA_RELANZAR = 75  # EX_TEMPFAIL: "me actualicé, reiníciame"
RELANZO_RAPIDO_S = 30  # si salió con 75 antes de esto, no relanzar en caliente (la conf pide algo que no hay)


def sha_actual(raiz: Path = RAIZ) -> str:
    r = subprocess.run(
        ["git", "-C", str(raiz), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True, errors="replace",
        stdin=subprocess.DEVNULL,
    )
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "sin-git"


def info(raiz: Path = RAIZ) -> dict[str, str]:
    return {"version": __version__, "sha": sha_actual(raiz)}


def pide_otra(fijada: dict[str, str], actual: dict[str, str]) -> str:
    """Vacío si lo que corre cumple lo que la conf fija; si no, la diferencia en una frase."""
    v = fijada.get("version", "")
    if v and v != actual["version"]:
        return (
            f"la conf fija la versión {v} y esta máquina corre la {actual['version']}"
        )
    s = fijada.get("sha", "")
    if s and (actual["sha"] == "sin-git" or not actual["sha"].startswith(s)):
        return f"la conf fija el commit {s[:12]} y esta máquina corre el {actual['sha'][:12]}"
    return ""


def espera_antes_de_relanzar(codigo: int, vivio_s: float) -> float:
    """Segundos que el supervisor espera tras salir el daemon. 75 relanza ya, salvo que haya salido
    de inmediato: sin eso, una conf que fija una versión que nadie instaló lo relanzaría sin parar."""
    if codigo == SALIDA_RELANZAR and vivio_s >= RELANZO_RAPIDO_S:
        return 0.0
    return 5.0 if codigo != SALIDA_RELANZAR else float(RELANZO_RAPIDO_S)
