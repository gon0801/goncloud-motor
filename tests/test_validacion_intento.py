from __future__ import annotations

import unittest
from dataclasses import replace

from motor.encargos.dominio import Fase, Proc, TransicionIlegal, reducir
from motor.encargos.reductor import TABLA


def _entrega():
    return reducir(None, {"kind": "entrega", "data": {
        "id": "e", "clave": "e", "huella": "h", "texto": "pedido",
    }}, nuevo=False)


class ValidacionIntento(unittest.TestCase):
    def test_validacion_es_propiedad_de_las_filas_de_intento(self) -> None:
        for kind in ("reserva", "intento_abierto", "conductor_fase",
                     "conductor", "actividad", "colgada"):
            with self.subTest(kind=kind):
                self.assertTrue(callable(TABLA[kind].validar))

    def test_cargas_incompletas_o_invalidas_no_se_escriben(self) -> None:
        e = _entrega()
        casos = (
            ("reserva", {"generacion": "no entero"}),
            ("reserva", {"ajustes": "no entero"}),
            ("intento_abierto", {"id": "i", "pid": "no entero", "inicio": "t"}),
            ("conductor", {}),
            ("actividad", {"observada": True}),
            ("colgada", {}),
        )
        for kind, datos in casos:
            with self.subTest(kind=kind, datos=datos), self.assertRaises(TransicionIlegal):
                reducir(e, {"kind": kind, "data": datos}, nuevo=True)

    def test_los_ocho_rechazos_conservan_estado_datos_y_mensaje(self) -> None:  # no-roja-en-base: mensajes legados
        e = _entrega()
        casos = (
            ("reserva", replace(e, reserva=True), {}, "ya hay un escritor"),
            ("reserva", replace(e, intentos_nuevos=False), {},
             "no hay autorización para otro intento"),
            ("intento_abierto", replace(e, intentos_nuevos=False), {},
             "no hay autorización para otro intento"),
            ("intento_abierto", replace(e, proc=Proc(1, "t", "b")), {},
             "ya hay un escritor"),
            ("intento_abierto", e, {"poteto_digest": "", "rol": "responsable"},
             "intento del responsable sin poteto verificado"),
            ("conductor_fase", e, {}, "asociación sin fase"),
            ("conductor_fase", replace(e, conductor="X"), {"fase": "F", "token": "Y"},
             "la asociación no corresponde al conductor"),
            ("conductor_fase", replace(e, fases=(Fase("F", "resultado"),)),
             {"fase": "Z"}, "asociación a una fase inexistente"),
        )
        for kind, previo, datos, mensaje in casos:
            with self.subTest(kind=kind, mensaje=mensaje), self.assertRaises(
                TransicionIlegal
            ) as rechazo:
                reducir(previo, {"kind": kind, "data": datos}, nuevo=True)
            self.assertEqual(str(rechazo.exception), mensaje)

    def test_aplicar_intentos_historicos_incompletos_no_lanza(self) -> None:
        e = _entrega()
        casos = (
            ("reserva", replace(e, reserva=True), {}),
            ("reserva", e, {"generacion": "no entero", "ajustes": "no entero"}),
            ("intento_abierto", e, {}),
            ("intento_abierto", replace(e, proc=Proc(1, "t", "b")), {}),
            ("conductor_fase", e, {}),
            ("conductor", e, {}),
            ("actividad", e, {"observada": True}),
            ("colgada", e, {}),
        )
        for kind, previo, datos in casos:
            with self.subTest(kind=kind, datos=datos):
                self.assertIsNotNone(
                    reducir(previo, {"kind": kind, "data": datos}, nuevo=False)
                )
