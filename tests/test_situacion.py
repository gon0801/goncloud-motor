from __future__ import annotations

import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest import mock

from motor.encargos.dominio import TransicionIlegal, reducir
from tools import plegado
from tools.plegado import INVARIANTES, Reductor, Traza


def _entrega() -> tuple[dict, object]:
    evento = {"id": 1, "kind": "entrega", "data": {
        "id": "e", "clave": "e", "huella": "h", "texto": "pedido",
    }}
    return evento, reducir(None, evento, nuevo=False)


class SituacionComoLectura(unittest.TestCase):
    def test_variantes_no_se_guardan_en_encargo(self) -> None:
        from motor.encargos.situacion import Cancelado, Recibido, situacion_de

        _, e = _entrega()
        anterior = asdict(e)
        recibido = situacion_de(e)
        self.assertIsInstance(recibido, Recibido)
        self.assertEqual(asdict(recibido), {"estado": "recibido"})
        self.assertEqual(asdict(situacion_de(replace(e, estado="cancelado"))),
                         {"estado": "cancelado"})
        self.assertIsInstance(situacion_de(replace(e, estado="cancelado")), Cancelado)
        self.assertEqual(asdict(e), anterior)

    def test_las_cuatro_incoherencias_conservan_sus_mensajes(self) -> None:
        from motor.encargos.situacion import EstadoIncoherente, situacion_de

        self.assertTrue(issubclass(EstadoIncoherente, TransicionIlegal))
        _, e = _entrega()
        casos = (
            ({"estado": "esperando_reintento"}, "espera incompleta"),
            ({"estado": "conciliando"}, "conciliando sin efecto incierto"),
            ({"estado": "terminado"}, "terminado sin evidencia"),
            ({"estado": "ejecutando", "ultimo_hecho": ""}, "ejecutando sin hecho"),
        )
        for cambios, mensaje in casos:
            with self.subTest(mensaje=mensaje), self.assertRaisesRegex(EstadoIncoherente, mensaje):
                situacion_de(replace(e, **cambios))

    def test_comprobar_mide_incoherencias_y_dependencia_del_estado(self) -> None:
        evento, e = _entrega()
        nombres = dict(INVARIANTES)
        self.assertIn("situacion_tipada", nombres)
        self.assertIn("datos_dependen_del_estado", nombres)
        reductor = Reductor(reducir, ("recibido",))
        incoherente = replace(e, estado="terminado")
        hallado = nombres["situacion_tipada"](
            reductor, [evento], Traza(estados=[incoherente]),
        )
        self.assertIsNotNone(hallado)
        self.assertIn("terminado sin evidencia", hallado[1])
        posterior = {"id": 2, "kind": "recibo", "data": {}}
        dependencia = nombres["datos_dependen_del_estado"](
            reductor, [evento, posterior],
            Traza(estados=[e, replace(e, escrituras=False)]),
        )
        self.assertIsNotNone(dependencia)
        self.assertEqual(dependencia[0]["id"], 2)

    def test_comprobar_compara_datos_del_estado_entre_encargos(self) -> None:
        evento, e = _entrega()
        eventos = [evento]
        trazas = [
            Traza(estados=[e]),
            Traza(estados=[replace(e, escrituras=False)]),
        ]
        with (
            mock.patch.object(plegado, "ultimo_evento", return_value=2),
            mock.patch.object(plegado, "leer_encargos",
                              return_value=iter((("uno", eventos), ("dos", eventos)))),
            mock.patch.object(plegado, "trazar", side_effect=trazas),
            mock.patch.object(plegado, "INVARIANTES",
                              (("datos_dependen_del_estado",
                                dict(INVARIANTES)["datos_dependen_del_estado"]),)),
        ):
            resultado = plegado.comprobar(Reductor(reducir, ("recibido",)), Path("registro.sqlite"))
        self.assertEqual(
            [(v.invariante, v.encargo) for v in resultado.violaciones],
            [("datos_dependen_del_estado", "dos")],
        )


if __name__ == "__main__":
    unittest.main()
