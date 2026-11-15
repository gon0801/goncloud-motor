from __future__ import annotations

import unittest

from motor.encargos.dominio import _ENTRE_REINTENTOS, _EVENTOS_DE_MARCADOR
from motor.encargos.reductor import TABLA


class PropiedadesFila(unittest.TestCase):
    # no-roja-en-base: fija las tuplas que ahora salen de la tabla
    def test_tuplas_conservan_sus_elementos(self) -> None:
        self.assertIsInstance(_ENTRE_REINTENTOS, tuple)
        self.assertIsInstance(_EVENTOS_DE_MARCADOR, tuple)
        self.assertCountEqual(
            _ENTRE_REINTENTOS,
            ("plazo", "espera", "instrumento_fallo", "reserva", "reserva_cerrada",
             "contexto_entrega", "sesion_perdida"),
        )
        self.assertCountEqual(
            _EVENTOS_DE_MARCADOR,
            ("incorporado", "quietud_vigilada", "deepseek_lectura", "deepseek_cerrado",
             "ci_sondeo", "excepcion", "conciliacion_fase"),
        )

    def test_las_tuplas_salen_de_las_propiedades_de_fila(self) -> None:
        self.assertEqual(
            _EVENTOS_DE_MARCADOR,
            tuple(kind for kind, fila in TABLA.items() if fila.marcador),
        )
        self.assertEqual(
            _ENTRE_REINTENTOS,
            tuple(kind for kind, fila in TABLA.items()
                  if not fila.corta_series and not fila.marcador),
        )
        self.assertTrue(all(not fila.corta_series for fila in TABLA.values() if fila.marcador))

    def test_cada_fila_declara_el_alcance_de_su_hecho(self) -> None:
        for fila in TABLA.values():
            self.assertIn(fila.alcance, ("encargo", "fase", "intento"))
        self.assertEqual(TABLA["recibo"].alcance, "encargo")
        self.assertEqual(TABLA["fase_desplegada"].alcance, "fase")
        self.assertEqual(TABLA["inspeccion_solicitud"].alcance, "fase")
        self.assertEqual(TABLA["intento_abierto"].alcance, "intento")
