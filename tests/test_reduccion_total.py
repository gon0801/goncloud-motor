from __future__ import annotations

import unittest
from dataclasses import asdict, replace

from motor.encargos.dominio import TransicionIlegal, reducir
from motor.encargos.reductor import TABLA


def _pedido():
    return reducir(None, {"kind": "entrega", "data": {
        "id": "e", "clave": "e", "huella": "h", "texto": "pedido",
    }}, nuevo=False)


class ReduccionTotal(unittest.TestCase):
    def test_fallo_interno_historico_no_cambia_un_encargo_cerrado(self) -> None:
        cerrado = replace(_pedido(), estado="terminado")
        dato = {"kind": "fallo_interno", "data": {}}
        self.assertEqual(asdict(reducir(cerrado, dato, nuevo=False)), asdict(cerrado))
        with self.assertRaisesRegex(
            TransicionIlegal, "un encargo cerrado o que ya se detiene no cambia de causa",
        ):
            reducir(cerrado, dato, nuevo=True)

    def test_rechazos_de_pedido_siguen_iguales_al_escribir(self) -> None:  # no-roja-en-base: fija los guardias que se moverán a validar
        previo = _pedido()
        casos = (
            ("contexto_entrega", {}, TransicionIlegal, "el contexto no puede cambiar una base autorizada"),
            ("orden", {"accion": "inventada"}, TransicionIlegal, "orden desconocida: inventada"),
            ("proveedor_fallo", {}, KeyError, "'agente'"),
            ("ruta_agentes", {}, KeyError, "'agentes'"),
            ("procedimiento", {}, KeyError, "'nota'"),
        )
        for kind, datos, tipo, mensaje in casos:
            with self.subTest(kind=kind):
                with self.assertRaises(tipo) as exc:
                    reducir(previo, {"kind": kind, "data": datos}, nuevo=True)
                self.assertEqual(str(exc.exception), mensaje)

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
