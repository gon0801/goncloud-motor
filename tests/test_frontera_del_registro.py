"""Solo la frontera convierte un rechazo del registro en una respuesta.

`Registro.anotar` lanza el rechazo y, en un paso del barrido, detiene el
encargo por fallo interno. `Registro.anotar_en_frontera` lo devuelve como
`ajena` o `ilegal` a quien pidió. Una función nueva que lo use entra aquí con
su razón; si no, el rechazo que devuelve es un hecho que se calla.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from motor.encargos.registro import Registro

RAIZ = Path(__file__).resolve().parents[1]

FRONTERA = {
    ("motor.encargos.ciclo", "_ordenar"): "la orden del dueño se rechaza con su motivo",
    ("motor.encargos.ciclo", "corregir"): "la corrección del dueño se rechaza o choca",
    ("motor.encargos.ciclo", "corregir.rechazar"): "la constancia de un rechazo ya dicho",
    ("motor.encargos.ciclo", "conceder_conductor"): "la autoridad pide el conductor",
    ("motor.encargos.ciclo", "observar_intento"): "la autoridad informa un intento",
    ("motor.encargos.ciclo", "recibir_resultado"): "la autoridad entrega un resultado",
    ("motor.encargos.ciclo", "_registrar_fase_desplegada"): "el recibo externo del despliegue",
    ("motor.encargos.ciclo", "_lanzar_uno_en_turno"): "sin reserva no se lanza el turno",
    ("motor.encargos.arranque", "ejecutar"): "el lanzador abre el intento que le dieron",
    ("motor.encargos.fases", "adoptar"): "la propuesta de fases del agente",
    ("motor.encargos.frontera", "_conceder"): "el pedido de un recurso",
    ("motor.encargos.frontera", "_registrar_efecto"): "el pedido de un efecto",
    ("motor.encargos.necesidades", "_atender_decision"): "la decisión que pide el agente",
    ("motor.encargos.necesidades", "_registrar_solicitud"): "la solicitud del buzón",
    ("motor.encargos.necesidades", "atender"): "la solicitud tardía del buzón",
    ("motor.encargos.necesidades", "responder"): "la respuesta del dueño",
    ("motor.integraciones.directores", "_anotar_resultado_local"): "el resultado del director",
    ("motor.integraciones.directores", "_aplicar_promover_local"): "el director pasa a durable",
}

# Un `except Exception`, `BaseException` o desnudo que no re-lanza toma el rechazo
# de un `anotar` de su `try` por otro error y lo calla. Va antes `except Rechazo: raise`.
CALLA_A_PROPOSITO = {
    ("motor.encargos.contencion", "contener"): (
        "es el último recurso del barrido: si tampoco anota el fallo, lo dice por "
        "stderr y atiende los demás encargos"
    ),
}


def _llamadores() -> set[tuple[str, str]]:
    """Cada función de `motor/` que llama a `anotar_en_frontera`, por la más interna."""
    encontrados: set[tuple[str, str]] = set()

    def recorrer(nodo: ast.AST, modulo: str, funcion: str) -> None:
        for hijo in ast.iter_child_nodes(nodo):
            if isinstance(hijo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                recorrer(hijo, modulo, f"{funcion}.{hijo.name}" if funcion else hijo.name)
                continue
            if (
                isinstance(hijo, ast.Call)
                and isinstance(hijo.func, ast.Attribute)
                and hijo.func.attr == "anotar_en_frontera"
            ):
                encontrados.add((modulo, funcion))
            recorrer(hijo, modulo, funcion)

    for ruta in sorted((RAIZ / "motor").rglob("*.py")):
        modulo = ".".join(ruta.relative_to(RAIZ).with_suffix("").parts)
        recorrer(ast.parse(ruta.read_text(encoding="utf-8")), modulo, "")
    return encontrados


def _calla_el_rechazo(intento: ast.Try) -> bool:
    """El primer manejador que atraparía un `Rechazo` es amplio y no re-lanza."""
    for manejador in intento.handlers:
        tipos = manejador.type.elts if isinstance(manejador.type, ast.Tuple) else [manejador.type]
        nombres = {t.id if isinstance(t, ast.Name) else "" for t in tipos if t is not None}
        if "Rechazo" in nombres:
            return False
        if manejador.type is None or nombres & {"Exception", "BaseException"}:
            final = manejador.body[-1]
            return not (isinstance(final, ast.Raise) and final.exc is None)
    return False


def _anotar_callado() -> set[tuple[str, str]]:
    """Cada función de `motor/` con un `anotar` dentro de un `try` que calla su rechazo."""
    encontrados: set[tuple[str, str]] = set()

    def visitar(nodo: ast.AST, modulo: str, funcion: str, callado: bool) -> None:
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcion, callado = f"{funcion}.{nodo.name}" if funcion else nodo.name, False
        elif isinstance(nodo, ast.Lambda):
            callado = False
        elif isinstance(nodo, ast.Try):
            for hijo in nodo.body:
                visitar(hijo, modulo, funcion, callado or _calla_el_rechazo(nodo))
            for hijo in (*nodo.handlers, *nodo.orelse, *nodo.finalbody):
                visitar(hijo, modulo, funcion, callado)
            return
        elif (
            callado
            and isinstance(nodo, ast.Call)
            and isinstance(nodo.func, ast.Attribute)
            and nodo.func.attr == "anotar"
        ):
            encontrados.add((modulo, funcion))
        for hijo in ast.iter_child_nodes(nodo):
            visitar(hijo, modulo, funcion, callado)

    for ruta in sorted((RAIZ / "motor").rglob("*.py")):
        modulo = ".".join(ruta.relative_to(RAIZ).with_suffix("").parts)
        visitar(ast.parse(ruta.read_text(encoding="utf-8")), modulo, "", False)
    return encontrados


class FronteraDelRegistro(unittest.TestCase):
    def test_solo_la_frontera_devuelve_el_rechazo_como_respuesta(self):
        self.assertEqual(_llamadores(), set(FRONTERA))

    def test_ningun_except_amplio_calla_el_rechazo_de_anotar(self):
        self.assertEqual(_anotar_callado(), set(CALLA_A_PROPOSITO))

    def test_no_queda_el_append_que_callaba_el_rechazo(self):
        self.assertFalse(hasattr(Registro, "append"))


if __name__ == "__main__":
    unittest.main()
