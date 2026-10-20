"""Banco de pruebas desechable para el motor: conf compartida, dos repos, GitHub falso,
producción falsa y una máquina ("Mac (prueba)").

    python3 proto/sandbox.py <dir>   -> imprime el home; el plan queda en <dir>/plan.md
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROTO = Path(__file__).resolve().parent
PY = sys.executable


def sh(*a: str, cwd: Path | None = None) -> None:
    subprocess.run(a, cwd=cwd, check=True, capture_output=True, stdin=subprocess.DEVNULL)


def _repo(base: Path, nombre: str, archivos: dict[str, str]) -> str:
    bare, seed = base / f"{nombre}.git", base / f"seed-{nombre}"
    sh("git", "init", "-q", "--bare", "-b", "main", str(bare))
    seed.mkdir()
    for ruta, contenido in archivos.items():
        p = seed / ruta
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(contenido)
        if ruta.endswith(".sh"):
            p.chmod(0o755)
    sh("git", "init", "-q", "-b", "main", cwd=seed)
    sh("git", "add", "-A", cwd=seed)
    sh("git", "-c", "user.name=seed", "-c", "user.email=s@l", "commit", "-q", "-m", "inicio", cwd=seed)
    sh("git", "push", "-q", str(bare), "main", cwd=seed)
    return str(bare)


def create(base: Path, *, codigo=("sonnet", "glm"), revisar=("opus", "k3"),
           modos: dict | None = None, reglas_revision="A=extra,B=ruido", sin_avance=30, wip_cada=300,
           chat_url: str = "", timeout=120) -> tuple[Path, Path]:
    modos = {"sonnet": "cuota_primero", "glm": "stdin", "opus": "ok", "k3": "ok", **(modos or {})}
    base.mkdir(parents=True, exist_ok=True)
    prod = base / "prod"
    prod.mkdir()
    app = _repo(base, "app", {
        "README.md": "app de prueba\n",
        "ci.sh": "#!/bin/bash\n# falla si algún archivo dice ROMPE\n! grep -rl --exclude-dir=.git --exclude=ci.sh ROMPE .\n",
        ".motor.toml": f"""[repo]
principal = "main"
[github]
merge = "squash"
checks_extra = ["seguridad"]
[deploy]
desplegar = "ops/desplegar.sh"
checklist = "ops/checklist.sh"
rollback = "ops/rollback.sh"
requiere = []
[pruebas.env]
PROD_DIR = "{prod}"
"""})
    docs = _repo(base, "docs", {"README.md": "documentación\n"})
    gh = base / "github"
    gh.mkdir()
    (gh / "repos.json").write_text(json.dumps({
        app: {"requeridos": ["ci", "completa"], "solo_listo": ["completa"],
              "checks": {"ci": "./ci.sh", "completa": "./ci.sh", "seguridad": "./ci.sh"}},
        docs: {"requeridos": ["ci"], "checks": {"ci": "true"}},
    }))
    conf = base / "conf"
    (conf / "reglas").mkdir(parents=True)
    (conf / "papeles.toml").write_text('[papeles]\nimplementar = "codigo"\nrevisar = "revisar"\n')
    (conf / "modelos.toml").write_text(f"[carriles]\ncodigo = {list(codigo)!r}\nrevisar = {list(revisar)!r}\n".replace("'", '"'))
    cuentas = {"sonnet": "anthropic", "opus": "anthropic", "glm": "zai", "k3": "kimi", "grok": "xai"}
    rutas = []
    for mo in dict.fromkeys([*codigo, *revisar]):
        script = "fake_reviewer.py" if mo in revisar and mo not in codigo else "fake_agent.py"
        extra = ', "--reglas", "%s"' % reglas_revision if script == "fake_reviewer.py" else ""
        rutas.append(f'[rutas.{mo}]\ncuenta = "{cuentas.get(mo, mo)}"\ncli = "{mo}"\nmax_parallel = 2\n'
                     f'argv = ["{PY}", "{PROTO / script}", "--modo", "{modos.get(mo, "ok")}"{extra}, "--prompt-file", "{{brief}}"]\n')
    (conf / "rutas.toml").write_text("\n".join(rutas))
    (conf / "general.toml").write_text(f"""[tiempos]
timeout_s = {timeout}
sin_avance_s = {sin_avance}
wip_cada_s = {wip_cada}
checks_cada_s = 0
cooldown_default_s = 7200
aviso_lento_s = 21600
[avisos]
base_url = "{chat_url or 'http://127.0.0.1:9'}"
chat = "{'123' if chat_url else ''}"
token_env = "MOTOR_TELEGRAM_TOKEN"
""")
    (conf / "reglas" / "encargo.md").write_text("Reglas: nunca --no-verify; nunca push a la rama principal.\n")
    (conf / "reglas" / "revisor.md").write_text("Revisa como el lead: solo bloquea lo reproducible.\n")
    sh("git", "init", "-q", cwd=conf)
    sh("git", "add", "-A", cwd=conf)
    sh("git", "-c", "user.name=c", "-c", "user.email=c@l", "commit", "-q", "-m", "conf", cwd=conf)
    home = base / "home"
    home.mkdir()
    (home / "maquina.toml").write_text(f"""nombre = "Mac (prueba)"
conf = "{conf}"
github = "falso:{gh}"
[env]
FAKE_STATE = "{base / 'fake-state'}"
""")
    sec = home / "secretos.env"
    sec.write_text("MOTOR_TELEGRAM_TOKEN=prueba\n")
    sec.chmod(0o600)
    plan = base / "plan.md"
    plan.write_text(f"""# Demo de saludo
autopiloto: motor
plan: demo
repo: {app}

## Bloque A — archivos de saludo
lane: gate
aviso: los archivos de saludo ya existen
prueba-real: true
| Fila | Contenido | Acepta |
| --- | --- | --- |
| A.1 | escribe: saludo.txt=hola | test "$(cat saludo.txt)" = hola |
| A.2 | escribe: despedida.txt=adios | test -f despedida.txt |

## Bloque B — versión 2
lane: gate
depende: A
aviso: la versión 2 está lista
prueba-real: true
| Fila | Contenido | Acepta |
| --- | --- | --- |
| B.1 | escribe: version.txt=v2 | test "$(cat version.txt)" = v2 |

## Bloque D — la versión 2 en producción
lane: release
depende: B
aviso: la versión 2 ya está en producción
prueba-real: test "$(cat "$PROD_DIR/version.txt")" = v2
| Fila | Contenido | Acepta |
| --- | --- | --- |
| D.1 | escribe: ops/desplegar.sh=mkdir -p "$PROD_DIR" && cp version.txt "$PROD_DIR/version.txt" | test -f ops/desplegar.sh |
| D.2 | escribe: ops/checklist.sh=test "$(cat "$PROD_DIR/version.txt")" = v2 | test -f ops/checklist.sh |
| D.3 | escribe: ops/rollback.sh=rm -f "$PROD_DIR/version.txt" | test -f ops/rollback.sh |

## Bloque C — guía en el repo de documentación
lane: gate
repo: {docs}
aviso: la guía ya está publicada
prueba-real: true
| Fila | Contenido | Acepta |
| --- | --- | --- |
| C.1 | escribe: GUIA.md=Cómo usar la app | test -f GUIA.md |

## Bloque E — permiso de Seller Central
lane: fast
humano: login Entra a Seller Central y acepta el permiso de anuncios
sonda-humano: test -f "$MOTOR_HOME/login-hecho"
""")
    return home, plan


if __name__ == "__main__":
    h, p = create(Path(sys.argv[1]))
    print(h)
    print(p)
