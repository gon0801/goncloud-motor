"""Líneas del reductor que no ejercita la reproducción del corpus."""

from __future__ import annotations

import dis
import linecache
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import CodeType


def _codigos(codigo: CodeType) -> Iterator[CodeType]:
    yield codigo
    for valor in codigo.co_consts:
        if isinstance(valor, CodeType):
            yield from _codigos(valor)


def medir(raiz: Path, ejecutar: Callable[[], int]) -> int:
    from tools.plegado import cargar_reductor

    cargar_reductor(raiz)
    from motor.encargos import dominio

    funciones = [dominio._reducir_evento]
    if (raiz / "motor" / "encargos" / "reductor").is_dir():
        from motor.encargos.reductor import TABLA

        funciones.extend(fila.aplicar for fila in TABLA.values())
    codigos = {codigo for funcion in funciones for codigo in _codigos(funcion.__code__)}
    rutas = {codigo: Path(codigo.co_filename).resolve().relative_to(raiz.resolve()).as_posix()
             for codigo in codigos}
    posibles = {
        (rutas[codigo], linea)
        for codigo in codigos for _, linea in dis.findlinestarts(codigo)
        if linea != codigo.co_firstlineno or codigo.co_name.startswith("<")
    }
    visitadas: set[tuple[str, int]] = set()

    def trazar(frame, evento, _arg):
        if evento == "call":
            return trazar if frame.f_code in codigos else None
        if evento == "line":
            visitadas.add((rutas[frame.f_code], frame.f_lineno))
        return trazar

    anterior = sys.gettrace()
    sys.settrace(trazar)
    try:
        resultado = ejecutar()
    finally:
        sys.settrace(anterior)
    faltan = sorted(posibles - visitadas)
    print(f"cobertura del reductor: {len(posibles) - len(faltan)} de {len(posibles)} "
          f"líneas ejecutadas; {len(faltan)} sin ejecutar")
    for archivo, linea in faltan:
        texto = linecache.getline(str(raiz / archivo), linea).strip()
        print(f"{archivo}:{linea}: {texto}")
    return resultado
