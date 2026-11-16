"""Extrae Encargo sin reescribir las demás funciones de dominio."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
LEGADOS = (
    "estado", "orden_parada", "reanuda_en", "intento", "proc", "reserva",
    "intento_interrumpido", "cierre_forzado_fase", "cierre_forzado_huella",
)


def extraer(comprobar: bool = False) -> None:
    ruta = RAIZ / "motor" / "encargos" / "dominio.py"
    destino = ruta.with_name("encargo.py")
    if destino.exists():
        if comprobar:
            arbol = ast.parse(ruta.read_text(encoding="utf-8"))
            if any(isinstance(n, ast.ClassDef) and n.name == "Encargo" for n in arbol.body):
                raise RuntimeError("Encargo sigue definido en dominio")
            campos = {n.target.id for n in ast.walk(ast.parse(
                destino.read_text(encoding="utf-8"))) if isinstance(n, ast.AnnAssign)
                and isinstance(n.target, ast.Name)}
            if campos.intersection(LEGADOS):
                raise RuntimeError("hay campos legados planos en Encargo")
            return
        raise RuntimeError(f"el destino ya existe: {destino}")
    texto = ruta.read_text(encoding="utf-8")
    arbol = ast.parse(texto)
    nodo = next(n for n in arbol.body if isinstance(n, ast.ClassDef) and n.name == "Encargo")
    lineas = texto.splitlines(keepends=True)
    inicio = nodo.decorator_list[0].lineno - 1
    cuerpo = lineas[inicio:nodo.end_lineno]
    for indice, linea in enumerate(cuerpo):
        if linea.startswith("    ") and not linea.startswith("        "):
            nombre = linea.strip().split(":", 1)[0]
            if nombre in LEGADOS:
                cuerpo[indice] = ""
    extraido = "".join(cuerpo)
    for nombre_clase, fabrica in (
        ("EntregaAutorizada", "_por_defecto_entrega"),
        ("LecturaDeepSeek", "_por_defecto_lectura"),
        ("MarcasDeepSeekCerrado", "_por_defecto_cerrado"),
        ("SondeoCI", "_por_defecto_sondeo"),
    ):
        extraido = extraido.replace(f"default_factory={nombre_clase}", f"default_factory={fabrica}")
    extraido = extraido.replace(
        "    procedimiento: tuple[str, ...] = ()\n",
        "    procedimiento: tuple[str, ...] = ()\n"
        "    situacion: Situacion = field(default_factory=_por_defecto_situacion)\n",
    )
    extraido = extraido.replace(
        "    def nivel_alcanzado(self) -> str:\n        return nivel_de(self.fase_actual)",
        "    def nivel_alcanzado(self) -> str:\n"
        "        from .dominio import nivel_de\n\n        return nivel_de(self.fase_actual)",
    )
    if "situacion: Situacion" not in extraido:
        raise RuntimeError("no se encontró el punto de inserción de situacion")
    cabecera = '''"""Encargo: datos persistentes y situación tipada en un único dueño."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .dominio import (
        Decision, Efecto, EntregaAutorizada, Fase, Impedimento, LecturaDeepSeek,
        MarcasDeepSeekCerrado, PausaAgente, Proc, RefRevision, Solicitud,
        SondeoCI, VersionProducto,
    )
    from .situacion import Situacion


def _por_defecto_entrega() -> EntregaAutorizada:
    from .dominio import EntregaAutorizada

    return EntregaAutorizada()


def _por_defecto_lectura() -> LecturaDeepSeek:
    from .dominio import LecturaDeepSeek

    return LecturaDeepSeek()


def _por_defecto_cerrado() -> MarcasDeepSeekCerrado:
    from .dominio import MarcasDeepSeekCerrado

    return MarcasDeepSeekCerrado()


def _por_defecto_sondeo() -> SondeoCI:
    from .dominio import SondeoCI

    return SondeoCI()


def _por_defecto_situacion() -> Situacion:
    from .situacion import Recibido

    return Recibido()


CAMPOS_LEGADOS = (
    "estado", "orden_parada", "reanuda_en", "intento", "proc", "reserva",
    "intento_interrumpido", "cierre_forzado_fase", "cierre_forzado_huella",
)


'''
    importacion = "from .catalogo_agentes import identidad_modelo\n"
    if texto.count(importacion) != 1:
        raise RuntimeError("no se encontró la lista de imports de dominio")
    dominio = "".join(lineas[:inicio] + lineas[nodo.end_lineno:])
    dominio = dominio.replace(
        importacion, importacion + "from .encargo import CAMPOS_LEGADOS, Encargo, evolucionar\n",
        1,
    )
    destino.write_text(cabecera + extraido + "\n", encoding="utf-8")
    ruta.write_text(dominio, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    extraer(parser.parse_args().check)
