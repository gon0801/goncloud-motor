"""Asigna propiedades a las filas sin alterar los manejadores del reductor."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
RUTA = RAIZ / "motor" / "encargos" / "reductor" / "__init__.py"
MARCADORES = frozenset((
    "incorporado", "quietud_vigilada", "deepseek_lectura", "deepseek_cerrado",
    "ci_sondeo", "excepcion", "conciliacion_fase", "pr_observado", "limpieza",
))
ENTRE_REINTENTOS = frozenset((
    "plazo", "espera", "instrumento_fallo", "reserva", "reserva_cerrada",
    "contexto_entrega", "sesion_perdida", "limpio",
))
HECHOS_DE_ENCARGO = frozenset((
    "deepseek_lectura", "deepseek_cerrado", "ci_sondeo", "fases",
    "nivel_efectivo", "observacion_supervisor", "hallazgo_fuera_de_alcance",
    "resultado_directo",
))


def alcance(kind: str, manejador: str) -> str:
    tema = manejador.split(".", 1)[0]
    if kind in {"ajuste", "conductor_fase", "pr_observado", "fase_cancelada",
                "pr_otra_base"} or (
        tema in {"fase", "revision"} and kind not in HECHOS_DE_ENCARGO
    ):
        return "fase"
    if tema == "intento":
        return "intento"
    return "encargo"


def asignar(texto: str) -> str:
    lineas = texto.splitlines(keepends=True)
    arbol = ast.parse(texto)
    tabla = next(
        n.value for n in arbol.body if isinstance(n, ast.AnnAssign)
        and isinstance(n.target, ast.Name) and n.target.id == "TABLA"
    )
    if not isinstance(tabla, ast.Dict) or len(tabla.keys) != 86:
        raise ValueError("se esperaban las 86 filas literales de TABLA")
    vistos: set[str] = set()
    for llave, valor in zip(tabla.keys, tabla.values, strict=True):
        if not isinstance(llave, ast.Constant) or not isinstance(llave.value, str):
            raise ValueError("la fila necesita un kind literal")
        if not isinstance(valor, ast.Call) or len(valor.args) != 1:
            raise ValueError(f"fila mal formada: {llave.value}")
        kind = llave.value
        if kind in vistos:
            raise ValueError(f"kind repetido: {kind}")
        vistos.add(kind)
        manejador = ast.get_source_segment(texto, valor.args[0])
        if manejador is None:
            raise ValueError(f"manejador ilegible: {kind}")
        antigua = lineas[valor.lineno - 1]
        prefijo, sep, _ = antigua.partition(": Fila(")
        if not sep or not antigua.rstrip().endswith("),"):
            raise ValueError(f"fila no aislada en su línea: {kind}")
        opciones = []
        tipo = alcance(kind, manejador)
        if tipo != "encargo":
            opciones.append(f'alcance="{tipo}"')
        if kind in MARCADORES or kind in ENTRE_REINTENTOS:
            opciones.append("corta_series=False")
        if kind in MARCADORES:
            opciones.append("marcador=True")
        for keyword in valor.keywords:
            if keyword.arg == "validar":
                validador = ast.get_source_segment(texto, keyword.value)
                if validador is None:
                    raise ValueError(f"validador ilegible: {kind}")
                opciones.append(f"validar={validador}")
        argumentos = ", " + ", ".join(opciones) if opciones else ""
        lineas[valor.lineno - 1] = f"{prefijo}: Fila({manejador}{argumentos}),\n"
    return "".join(lineas)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    actual = RUTA.read_text(encoding="utf-8")
    esperado = asignar(actual)
    if args.check:
        if esperado != actual:
            raise SystemExit("TABLA no coincide con las propiedades reproducibles")
    else:
        RUTA.write_text(esperado, encoding="utf-8")
    print("86 filas reproducibles")


if __name__ == "__main__":
    main()
