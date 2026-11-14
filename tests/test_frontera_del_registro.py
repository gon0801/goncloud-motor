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


class FronteraDelRegistro(unittest.TestCase):
    def test_solo_la_frontera_devuelve_el_rechazo_como_respuesta(self):
        self.assertEqual(_llamadores(), set(FRONTERA))

    def test_no_queda_el_append_que_callaba_el_rechazo(self):
        self.assertFalse(hasattr(Registro, "append"))


if __name__ == "__main__":
    unittest.main()
