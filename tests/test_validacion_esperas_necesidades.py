from __future__ import annotations

import unittest
from dataclasses import replace

from motor.encargos.dominio import TransicionIlegal, reducir
from motor.encargos.reductor import TABLA

ESPERAS = set("""
    afirmacion comprobacion efecto espera excepcion incorporado pendiente_entrega
    plazo quietud_vigilada sin_progreso
""".split())
NECESIDADES = set("""
    autoridad autorizacion decision estrategia herramienta impedimento
    impedimento_resuelto instrumento_fallo instrumento_no_disponible pregunta
    quietud solicitud solicitud_tardia via
""".split())


def _pedido():
    return reducir(None, {"kind": "entrega", "data": {
        "id": "p", "clave": "p", "huella": "h", "texto": "pedido",
    }}, nuevo=False)


class ValidacionEsperasNecesidades(unittest.TestCase):
    def test_todos_los_kinds_de_ambos_grupos_aplican_datos_vacios(self) -> None:
        esperados = ESPERAS | NECESIDADES
        self.assertEqual(
            {kind for kind, fila in TABLA.items()
             if fila.aplicar.__module__.rsplit(".", 1)[-1] in {"esperas", "necesidades"}},
            esperados,
        )
        e = _pedido()
        for kind in esperados:
            with self.subTest(kind=kind):
                self.assertIsNotNone(reducir(e, {"kind": kind, "data": {}}, nuevo=False))

    def test_los_mensajes_legados_permanecen_al_escribir(self) -> None:  # no-roja-en-base: fija los raises movidos
        e = _pedido()
        casos = (
            ("incorporado", e, {"generacion": -1},
             "la incorporación necesita una generación"),
            ("incorporado", e, {"generacion": "invalida"},
             "la incorporación necesita una generación"),
            ("quietud_vigilada", e, {"evento": "invalido"},
             "la quietud vigilada necesita números"),
            ("espera", e, {}, "la espera necesita motivo y reanudación"),
            ("comprobacion", e, {"clase": "confirmado"}, "confirmado sin evidencia"),
            ("comprobacion", e, {"clase": "desconocida"},
             "comprobación desconocida: desconocida"),
            ("impedimento", e, {}, "el impedimento necesita clase, causa y condicion"),
            ("impedimento_resuelto", e, {},
             "el impedimento no se resuelve sin clase y evidencia"),
            ("instrumento_no_disponible", e, {},
             "instrumento_no_disponible sin clase"),
            ("quietud", e, {}, "la quietud necesita causa"),
            ("via", e, {"via": "otra"}, "via desconocida"),
            ("via", replace(e, estado="cancelado"), {"via": "directa"},
             "el encargo ya cerró"),
            ("via", replace(e, reserva=True), {"via": "directa"},
             "el ejecutor anterior sigue abierto"),
            ("solicitud", e, {}, "la solicitud necesita id"),
            ("herramienta", e, {}, "la herramienta no tiene solicitud"),
            ("autorizacion", e, {}, "la autorización necesita recurso"),
            ("decision", e, {}, "la decisión necesita id"),
        )
        for kind, previo, datos, mensaje in casos:
            with self.subTest(kind=kind, mensaje=mensaje), self.assertRaises(
                TransicionIlegal
            ) as exc:
                reducir(previo, {"kind": kind, "data": datos}, nuevo=True)
            self.assertEqual(str(exc.exception), mensaje)
