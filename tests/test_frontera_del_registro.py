"""Solo la frontera convierte un rechazo del registro en una respuesta.

`Registro.anotar` lanza el rechazo y, en un paso del barrido, detiene el
encargo por fallo interno. `Registro.anotar_en_frontera` lo devuelve como
`ajena` o `ilegal` a quien pidió. Una función nueva que lo use entra aquí con
su razón; si no, el rechazo que devuelve es un hecho que se calla.
"""

from __future__ import annotations

import ast
import functools
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
    ("motor.encargos.ciclo", "barrer"): (
        "es la contención del barrido: pasa el rechazo de un encargo a `contener`, "
        "que lo detiene por fallo interno"
    ),
    ("motor.encargos.ciclo", "migrar_marcadores"): (
        "el marcador viejo se borra solo después de guardar su evento: si el registro "
        "lo rechaza, el hecho sigue en disco, la vuelta siguiente lo reintenta sin "
        "gastar más que leer el disco y stderr lo dice una vez"
    ),
    ("motor.encargos.ciclo", "_barrer_encargos"): (
        "la autoridad de un encargo cerrado: un cerrado no se detiene, el rechazo queda "
        "en una nota `procedimiento` con una clave por clase de error, y la limpieza "
        "del cierre sigue"
    ),
}
# Llamadas a ayudantes del mismo módulo que la guarda sigue hasta llegar a `anotar`.
PROFUNDIDAD = 3


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
    """Cada función de `motor/` con un `anotar` dentro de un `try` que calla su rechazo,
    escrito ahí o en un ayudante del mismo módulo hasta `PROFUNDIDAD` llamadas."""
    encontrados: set[tuple[str, str]] = set()

    def visitar(nodo: ast.AST, modulo: str, funcion: str, callado: bool, anota) -> None:
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcion, callado = f"{funcion}.{nodo.name}" if funcion else nodo.name, False
        elif isinstance(nodo, ast.Lambda):
            callado = False
        elif isinstance(nodo, ast.Try):
            for hijo in nodo.body:
                visitar(hijo, modulo, funcion, callado or _calla_el_rechazo(nodo), anota)
            for hijo in (*nodo.handlers, *nodo.orelse, *nodo.finalbody):
                visitar(hijo, modulo, funcion, callado, anota)
            return
        elif callado and anota(nodo, PROFUNDIDAD):
            encontrados.add((modulo, funcion))
        for hijo in ast.iter_child_nodes(nodo):
            visitar(hijo, modulo, funcion, callado, anota)

    for ruta in sorted((RAIZ / "motor").rglob("*.py")):
        modulo = ".".join(ruta.relative_to(RAIZ).with_suffix("").parts)
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        visitar(arbol, modulo, "", False, _anota_en(arbol))
    return encontrados


def _anota_en(arbol: ast.Module):
    """`anota(llamada, profundidad)`: la llamada es un `anotar`, o una función del
    módulo cuyo cuerpo llega a uno fuera de un `try` que ya lo calla."""
    funciones = {
        n.name: n for n in arbol.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def lanza(nodo: ast.AST, profundidad: int) -> bool:
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return False
        if isinstance(nodo, ast.Try) and _calla_el_rechazo(nodo):
            hijos = [*nodo.handlers, *nodo.orelse, *nodo.finalbody]
        else:
            hijos = list(ast.iter_child_nodes(nodo))
        return anota(nodo, profundidad) or any(lanza(h, profundidad) for h in hijos)

    @functools.cache
    def ayudante_lanza(nombre: str, profundidad: int) -> bool:
        return any(lanza(s, profundidad) for s in funciones[nombre].body)

    def anota(nodo: ast.AST, profundidad: int) -> bool:
        if not isinstance(nodo, ast.Call):
            return False
        if isinstance(nodo.func, ast.Attribute):
            return nodo.func.attr == "anotar"
        return (
            profundidad > 0
            and isinstance(nodo.func, ast.Name)
            and nodo.func.id in funciones
            and ayudante_lanza(nodo.func.id, profundidad - 1)
        )

    return anota


class FronteraDelRegistro(unittest.TestCase):
    def test_solo_la_frontera_devuelve_el_rechazo_como_respuesta(self):
        self.assertEqual(_llamadores(), set(FRONTERA))

    def test_ningun_except_amplio_calla_el_rechazo_de_anotar(self):
        self.assertEqual(_anotar_callado(), set(CALLA_A_PROPOSITO))

    def test_no_queda_el_append_que_callaba_el_rechazo(self):
        self.assertFalse(hasattr(Registro, "append"))


if __name__ == "__main__":
    unittest.main()
