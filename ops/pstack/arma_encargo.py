"""Arma el encargo de cada CLI para ops/pstack/mide.sh con el código del motor.

    arma_encargo.py <repo> <conf> <home> imprime <cli>   el encargo y el prompt de un CLI
    arma_encargo.py <repo> <conf> <home> datos           lo que viaja a las máquinas (base64)

El encargo sale de `Effects._encargo` (con su anexo de guías) y el prompt de `prompt_de_encargo`:
aquí no se escribe a mano ningún título del encargo.
"""

from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
import tarfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

TAREA = "Lanza dos subagentes y pídeles que lean README.md. No cambies nada."
PLAN = (
    "# Medición\nautopiloto: motor\nplan: mide\nrepo: git@github.com:x/mide.git\n\n"
    "## Bloque A — medir\nlane: gate\nprueba-real: true\n"
    f"| Fila | Contenido | Acepta |\n| --- | --- | --- |\n| A.1 | {TAREA} | true |\n"
)
MARCA_BRIEF = "@@BRIEF@@"
MARCA_KB = "@@KB@@"


def arma(repo: str, conf_dir: str, home: str):
    """Devuelve `encargo(cli) -> (carpeta, info, texto)` y la conf con la que se midió."""
    sys.path.insert(0, repo)
    from motor import effects as ef
    from motor import machine as m
    from motor.conf import RepoConf, load_conf
    from motor.plan import parse_plan

    conf = load_conf(Path(conf_dir))
    if conf.pstack is None:
        raise SystemExit("la conf no trae pstack.toml: no hay nada que medir")
    celda = conf.pstack.exigencia("gate", "implementar")
    exigencias = {
        **conf.pstack.exigencias,
        ("gate", "implementar"): replace(celda, min_subagentes=2),
    }
    conf = replace(conf, pstack=replace(conf.pstack, exigencias=exigencias))
    casa = Path(home)
    fx = ef.Effects(
        SimpleNamespace(
            home=casa, conf=conf, ws=SimpleNamespace(rama=lambda p, b: f"motor/{p}/{b}")
        )
    )
    plan = parse_plan(PLAN)
    block = plan.block("A")
    rc = RepoConf.parse('[repo]\nprincipal = "main"\n')
    trabajo = casa / "trabajo"
    trabajo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=trabajo, check=True)
    a = m.StartAttempt("A", "implementar", "sonnet", 1, "a" * 40, (), False)

    def encargo(cli):
        d = casa / f"intento-{cli}"
        d.mkdir(parents=True, exist_ok=True)
        info = fx._prepara_pstack(
            d, "gate", "implementar", cli, trabajo, "mide", a.correccion
        )
        return d, info, fx._encargo(plan, block, a, trabajo, rc, info)

    return encargo, conf


def main(argv: list[str]) -> int:
    repo, conf_dir, home, modo = argv[:4]
    encargo, conf = arma(repo, conf_dir, home)
    from motor import effects as ef
    from motor import pstack as ps

    if modo == "imprime":
        cli = argv[4]
        if cli not in ps.FORMATOS:
            raise SystemExit(f"cli desconocido: {cli} (hay {', '.join(ps.FORMATOS)})")
        d, _, brief = encargo(cli)
        (d / "brief.md").write_text(brief, encoding="utf-8")
        sys.stdout.write(
            "=== encargo ===\n"
            + brief
            + "=== prompt ===\n"
            + ef.prompt_de_encargo(str(d / "brief.md"), brief)
            + "\n"
        )
        return 0
    clis, raiz = {}, ""
    for cli in ps.FORMATOS:
        _, info, brief = encargo(cli)
        raiz = info["raiz"]
        # el KB lo calcula cada máquina con el encargo que escribe allí (la ruta cambia el tamaño);
        # la plantilla es la misma que usa `prompt_de_encargo`
        clis[cli] = {
            "encargo": brief,
            "prompt": ef.PLANTILLA_PROMPT.format(ruta=MARCA_BRIEF, kb=MARCA_KB),
        }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for n in ("pstack", "pstack-motor"):
            if (Path(raiz) / n).is_dir():
                tf.add(Path(raiz) / n, arcname=n)
    datos = {
        "conf_sha": conf.sha,
        "carriles": {k: list(v) for k, v in conf.carriles.items()},
        "raiz": raiz,
        "marca_brief": MARCA_BRIEF,
        "marca_kb": MARCA_KB,
        "tar": base64.b64encode(buf.getvalue()).decode("ascii"),
        "clis": clis,
    }
    sys.stdout.write(
        base64.b64encode(json.dumps(datos, ensure_ascii=False).encode("utf-8")).decode(
            "ascii"
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
