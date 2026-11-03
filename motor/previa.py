"""Previa estructural de un plan. No ejecuta la Acepta ni la prueba real.

Una Acepta que falla porque el arreglo aún no existe está bien. Solo es rechazo si no
puede correr en esta máquina. Las `prueba-real` no se corren: pueden tocar máquinas.
"""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import shutil
import tempfile
from pathlib import Path

from .conf import Maquina, RepoConf, load_maquina
from .effects import _run, comandos_faltantes, entorno_maquina
from .plan import Plan
from .workspace import Workspace, git

RAIZ = Path(__file__).resolve().parent.parent
TOPE_PREVIA_S = 120.0

_PATRONES = (
    ("Could not resolve host", "no se puede resolver el host (Could not resolve host)"),
    (
        "Permission denied (publickey)",
        "falta acceso por ssh (Permission denied (publickey))",
    ),
    ("is not recognized", "un programa no se reconoce en esta máquina"),
)


def faltantes_de_salida(salida: str, codigo: int = 0) -> list[str]:
    """Lo que falta en la máquina según una salida, sin repetir los patrones del shell."""
    faltan = comandos_faltantes(salida)
    if "cannot import name 'tomllib'" in salida or 'cannot import name "tomllib"' in salida:
        faltan.append("tomllib")
    if codigo in (126, 127) and not faltan:
        m = re.search(r"(?:^|\s)([\w.+-]+):\s*(?:command not found|Permission denied)", salida)
        if m:
            faltan.append(m.group(1))
    return sorted(set(faltan))


def causa_de_salida(codigo: int, salida: str) -> str | None:
    """La causa de «no puede correr aquí», o None si la salida es una falla del código."""
    if "cannot import name 'tomllib'" in salida or 'cannot import name "tomllib"' in salida:
        return "falta tomllib en el Python del motor (ese Python es anterior a 3.11)"
    for patron, causa in _PATRONES:
        if patron in salida:
            return causa
    if "command not found" in salida or codigo in (126, 127):
        faltan = comandos_faltantes(salida) or faltantes_de_salida(salida, codigo)
        if faltan:
            return f"falta el programa {faltan[0]} en esta máquina"
        return f"no puede correr aquí (salida {codigo})"
    return None


def _entre_comillas_invertidas(acepta: str) -> bool:
    a = acepta.strip()
    return len(a) >= 2 and a.startswith("`") and a.endswith("`")


def _candidatos(acepta: str) -> list[str]:
    out = []
    for tok in re.split(r"[\s|&;()<>]+", acepta):
        tok = tok.strip("`'\"$(){}")
        if not tok or tok.startswith(("-", "http://", "https://")) or "=" in tok:
            continue
        if "/" in tok or re.search(r"\.[A-Za-z0-9]+$", tok):
            if tok not in out:
                out.append(tok)
    return out


def archivo_ignorado(copia: Path, acepta: str) -> str | None:
    for tok in _candidatos(acepta):
        if git("check-ignore", "-q", "--", tok, cwd=copia).returncode == 0:
            return tok
    return None


def _entorno(maquina: Maquina | None, prueba_env=()) -> dict:
    return entorno_maquina(maquina, dict(prueba_env))


def _primer_programa(comando: str) -> str:
    try:
        partes = shlex.split(comando)
    except ValueError:
        partes = comando.split()
    for p in partes:
        if "=" in p and not p.startswith("="):
            continue
        return p
    return ""


@contextlib.contextmanager
def _entorno_proceso(maquina: Maquina | None):
    """El entorno de la máquina también para git: alias y `path_extra` (como en el daemon)."""
    if maquina is None:
        yield
        return
    guardado = {k: os.environ.get(k) for k, _ in maquina.env}
    guardado["PATH"] = os.environ.get("PATH")
    os.environ.update(dict(maquina.env))
    if maquina.path_extra:
        os.environ["PATH"] = os.pathsep.join(
            [*maquina.path_extra, os.environ.get("PATH", "")]
        )
    try:
        yield
    finally:
        for k, v in guardado.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def corre_previa(
    plan: Plan, home: Path | None = None, maquina: Maquina | None = None
) -> list[str]:
    """Una línea por fila que no puede correr aquí. Vacío si la previa pasa."""
    if maquina is None and home is not None and (home / "maquina.toml").exists():
        try:
            maquina = load_maquina(home)
        except Exception:  # noqa: BLE001 - sin máquina legible se usa el entorno actual
            maquina = None
    with _entorno_proceso(maquina):
        return _corre_previa(plan, home, maquina)


def _corre_previa(
    plan: Plan, home: Path | None, maquina: Maquina | None
) -> list[str]:
    errores: list[str] = []
    with tempfile.TemporaryDirectory(prefix="motor-previa-") as td:
        base_home = home or Path(td)
        ws = Workspace(base_home, maquina.nombre if maquina else "previa")
        copias: dict[str, Path] = {}
        try:
            for block in plan.blocks:
                try:
                    espejo = ws.espejo(block.repo)
                    detectada = (
                        git(
                            "symbolic-ref",
                            "--short",
                            "refs/remotes/origin/HEAD",
                            cwd=espejo,
                        )
                        .stdout.strip()
                        .removeprefix("origin/")
                    )
                    principal = detectada or "main"
                    sha, texto = ws.leer_en_base(block.repo, principal, ".motor.toml")
                    if texto.strip():
                        rc = RepoConf.parse(texto)
                        if rc.principal != principal:
                            principal = rc.principal
                            sha, texto = ws.leer_en_base(
                                block.repo, principal, ".motor.toml"
                            )
                            rc = (
                                RepoConf.parse(texto)
                                if texto.strip()
                                else RepoConf(principal=principal)
                            )
                    else:
                        rc = RepoConf(principal=principal)
                    if not sha:
                        r = git("rev-parse", f"origin/{principal}", cwd=espejo)
                        if r.returncode != 0 or not r.stdout.strip():
                            raise RuntimeError(
                                f"no se pudo leer la punta de la rama principal {principal}"
                            )
                        sha = r.stdout.strip()
                    copia = ws.copia(
                        block.repo, sha, Path(td) / f"copia-{block.id}"
                    )
                    copias[block.id] = copia
                except Exception as e:  # noqa: BLE001 - sin copia no hay previa válida
                    detalle = str(e).strip().splitlines()[-1] if str(e).strip() else type(e).__name__
                    if block.rows:
                        for r in block.rows:
                            if r.acepta:
                                errores.append(
                                    f"{r.id}: no se pudo preparar la copia de la rama principal para la previa ({detalle})"
                                )
                    else:
                        errores.append(
                            f"{block.id}: no se pudo preparar la copia de la rama principal para la previa ({detalle})"
                        )
                    continue
                env = _entorno(maquina, rc.prueba_env)
                for r in block.rows:
                    if not r.acepta:
                        continue
                    if _entre_comillas_invertidas(r.acepta):
                        errores.append(
                            f"{r.id}: la Acepta está entre comillas invertidas"
                        )
                        continue
                    if ign := archivo_ignorado(copia, r.acepta):
                        errores.append(
                            f"{r.id}: nombra el archivo ignorado {ign} (git check-ignore)"
                        )
                        continue
        finally:
            for block in plan.blocks:
                copia = copias.get(block.id)
                if copia is not None and copia.exists():
                    try:
                        espejo = ws.espejo(block.repo)
                        git("worktree", "remove", "--force", str(copia), cwd=espejo)
                    except Exception:  # noqa: BLE001
                        pass
    return errores
