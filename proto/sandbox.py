"""Crea un banco de pruebas desechable: repo "origin" bare, carpeta de producción y config.

    python3 proto/sandbox.py <dir> [--implementadores muse,glm] [--modos muse=cuota_primero,glm=stdin]
        [--revision A=extra,B=ruido] [--sin-avance 30]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROTO = Path(__file__).resolve().parent


def sh(*a: str, cwd: Path | None = None) -> None:
    subprocess.run(a, cwd=cwd, check=True, capture_output=True, stdin=subprocess.DEVNULL)


def create(base: Path, implementadores=("muse", "glm"), modos=None, revision="A=extra,B=ruido",
           sin_avance: float = 30, timeout: float = 120) -> Path:
    modos = modos or {"muse": "cuota_primero", "glm": "stdin"}
    base.mkdir(parents=True, exist_ok=True)
    origin, seed, home, prod = base / "origin.git", base / "seed", base / "home", base / "prod"
    sh("git", "init", "-q", "--bare", "-b", "main", str(origin))
    seed.mkdir()
    (seed / "README.md").write_text("Repo de prueba de taller.\n")
    (seed / "ci.sh").write_text("#!/bin/bash\n# CI de prueba: falla si algún archivo dice ROMPE\n"
                                "! grep -rl --exclude-dir=.git --exclude=ci.sh ROMPE .\n")
    (seed / "ci.sh").chmod(0o755)
    sh("git", "init", "-q", "-b", "main", cwd=seed)
    sh("git", "add", "-A", cwd=seed)
    sh("git", "-c", "user.name=seed", "-c", "user.email=s@l", "commit", "-q", "-m", "inicio", cwd=seed)
    sh("git", "push", "-q", str(origin), "main", cwd=seed)
    home.mkdir()
    prod.mkdir()
    state = base / "fake-state"
    provs = []
    for p in implementadores:
        provs.append(f"""[proveedores.{p}]
argv = ["{sys.executable}", "{PROTO / 'fake_agent.py'}", "--prompt-file", "{{brief}}"]
max_parallel = 2
env = {{ FAKE_MODE = "{modos.get(p, 'ok')}", FAKE_STATE = "{state}" }}
""")
    provs.append(f"""[proveedores.claude]
argv = ["{sys.executable}", "{PROTO / 'fake_reviewer.py'}", "--prompt-file", "{{brief}}"]
max_parallel = 2
env = {{ FAKE_REVIEW = "{revision}" }}
""")
    (home / "taller.toml").write_text(f"""[repo]
origin = "{origin}"
principal = "main"
ci = "./ci.sh"

[produccion]
dir = "{prod}"

[tiempos]
timeout_s = {timeout}
sin_avance_s = {sin_avance}
cooldown_default_s = 7200
aviso_lento_s = 21600

[escaleras]
implementadores = {list(implementadores)!r}
revisores = ["claude"]

{''.join(provs)}""".replace("'", '"'))
    return home


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", type=Path)
    ap.add_argument("--implementadores", default="muse,glm")
    ap.add_argument("--modos", default="muse=cuota_primero,glm=stdin")
    ap.add_argument("--revision", default="A=extra,B=ruido")
    ap.add_argument("--sin-avance", type=float, default=30)
    a = ap.parse_args()
    home = create(a.dir, tuple(a.implementadores.split(",")),
                  dict(x.split("=") for x in a.modos.split(",") if x), a.revision, a.sin_avance)
    print(home)
