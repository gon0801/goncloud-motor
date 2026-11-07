"""Candados de calidad con línea base: frenan lo nuevo, no lo que ya estaba.

Uso, igual en local y en CI:

    python3 tools/calidad.py tipos
    python3 tools/calidad.py lint
    python3 tools/calidad.py io

Cada modo compara contra `.github/calidad/baseline.json` y falla solo si el
cambio agrega un error. `--actualizar` regenera la línea base (se hace desde la
rama base, no desde el PR que se está revisando).
"""

from __future__ import annotations

import argparse
import ast
import collections
import json
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
BASE = RAIZ / ".github" / "calidad" / "baseline.json"
IO_PERMITIDO = RAIZ / "motor" / "io.py"
MENSAJE_IO = "usa motor.io.leer_json / escribir_texto"


VERSIONES = {"pyright": "1.1.414", "ruff": "0.17.4"}


def _ejecutable(nombre: str, modulo: str) -> list[str]:
    ruta = shutil.which(nombre)
    if ruta:
        return [ruta]
    if shutil.which("uv"):
        return ["uv", "tool", "run", "--from", f"{nombre}=={VERSIONES[nombre]}", nombre]
    return [sys.executable, "-m", modulo]


def _relativa(ruta: str) -> str:
    try:
        return str(Path(ruta).resolve().relative_to(RAIZ))
    except ValueError:
        return ruta


def diagnosticos_tipos() -> collections.Counter:
    salida = subprocess.run(
        [*_ejecutable("pyright", "pyright"), "--outputjson"],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        datos = json.loads(salida.stdout or "{}")
    except ValueError as exc:
        raise SystemExit(f"pyright no devolvió JSON: {exc}\n{salida.stderr[-2000:]}")
    cuenta: collections.Counter = collections.Counter()
    for d in datos.get("generalDiagnostics") or []:
        if d.get("severity") != "error":
            continue
        cuenta[
            (
                _relativa(str(d.get("file") or "")),
                str(d.get("rule") or ""),
                str(d.get("message") or ""),
            )
        ] += 1
    return cuenta


def diagnosticos_lint() -> collections.Counter:
    salida = subprocess.run(
        [*_ejecutable("ruff", "ruff"), "check", ".", "--output-format", "json"],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        datos = json.loads(salida.stdout or "[]")
    except ValueError as exc:
        raise SystemExit(f"ruff no devolvió JSON: {exc}\n{salida.stderr[-2000:]}")
    cuenta: collections.Counter = collections.Counter()
    for d in datos:
        cuenta[
            (
                _relativa(str(d.get("filename") or "")),
                str(d.get("code") or ""),
                str(d.get("message") or ""),
            )
        ] += 1
    return cuenta


def _llamadas_io(arbol: ast.AST) -> list[tuple[str, int]]:
    encontradas = []
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Call):
            continue
        funcion = nodo.func
        if isinstance(funcion, ast.Attribute) and funcion.attr in (
            "read_text",
            "write_text",
        ):
            encontradas.append((funcion.attr, nodo.lineno))
        if (
            isinstance(funcion, ast.Attribute)
            and isinstance(funcion.value, ast.Name)
            and funcion.value.id == "json"
            and funcion.attr == "loads"
            and any(
                isinstance(x, ast.Call)
                and isinstance(x.func, ast.Attribute)
                and x.func.attr == "read_text"
                for x in ast.walk(nodo)
            )
        ):
            encontradas.append(("json.loads(read_text)", nodo.lineno))
    return encontradas


def usos_io() -> collections.Counter:
    cuenta: collections.Counter = collections.Counter()
    for archivo in sorted((RAIZ / "motor").rglob("*.py")):
        if archivo.resolve() == IO_PERMITIDO.resolve():
            continue
        try:
            arbol = ast.parse(archivo.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            raise SystemExit(f"no se puede leer {archivo}: {exc}")
        for patron, _linea in _llamadas_io(arbol):
            cuenta[(str(archivo.relative_to(RAIZ)), patron)] += 1
    return cuenta


def _cargar() -> dict:
    if not BASE.is_file():
        return {}
    return json.loads(BASE.read_text(encoding="utf-8"))


def _lista(cuenta: collections.Counter) -> list[list[str]]:
    return sorted([list(clave), str(n)] for clave, n in cuenta.items())


def _cuenta(lista: list[list[str]]) -> collections.Counter:
    return collections.Counter({tuple(clave): int(n) for clave, n in lista})


def _comparar(cuenta: collections.Counter, linea_base: dict, modo: str) -> int:
    cuenta = collections.Counter(cuenta)
    previos = _cuenta(linea_base.get(modo) or [])
    nuevos = cuenta - previos
    if not nuevos:
        print(f"{modo}: sin errores nuevos (hoy: {sum(cuenta.values())}; base: {sum(previos.values())})")
        return 0
    print(f"{modo}: {sum(nuevos.values())} error(es) nuevo(s)", file=sys.stderr)
    for clave, n in sorted(nuevos.items()):
        for _ in range(n):
            if modo == "io":
                print(f"  {clave[0]}: {clave[1]}; {MENSAJE_IO}", file=sys.stderr)
            else:
                print(f"  {clave[0]}: {clave[1]}: {clave[2]}", file=sys.stderr)
    return 1


def _actualizar(linea_base: dict) -> None:
    linea_base["tipos"] = _lista(diagnosticos_tipos())
    linea_base["lint"] = _lista(diagnosticos_lint())
    linea_base["io"] = _lista(usos_io())
    BASE.parent.mkdir(parents=True, exist_ok=True)
    BASE.write_text(json.dumps(linea_base, ensure_ascii=False, indent=1) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="calidad")
    ap.add_argument("modo", choices=("tipos", "lint", "io"))
    ap.add_argument("--actualizar", action="store_true")
    args = ap.parse_args(argv)
    linea_base = _cargar()
    if args.actualizar:
        _actualizar(linea_base)
        return 0
    if args.modo == "tipos":
        return _comparar(diagnosticos_tipos(), linea_base, "tipos")
    if args.modo == "lint":
        return _comparar(diagnosticos_lint(), linea_base, "lint")
    return _comparar(usos_io(), linea_base, "io")


if __name__ == "__main__":
    raise SystemExit(main())
