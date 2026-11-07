"""Las pruebas nuevas tienen que salir rojas contra la base del PR.

En un PR toma las funciones `test_` nuevas o cambiadas de `tests/` (y
`conftest.py`), las corre con el código de la rama base en un worktree temporal
y falla si alguna **pasa** allí: una prueba que pasa sin el arreglo no demuestra
el arreglo.

Una prueba puede eximirse con `# no-roja-en-base: <razón>` en su línea `def`.
El job lista esas excepciones.

    python3 tools/pruebas_nuevas.py --base origin/main
"""

from __future__ import annotations

import argparse
import ast
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
ARCHIVOS_DEL_REPO = ("conftest.py",)


def _git(*args: str, cwd: Path = RAIZ) -> str:
    salida = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    if salida.returncode:
        raise RuntimeError(
            f"git {' '.join(args)} falló: {salida.stderr.strip() or salida.stdout.strip()}"
        )
    return salida.stdout


def _ref_de_base(base: str) -> str:
    if subprocess.run(
        ["git", "rev-parse", "--verify", base], cwd=RAIZ, capture_output=True
    ).returncode == 0:
        return base
    nombre = base.split("/")[-1]
    _git("fetch", "--quiet", "origin", f"+refs/heads/{nombre}:refs/remotes/{base}")
    return base


def _archivos_cambiados(base: str) -> list[str]:
    salida = _git("diff", "--name-only", "--diff-filter=AM", f"{base}...HEAD")
    archivos = []
    for ruta in salida.splitlines():
        if ruta in ARCHIVOS_DEL_REPO or (
            ruta.startswith("tests/") and ruta.endswith(".py")
        ):
            archivos.append(ruta)
    return archivos


def _tests(texto: str) -> dict[str, tuple[str, str]]:
    """nombre -> (fuente, linea del def); incluye métodos de clases."""
    arbol = ast.parse(texto)
    salida: dict[str, tuple[str, str]] = {}
    lineas = texto.splitlines()

    def agrega(nombre: str, nodo: ast.FunctionDef) -> None:
        fuente = ast.get_source_segment(texto, nodo) or ""
        linea = lineas[nodo.lineno - 1] if nodo.lineno <= len(lineas) else ""
        salida[nombre] = (fuente, linea)

    for nodo in arbol.body:
        if isinstance(nodo, ast.FunctionDef) and nodo.name.startswith("test_"):
            agrega(nodo.name, nodo)
        elif isinstance(nodo, ast.ClassDef):
            for hijo in nodo.body:
                if isinstance(hijo, ast.FunctionDef) and hijo.name.startswith("test_"):
                    agrega(f"{nodo.name}::{hijo.name}", hijo)
    return salida


def _texto_en_base(base: str, ruta: str) -> str:
    salida = subprocess.run(
        ["git", "show", f"{base}:{ruta}"],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=False,
    )
    return salida.stdout if salida.returncode == 0 else ""


def _nuevas_o_cambiadas(
    base: str,
) -> tuple[list[str], dict[str, list[str]], dict[str, str]]:
    cambios: dict[str, list[str]] = {}
    excepciones: dict[str, str] = {}
    copiar = _archivos_cambiados(base)
    for ruta in copiar:
        ruta_abs = RAIZ / ruta
        if not ruta_abs.is_file():
            continue
        actuales = _tests(ruta_abs.read_text(encoding="utf-8"))
        previos = _tests(_texto_en_base(base, ruta))
        tocados = [
            nombre
            for nombre, (fuente, _linea) in actuales.items()
            if nombre not in previos or previos[nombre][0] != fuente
        ]
        for nombre in tocados:
            _fuente, linea = actuales[nombre]
            if "# no-roja-en-base:" in linea:
                razon = linea.split("# no-roja-en-base:", 1)[1].strip()
                excepciones[nombre] = razon or "(sin razón)"
        if tocados:
            cambios[ruta] = tocados
    return copiar, cambios, excepciones

def _marcador(ruta: Path) -> str:
    try:
        return str(ruta.resolve().relative_to(RAIZ))
    except ValueError:
        return str(ruta)


def _corre_en_base(
    base: str, archivos: list[str], nombres: list[str]
) -> tuple[set[str] | None, str]:
    if not archivos or not nombres:
        return set(), ""
    temporal = Path(tempfile.mkdtemp(prefix="pruebas-base-"))
    try:
        worktree = temporal / "base"
        _git("worktree", "add", "--detach", "-q", str(worktree), base)
        for ruta in archivos:
            destino = worktree / ruta
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(RAIZ / ruta, destino)
        filtro = " or ".join(sorted({n.split("::")[-1] for n in nombres}))
        reporte = temporal / "reporte.xml"
        comando = [
            sys.executable,
            "-m",
            "pytest",
            *[str(worktree / r) for r in archivos],
            "-k",
            filtro,
            f"--junitxml={reporte}",
            "--continue-on-collection-errors",
            "-q",
            "-p",
            "no:cacheprovider",
        ]
        corrida = subprocess.run(
            comando,
            cwd=worktree,
            capture_output=True,
            text=True,
            check=False,
        )
        if not reporte.is_file():
            return None, corrida.stdout + corrida.stderr
        pasaron: set[str] = set()
        for caso in ET.parse(reporte).iter("testcase"):
            if caso.find("failure") is not None:
                continue
            if caso.find("error") is not None:
                continue
            if caso.find("skipped") is not None:
                continue
            nombre = (caso.get("name") or "").split("[")[0].split(".")[-1]
            pasaron.add(nombre)
        return pasaron, corrida.stdout + corrida.stderr
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(temporal / "base")],
            cwd=RAIZ,
            capture_output=True,
        )
        shutil.rmtree(temporal, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pruebas-nuevas")
    ap.add_argument("--base", default="origin/main")
    args = ap.parse_args(argv)
    try:
        base = _ref_de_base(args.base)
        copiar, cambios, excepciones = _nuevas_o_cambiadas(base)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for nombre, razon in sorted(excepciones.items()):
        print(f"excepción {nombre}: {razon}")
    if not cambios:
        print("sin pruebas nuevas o cambiadas")
        return 0
    nombres = sorted({n for lista in cambios.values() for n in lista})
    seleccionadas = [n for n in nombres if n not in excepciones]
    archivos = copiar
    if not seleccionadas:
        print(f"{len(nombres)} prueba(s) nueva(s) exenta(s); no hay nada que correr")
        return 0
    pasaron, salida = _corre_en_base(base, archivos, seleccionadas)
    if pasaron is None:
        print(
            "no se pudo correr la base (¿falta pytest?):\n" + salida[-2000:],
            file=sys.stderr,
        )
        return 2
    exceptuadas = {n.split("::")[-1] for n in excepciones}
    culpables = sorted(n for n in pasaron if n not in exceptuadas)
    if culpables:
        print(
            "estas pruebas nuevas pasan en la base: no demuestran el cambio. "
            "Marca una excepción con `# no-roja-en-base: <razón>` o hazla fallar sin el arreglo:",
            file=sys.stderr,
        )
        for nombre in culpables:
            print(f"  {nombre}", file=sys.stderr)
        return 1
    print(f"{len(nombres)} prueba(s) nueva(s) revisada(s); ninguna pasa en la base")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
