"""Qué jobs del CI toca un cambio, por las rutas que cada job ejecuta.

La tabla sale de lo que cada job ejecuta y de lo que sus pruebas leen de
verdad. `ops-tests` corre `ops/tests/correr.sh`; `macos` y `windows` corren
listas acotadas de `tests/` que importan `motor/`, leen `conftest.py` y
`verificacion/`, y `windows` además ejecuta `ops/prepara_pc.py`. `bateria`
descubre toda la suite, así que suma `conf-ejemplo/`, `proto/`, la skill del
director, los dos documentos que se comprueban y los archivos de `ops/` que
las pruebas importan o copian. `gate` compila todas las rutas y escanea el
historial. `reconcile` no es un job de pruebas: vive en
`ai-review-publish.yml` y corre en cada evento del PR, así que no se puede
reusar por rutas.

El workflow pide la selección con `python -m motor.encargos.trabajos_ci` y
cada job declara `if: ... != 'false'`. Un job saltado por condición cuenta
como éxito para la protección de rama (GitHub lo documenta); un workflow
saltado por `paths` quedaría pendiente y bloquearía el merge. Por eso el
filtro va en el job, nunca en el disparador.

El commit temporal de `ci.correr` pide por mensaje solo los jobs que el motor
ya sabe que hacen falta. Ese mensaje no manda en un PR normal: solo se lee en
una rama `ci-prueba/`, y ni allí puede saltar un job cuyas rutas toca el
propio commit.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

TRABAJOS = ("ops-tests", "bateria", "macos", "windows")
TODOS = TRABAJOS + ("gate", "reconcile")

_RUTAS_MOTOR = ("motor/", "tests/", "conftest.py", "verificacion/")

# `bateria` descubre y corre toda la suite, así que depende además de lo que
# esas pruebas leen: la conf de ejemplo, los CLI falsos de proto, la skill del
# director, dos documentos que se comprueban literalmente y los archivos de
# ops/ que importan o copian (prepara_pc, lib.sh, sol_remoto, reversa y sus
# fixtures). Un cambio a otra parte de ops/ solo corre ops-tests.
DEPENDENCIAS = {
    "ops-tests": ("ops/",),
    "bateria": _RUTAS_MOTOR
    + (
        "conf-ejemplo/",
        "proto/",
        "skill/motor/SKILL.md",
        "docs/diseno-flujo-revisiones.md",
        "docs/flujo-revisiones.md",
        "ops/prepara_pc.py",
        "ops/tests/",
        "ops/lib.sh",
        "ops/sol_remoto.py",
        "ops/reversa_ensayo.sh",
        "ops/instala-todo.sh",
        "ops/vuelve-a-la-anterior.sh",
        "ops/maquina_local.py",
    ),
    "macos": _RUTAS_MOTOR,
    "windows": _RUTAS_MOTOR + ("ops/prepara_pc.py",),
    "gate": ("*",),
    "reconcile": ("*",),
}

# El nombre visible del job también es el nombre del check requerido.
ALIAS = {
    "Lanzamiento directo en macOS": "macos",
    "Bateria completa": "bateria",
}

SALIDAS = {
    "ops-tests": "ops_tests",
    "bateria": "bateria",
    "macos": "macos",
    "windows": "windows",
}


def trabajo(nombre: str) -> str:
    texto = str(nombre or "").strip()
    return ALIAS.get(texto, texto)


def _normalizar(ruta: str) -> str:
    limpio = str(ruta or "").strip().replace("\\", "/")
    while limpio.startswith("./"):
        limpio = limpio[2:]
    return limpio.lstrip("/")


def afecta(nombre: str, rutas) -> bool:
    """True si alguna ruta cae en lo que el job ejecuta."""
    job = trabajo(nombre)
    if job not in DEPENDENCIAS:
        return True
    cambio_workflow = any(
        _normalizar(r).startswith(".github/workflows/") for r in rutas
    )
    if cambio_workflow:
        return True
    for dependencia in DEPENDENCIAS[job]:
        if dependencia == "*":
            return True
        prefijo = _normalizar(dependencia)
        for ruta in rutas:
            limpia = _normalizar(ruta)
            if prefijo.endswith("/"):
                if limpia == prefijo[:-1] or limpia.startswith(prefijo):
                    return True
            elif limpia == prefijo:
                return True
    return False


def trabajos_a_correr(rutas, disponibles=TRABAJOS) -> tuple[str, ...]:
    """Los jobs que hay que correr; el resto reusa su verde del mismo árbol."""
    return tuple(
        job
        for job in disponibles
        if job in DEPENDENCIAS and afecta(job, rutas)
    )


def _marcador_de_jobs(
    mensaje: str, rama: str, misma_rama: bool = True
) -> tuple[str, ...] | None:
    """El commit temporal del motor trae `jobs=a,b` y su rama es `ci-prueba/`.

    Cualquier PR puede escribir ese mensaje en su commit, pero solo una rama
    del mismo repo que crea `ci.correr` trae la marca. Fuera de ella los jobs
    salen de las rutas, nunca del mensaje."""
    texto = str(mensaje or "").strip()
    if not misma_rama:
        return None
    if not str(rama or "").startswith("ci-prueba/"):
        return None
    if not texto.startswith("ci-prueba"):
        return None
    m = re.search(r"(?:^|\s)jobs=([^\s]+)", texto)
    if not m:
        return None
    valor = m.group(1).strip()
    if valor in ("todos", "all", "*"):
        return tuple(TRABAJOS)
    pedidos = [trabajo(x) for x in valor.split(",") if x.strip()]
    return tuple(j for j in TRABAJOS if j in pedidos)


def _git(*args: str) -> str:
    r = subprocess.run(
        ["git", *args], capture_output=True, text=True, timeout=60
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "git falló")
    return r.stdout


def cambios_de_git(base: str, head: str) -> tuple[str, ...]:
    texto = _git("diff", "--name-only", base, head)
    return tuple(linea.strip() for linea in texto.splitlines() if linea.strip())


def mensaje_de_git(head: str) -> str:
    return _git("log", "-1", "--format=%B", head)


def _evento() -> dict:
    ruta = os.environ.get("GITHUB_EVENT_PATH") or ""
    if not ruta or not Path(ruta).is_file():
        return {}
    try:
        return json.loads(Path(ruta).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _base_y_head(evento: dict) -> tuple[str, str, str, bool]:
    nombre = os.environ.get("GITHUB_EVENT_NAME") or ""
    if nombre == "pull_request":
        pr = evento.get("pull_request") or {}
        base = pr.get("base") or {}
        cabeza = pr.get("head") or {}
        repo_base = str((base.get("repo") or {}).get("full_name") or "")
        repo_cabeza = str((cabeza.get("repo") or {}).get("full_name") or "")
        misma_rama = bool(repo_base) and repo_base == repo_cabeza
        return (
            str(base.get("sha") or ""),
            str(cabeza.get("sha") or ""),
            str(cabeza.get("ref") or ""),
            misma_rama,
        )
    if nombre == "push":
        return (
            str(evento.get("before") or ""),
            str(evento.get("after") or ""),
            "",
            False,
        )
    return "", "", "", False


def _decidir(
    marcador: tuple[str, ...] | None,
    propios: tuple[str, ...] | None,
    cambios: tuple[str, ...] | None,
    hay_base: bool,
) -> dict[str, bool]:
    """El marcador del commit temporal manda, pero nunca puede saltar un job
    cuyas rutas toca el propio commit. Sin marcador deciden las rutas."""
    if marcador is not None:
        correr = (
            set(TRABAJOS)
            if propios is None
            else set(marcador) | set(trabajos_a_correr(propios))
        )
    elif hay_base and cambios is not None:
        correr = set(trabajos_a_correr(cambios))
    else:
        correr = set(TRABAJOS)
    return {job: job in correr for job in TRABAJOS}


def seleccion(evento: dict | None = None) -> dict[str, bool]:
    """Los cuatro jobs de pruebas, con True/False. Cualquier duda corre todos."""
    evento = evento if evento is not None else _evento()
    try:
        base, head, rama, misma_rama = _base_y_head(evento)
        if not head:
            head = os.environ.get("GITHUB_SHA") or "HEAD"
        marcador = (
            _marcador_de_jobs(mensaje_de_git(head), rama, misma_rama) if head else None
        )
        propios = None
        if marcador is not None:
            propios = cambios_de_git(f"{head}^", head)
        hay_base = bool(base) and not set(base) <= {"0"}
        cambios = cambios_de_git(base, head) if hay_base and not marcador else None
        return _decidir(marcador, propios, cambios, hay_base)
    except Exception as exc:  # sin base, git roto o mensaje ilegible: todos
        print(f"trabajos_ci: no se pudo decidir, corren todos: {exc}", file=sys.stderr)
        return {job: True for job in TRABAJOS}


def _salida_github(valores: dict[str, bool]) -> None:
    ruta = os.environ.get("GITHUB_OUTPUT") or ""
    lineas = "".join(
        f"{SALIDAS[job]}={'true' if valores[job] else 'false'}\n"
        for job in TRABAJOS
    )
    if ruta:
        with open(ruta, "a", encoding="utf-8") as fh:
            fh.write(lineas)
    else:
        sys.stdout.write(lineas)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--github-output", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--rutas", default="")
    ap.add_argument("--mensaje", default="")
    ap.add_argument("--rama", default="")
    args = ap.parse_args(argv)
    if args.rutas:
        correr = set(trabajos_a_correr(args.rutas.split(",")))
        valores = {job: job in correr for job in TRABAJOS}
    elif args.mensaje:
        marcador = _marcador_de_jobs(args.mensaje, args.rama)
        correr = set(marcador if marcador is not None else TRABAJOS)
        valores = {job: job in correr for job in TRABAJOS}
    else:
        valores = seleccion()
    if args.json:
        print(json.dumps(valores, sort_keys=True))
    elif args.github_output:
        _salida_github(valores)
    else:
        _salida_github(valores)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
