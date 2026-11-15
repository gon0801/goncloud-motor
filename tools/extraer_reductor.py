"""Extrae las ramas que quedan en dominio sin reescribir sus cuerpos."""

from __future__ import annotations

import ast
import builtins
import sys
from collections import defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DOMINIO = RAIZ / "motor" / "encargos" / "dominio.py"
REDUCTOR = RAIZ / "motor" / "encargos" / "reductor"

TEMAS = {
    "intento": """
        reserva reserva_cerrada conductor intento_abierto actividad colgada
        intento_cerrado sesion_perdida conductor_fase
    """,
    "fase": """
        fases nivel_efectivo politica_despliegue producto_listo ci_pr
        entrega_externa entrega_bloqueada lectura_fallida fase_reabierta
        entrada_incorporada cierre_forzado bloqueo_fase fase_reactivada
        conciliacion_fase fase_desplegada resultado_directo
    """,
    "revision": """
        observacion_supervisor comprobaciones_supervisor revision
        deepseek_lectura deepseek_cerrado ci_sondeo nota_pr
        hallazgo_fuera_de_alcance entrega_devuelta inspeccion_solicitud
        inspeccion_entregada resolucion respuesta
    """,
    "necesidades": """
        instrumento_fallo impedimento impedimento_resuelto
        instrumento_no_disponible solicitud herramienta pregunta quietud
        estrategia autoridad via autorizacion decision solicitud_tardia
    """,
    "esperas": """
        incorporado quietud_vigilada sin_progreso pendiente_entrega
        comprobacion afirmacion efecto plazo espera excepcion
    """,
}
GRUPOS = {kind: tema for tema, lista in TEMAS.items() for kind in lista.split()}


def _leer(ruta: Path) -> str:
    with ruta.open(encoding="utf-8") as archivo:
        return archivo.read()


def _escribir(ruta: Path, texto: str) -> None:
    with ruta.open("w", encoding="utf-8") as archivo:
        archivo.write(texto)


def _nombres_importados(arbol: ast.Module) -> set[str]:
    nombres: set[str] = set()
    for n in arbol.body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            nombres.add(n.name)
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            for x in ast.walk(n):
                if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store):
                    nombres.add(x.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            nombres.update(x.asname or x.name.split(".")[0] for x in n.names)
    return nombres


def _referencias(nodos: list[ast.stmt], globales: set[str]) -> list[str]:
    nombres = {x.id for n in nodos for x in ast.walk(n)
               if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)}
    locales = {x.id for n in nodos for x in ast.walk(n)
               if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store)}
    return sorted((nombres - locales - set(dir(builtins)) - {"e", "d", "hora", "nuevo"})
                  & globales)


def _kind(n: ast.If) -> str:
    if (isinstance(n.test, ast.Compare) and isinstance(n.test.left, ast.Name)
            and n.test.left.id == "kind" and len(n.test.ops) == 1
            and isinstance(n.test.ops[0], ast.Eq)
            and isinstance(n.test.comparators[0], ast.Constant)):
        return str(n.test.comparators[0].value)
    if (isinstance(n.test, ast.Compare) and isinstance(n.test.left, ast.Name)
            and n.test.left.id == "kind" and isinstance(n.test.ops[0], ast.In)):
        return ast.unparse(n.test.comparators[0])
    raise ValueError(f"rama sin kind simple en {n.lineno}")


