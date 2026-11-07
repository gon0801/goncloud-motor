"""Las pruebas nuevas tienen que salir rojas contra la base del PR.

En un PR toma las funciones `test_` nuevas o cambiadas de `tests/` (y
`conftest.py`), las corre con el código de la rama base en un worktree temporal
y falla si alguna **pasa** allí: una prueba que pasa sin el arreglo no demuestra
el arreglo.

Una prueba puede eximirse con `# no-roja-en-base: <razón>` en su línea `def`.
El job lista esas excepciones. La identidad es archivo, clase y método; una
excepción de una clase no tapa el mismo nombre en otra, y los casos nuevos de
`parametrize` cuentan como cambio de su función.

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


def _fuente(texto: str, nodo: ast.FunctionDef) -> str:
    """La función con sus decoradores: un `parametrize` nuevo es un cambio."""
    lineas = texto.splitlines()
    inicio = min([nodo.lineno, *[d.lineno for d in nodo.decorator_list]])
    return "\n".join(lineas[inicio - 1 : nodo.end_lineno])


def _tests(texto: str) -> dict[str, tuple[str, str]]:
    """identidad (Clase::test o test) -> (fuente con decoradores, línea del def)."""
    arbol = ast.parse(texto)
    salida: dict[str, tuple[str, str]] = {}
    lineas = texto.splitlines()

    def agrega(nombre: str, nodo: ast.FunctionDef) -> None:
        linea = lineas[nodo.lineno - 1] if nodo.lineno <= len(lineas) else ""
        salida[nombre] = (_fuente(texto, nodo), linea)

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
) -> tuple[list[str], dict[str, list[str]], dict[tuple[str, str], str]]:
    cambios: dict[str, list[str]] = {}
    excepciones: dict[tuple[str, str], str] = {}
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
            _fuente_texto, linea = actuales[nombre]
            if "# no-roja-en-base:" in linea:
                razon = linea.split("# no-roja-en-base:", 1)[1].strip()
                excepciones[(ruta, nombre)] = razon or "(sin razón)"
        if tocados:
            cambios[ruta] = tocados
    return copiar, cambios, excepciones


def _modulos(archivos: list[str]) -> list[tuple[str, str]]:
    """(módulo que usa pytest, ruta del archivo), del prefijo más largo al más corto."""
    pares = [
        (ruta[:-3].replace("/", "."), ruta) for ruta in archivos if ruta.endswith(".py")
    ]
    return sorted(pares, key=lambda par: len(par[0]), reverse=True)


def _identidad_del_caso(
    caso: ET.Element, modulos: list[tuple[str, str]]
) -> tuple[str, str] | None:
    classname = caso.get("classname") or ""
    nombre = (caso.get("name") or "").split("[")[0]
    for modulo, ruta in modulos:
        if classname == modulo:
            return ruta, nombre
        if classname.startswith(modulo + "."):
            clase = classname[len(modulo) + 1 :]
            return ruta, f"{clase}::{nombre}"
    return None


def _corre_en_base(
    base: str, seleccion: dict[str, list[str]]
) -> tuple[set[tuple[str, str]] | None, str]:
    if not seleccion:
        return set(), ""
    temporal = Path(tempfile.mkdtemp(prefix="pruebas-base-"))
    try:
        worktree = temporal / "base"
        _git("worktree", "add", "--detach", "-q", str(worktree), base)
        archivos = sorted(seleccion)
        for ruta in archivos:
            destino = worktree / ruta
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(RAIZ / ruta, destino)
        nodos = [
            f"{worktree / ruta}::{identidad}"
            for ruta in archivos
            for identidad in seleccion[ruta]
        ]
        reporte = temporal / "reporte.xml"
        comando = [
            sys.executable,
            "-m",
            "pytest",
            *nodos,
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
        modulos = _modulos(archivos)
        pasaron: set[tuple[str, str]] = set()
        for caso in ET.parse(reporte).iter("testcase"):
            if (
                caso.find("failure") is not None
                or caso.find("error") is not None
                or caso.find("skipped") is not None
            ):
                continue
            identidad = _identidad_del_caso(caso, modulos)
            if identidad is not None:
                pasaron.add(identidad)
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
    for (ruta, nombre), razon in sorted(excepciones.items()):
        print(f"excepción {ruta}::{nombre}: {razon}")
    total = sum(len(lista) for lista in cambios.values())
    if not cambios:
        print("sin pruebas nuevas o cambiadas")
        return 0
    seleccion = {
        ruta: [n for n in nombres if (ruta, n) not in excepciones]
        for ruta, nombres in cambios.items()
    }
    seleccion = {ruta: nombres for ruta, nombres in seleccion.items() if nombres}
    if not seleccion:
        print(f"{total} prueba(s) nueva(s) exenta(s); no hay nada que correr")
        return 0
    pasaron, salida = _corre_en_base(base, seleccion)
    if pasaron is None:
        print(
            "no se pudo correr la base (¿falta pytest?):\n" + salida[-2000:],
            file=sys.stderr,
        )
        return 2
    culpables = sorted(pasaron - set(excepciones))
    if culpables:
        print(
            "estas pruebas nuevas pasan en la base: no demuestran el cambio. "
            "Marca una excepción con `# no-roja-en-base: <razón>` o hazla fallar sin el arreglo:",
            file=sys.stderr,
        )
        for ruta, nombre in culpables:
            print(f"  {ruta}::{nombre}", file=sys.stderr)
        return 1
    print(f"{total} prueba(s) nueva(s) revisada(s); ninguna pasa en la base")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
