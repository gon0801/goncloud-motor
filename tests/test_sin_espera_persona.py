"""El motor no tiene un estado que espere a una persona.

El plegado rechaza estados fuera del conjunto, los eventos históricos de
pregunta y quietud no reabren una espera, y solo `NecesitaAgente` autoriza un
turno nuevo.
"""

from __future__ import annotations
from tests.soporte_situacion import evolucionar

import tempfile
import unittest

from motor.encargos import dominio
from tests.test_flujo_revisiones import Recorrido


class EstadosDelEncargo(unittest.TestCase):
    def test_necesita_usuario_ya_no_es_un_estado(self):
        self.assertNotIn("necesita_usuario", dominio.ESTADOS)

    def test_la_situacion_rechaza_un_estado_fuera_del_conjunto(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "estado-ilegal", "algo")
            self.addCleanup(r.reg.close)
            actual = r.fold()
            with self.assertRaisesRegex(dominio.TransicionIlegal, "estado desconocido"):
                evolucionar(actual, estado="necesita_usuario")

    def test_una_pregunta_historica_se_pliega_sin_bloquear(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "pregunta-historica", "algo")
            self.addCleanup(r.reg.close)
            antes = r.fold()
            r.reg.anotar(
                r.enc,
                {
                    "kind": "pregunta",
                    "data": {"texto": "¿Publico el informe?", "bloquea_todo": True},
                },
                "p-hist",
            )
            despues = r.fold()
            self.assertEqual(despues.estado, antes.estado)
            self.assertIn("¿Publico el informe?", despues.preguntas)

    def test_una_quietud_historica_deja_un_pendiente_y_no_un_estado_de_espera(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "quietud-historica", "algo")
            self.addCleanup(r.reg.close)
            antes = r.fold()
            r.reg.anotar(
                r.enc,
                {"kind": "quietud", "data": {"causa": "sin evento nuevo"}},
                "q-hist",
            )
            despues = r.fold()
            self.assertNotEqual(despues.estado, "necesita_usuario")
            self.assertEqual(despues.estado, antes.estado)
            self.assertIn("sin evento nuevo", despues.pendientes_entrega)


class ResultadoCerrado(unittest.TestCase):
    def test_solo_necesita_agente_autoriza_un_turno(self):
        from motor.encargos.resultado import (
            Avanzo,
            Devolver,
            Espera,
            NecesitaAgente,
            permite_lanzar,
        )

        self.assertTrue(permite_lanzar(NecesitaAgente("revisor")))
        for resultado in (
            Avanzo(),
            Espera("2099-01-01T00:00:00Z", "esperando"),
            Devolver("responsable", "corregir"),
        ):
            self.assertFalse(permite_lanzar(resultado))


if __name__ == "__main__":
    unittest.main()