def extraer() -> None:
    fuente = _leer(DOMINIO)
    lineas = fuente.splitlines(keepends=True)
    arbol = ast.parse(fuente)
    funcion = next(n for n in arbol.body
                   if isinstance(n, ast.FunctionDef) and n.name == "_reducir_evento")
    condiciones = [n for n in funcion.body if isinstance(n, ast.If)
                   and "kind" in ast.unparse(n.test)]
    primera_rama = next((n for n in condiciones if _kind(n) == "reserva"), None)
    ramas = [n for n in condiciones if primera_rama is not None
             and n.lineno >= primera_rama.lineno]
    if not ramas or _kind(ramas[0]) != "reserva":
        raise ValueError("la fuente no tiene las ramas del paso 3.1 sin extraer")

    sys.path.insert(0, str(RAIZ))
    from motor.encargos.detencion import DETENCIONES

    generados: dict[str, list[str]] = defaultdict(list)
    filas: dict[str, str] = {}
    globales = _nombres_importados(arbol)
    for n in ramas:
        kind = _kind(n)
        if kind == "DETENCIONES":
            for detenido in sorted(DETENCIONES):
                filas[detenido] = f"detencion.{detenido}"
            continue
        if kind.startswith("("):
            retirados = ast.literal_eval(kind)
            cabecera = next(i for i in range(n.lineno, n.body[0].lineno)
                            if lineas[i].strip() == "):")
            generados["retirados"].append(
                "def registrar(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:\n"
                "    from ..dominio import replace\n\n"
                + "".join(linea[4:] if linea.startswith("    ") else linea
                          for linea in lineas[cabecera + 1:n.end_lineno]).rstrip() + "\n\n"
            )
            for retirado in retirados:
                filas[retirado] = "retirados.registrar"
            continue
        tema = GRUPOS[kind]
        imports = _referencias(n.body, globales)
        if tema == "intento" and "socket" in imports:
            imports.remove("socket")
        if kind == "cierre_forzado":
            imports.insert(0, "Fase")
        cuerpo = "".join(linea[4:] if linea.startswith("    ") else linea
                         for linea in lineas[n.lineno:n.end_lineno]).rstrip()
        if kind == "cierre_forzado":
            cuerpo = cuerpo.replace("json.dumps([e.id, fase.id, hora, evidencia, e.reaperturas])",
                                    "json.dumps([e.id, cast(Fase, fase).id, hora, evidencia, e.reaperturas])")
        if kind == "resultado_directo":
            cuerpo = cuerpo.replace("    if fase is not None:\n        fases = tuple(",
                                    "    if fase is not None:\n        fase_id = fase.id\n        fases = tuple(")
            cuerpo = cuerpo.replace("if f.id == fase.id\n", "if f.id == fase_id\n")
        if "kind" in {x.id for cuerpo_n in n.body for x in ast.walk(cuerpo_n)
                      if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)}:
            raise ValueError(f"el cuerpo de {kind} todavía usa kind")
        generados[tema].append(
            f"def {kind}(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:\n"
            + (f"    from ..dominio import {', '.join(imports)}\n\n" if imports else "")
            + cuerpo + "\n\n"
        )
        filas[kind] = f"{tema}.{kind}"
    if len(filas) != 71:
        raise ValueError(f"se esperaban 71 filas restantes, hay {len(filas)}")

    inicio = (
        '"""Manejadores del reductor extraídos de dominio."""\n\n'
        "from __future__ import annotations\n\n"
        "from typing import TYPE_CHECKING\n\n"
        "if TYPE_CHECKING:\n"
        "    from ..dominio import Encargo\n\n\n"
    )
    for tema, cuerpos in generados.items():
        cabecera_tema = inicio
        if tema == "fase":
            cabecera_tema = inicio.replace("TYPE_CHECKING\n", "TYPE_CHECKING, cast\n", 1)
        if tema == "intento":
            cabecera_tema = inicio.replace(
                "from typing import TYPE_CHECKING", "import socket\nfrom typing import TYPE_CHECKING",
            )
        _escribir(REDUCTOR / f"{tema}.py",
            cabecera_tema + "".join(cuerpos).rstrip() + "\n",
        )
    _escribir(REDUCTOR / "detencion.py",
        inicio + "".join(
            f"def {kind}(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:\n"
            f"    from ..detencion import DETENCIONES\n\n"
            f"    return DETENCIONES[{kind!r}](e, d, hora)\n\n"
            for kind in sorted(DETENCIONES)
        ).rstrip() + "\n",
    )
    tabla = (REDUCTOR / "__init__.py")
    texto = _leer(tabla).replace(
        "from . import pedido", "from . import " + ", ".join(sorted(generados | {"detencion": []}) + ["pedido"]),
    )
    posicion = texto.rfind("}")
    if posicion < 0:
        raise ValueError("TABLA sin cierre")
    nuevas = "".join(f"    {kind!r}: Fila({fila}),\n" for kind, fila in sorted(filas.items()))
    _escribir(tabla, texto[:posicion] + nuevas + texto[posicion:])
    primera = ramas[0].lineno - 1
    ultima = ramas[-1].end_lineno
    despachador = (
        "    fila = TABLA.get(kind)\n"
        "    if fila is None:\n"
        "        raise TransicionIlegal(f\"evento desconocido: {kind}\")\n"
        "    return fila.aplicar(e, d, hora, nuevo)\n"
    )
    cabecera = "".join(lineas[:primera])
    cabecera = cabecera.replace(
        "    if kind in TABLA:\n        return TABLA[kind].aplicar(e, d, hora, nuevo)\n", "",
    )
    cabecera = cabecera.replace("import socket\n", "", 1)
    cabecera = cabecera.replace("from .detencion import DETENCIONES\n", "", 1)
    _escribir(DOMINIO, cabecera + despachador + "".join(lineas[ultima + 2:]))


if __name__ == "__main__":
    extraer()
