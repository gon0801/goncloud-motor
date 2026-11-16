"""Migra escrituras planas de Encargo sin reformatear otros `replace`."""

from __future__ import annotations

import ast
import argparse
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
MOVIDOS = {
    "estado", "orden_parada", "reanuda_en", "intento", "proc", "reserva",
    "intento_interrumpido", "cierre_forzado_fase", "cierre_forzado_huella",
}
AJENOS = {"fase", "sol", "h", "self", "contexto"}
PLIEGUES_DEL_CANARIO = {"detenido", "_parcial"}


def _es_encargo(nodo: ast.expr, ruta: Path) -> bool:
    if ruta == RAIZ / "tests/test_canario_encargos.py":
        if isinstance(nodo, ast.Name) and nodo.id in PLIEGUES_DEL_CANARIO:
            return False
        if (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name)
                and nodo.func.id in PLIEGUES_DEL_CANARIO):
            return False
    if isinstance(nodo, ast.Name):
        return nodo.id not in AJENOS
    if isinstance(nodo, ast.Subscript):
        return False
    return True


def _migrar(ruta: Path, comprobar: bool = False) -> int:
    texto = ruta.read_text(encoding="utf-8")
    arbol = ast.parse(texto)
    nombres = {"replace"}
    modulos = {"dataclasses"}
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ImportFrom) and nodo.module == "dataclasses":
            nombres.update(alias.asname or alias.name for alias in nodo.names
                           if alias.name == "replace")
        elif isinstance(nodo, ast.Import):
            modulos.update(alias.asname or alias.name for alias in nodo.names
                           if alias.name == "dataclasses")
    lineas = texto.splitlines(keepends=True)
    offsets = [0]
    for linea in lineas:
        offsets.append(offsets[-1] + len(linea.encode("utf-8")))
    cambios = []
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Call):
            continue
        funcion = nodo.func
        reconocido = (
            isinstance(funcion, ast.Name) and funcion.id in nombres
            or isinstance(funcion, ast.Attribute) and funcion.attr == "replace"
            and isinstance(funcion.value, ast.Name) and funcion.value.id in modulos
        )
        if not reconocido or not nodo.args or not _es_encargo(nodo.args[0], ruta):
            continue
        if not MOVIDOS.intersection(k.arg for k in nodo.keywords):
            continue
        inicio = offsets[funcion.lineno - 1] + funcion.col_offset
        fin = offsets[funcion.end_lineno - 1] + funcion.end_col_offset
        cambios.append((inicio, fin))
    if not cambios:
        return 0
    if comprobar:
        raise RuntimeError(f"faltan {len(cambios)} escrituras por migrar en {ruta}")
    crudo = texto.encode("utf-8")
    for inicio, fin in sorted(cambios, reverse=True):
        crudo = crudo[:inicio] + b"evolucionar" + crudo[fin:]
    texto = crudo.decode("utf-8")
    if ruta.name != "dominio.py":
        if "motor/encargos/reductor" in str(ruta):
            importacion = "from ..encargo import evolucionar\n"
        elif "motor/encargos" in str(ruta):
            importacion = "from .encargo import evolucionar\n"
        else:
            importacion = "from motor.encargos.encargo import evolucionar\n"
        lineas = texto.splitlines(keepends=True)
        futuro = next((i for i, linea in enumerate(lineas)
                       if linea.startswith("from __future__ import ")), None)
        if futuro is None:
            primero = arbol.body[0] if arbol.body else None
            if isinstance(primero, ast.Expr) and isinstance(primero.value, ast.Constant):
                lineas.insert(primero.end_lineno, importacion)
            else:
                lineas.insert(0, importacion)
        else:
            lineas.insert(futuro + 1, importacion)
        texto = "".join(lineas)
    ruta.write_text(texto, encoding="utf-8")
    return len(cambios)


def _rutas(raiz: Path = RAIZ):
    for carpeta in ("motor/encargos", "motor/integraciones", "ops", "tools", "tests"):
        for ruta in sorted((raiz / carpeta).rglob("*.py")):
            if ruta.name not in {"encargo.py", "migrar_usos_situacion.py"}:
                yield ruta


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    comprobar = parser.parse_args().check
    for ruta in _rutas():
        cambios = _migrar(ruta, comprobar)
        if cambios:
            print(f"{ruta.relative_to(RAIZ)}: {cambios} escrituras")
