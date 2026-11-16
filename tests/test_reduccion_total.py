from __future__ import annotations

import unittest
from dataclasses import asdict

from motor.encargos.dominio import reducir
from motor.encargos.reductor import TABLA


def _pedido():
    return reducir(None, {"kind": "entrega", "data": {
        "id": "e", "clave": "e", "huella": "h", "texto": "pedido",
    }}, nuevo=False)


class ReduccionTotal(unittest.TestCase):
    def test_aplicar_nunca_lanza_ni_con_infinity(self) -> None:
        previo = _pedido()
        casos = [(kind, {}) for kind in sorted(TABLA)]
        casos += [
            ("incorporado", {"generacion": float("inf")}),
            ("quietud_vigilada", {"vueltas": float("inf")}),
            ("solicitud", {"id": "s", "fase": "", "intento": "i",
                           "operacion": "leer", "generacion": float("inf")}),
        ]
        for kind, datos in casos:
            with self.subTest(kind=kind, datos=datos):
                reducir(previo, {"kind": kind, "data": datos}, nuevo=False)

    def test_kind_desconocido_no_modifica_nada_al_plegar(self) -> None:
        previo = _pedido()
        resultado = reducir(previo, {"kind": "orden_inventada", "data": {}}, nuevo=False)
        self.assertEqual(asdict(resultado), asdict(previo))
