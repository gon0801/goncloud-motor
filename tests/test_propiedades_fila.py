from __future__ import annotations

import unittest

from motor.encargos.dominio import _ENTRE_REINTENTOS, _EVENTOS_DE_MARCADOR
from motor.encargos.reductor import TABLA


class PropiedadesFila(unittest.TestCase):
    def test_tuplas_conservan_sus_elementos(self) -> None:  # no-roja-en-base: fija tuplas legadas
        self.assertIsInstance(_ENTRE_REINTENTOS, tuple)
        self.assertIsInstance(_EVENTOS_DE_MARCADOR, tuple)
        self.assertCountEqual(
            _ENTRE_REINTENTOS,
            ("plazo", "espera", "instrumento_fallo", "reserva", "reserva_cerrada",
             "contexto_entrega", "sesion_perdida", "limpio"),
        )
        self.assertCountEqual(
            _EVENTOS_DE_MARCADOR,
            ("incorporado", "quietud_vigilada", "deepseek_lectura", "deepseek_cerrado",
             "ci_sondeo", "excepcion", "conciliacion_fase", "pr_observado", "limpieza"),
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

    def test_cada_fila_declara_el_alcance_de_su_hecho(self) -> None:  # no-roja-en-base: fija la propiedad de las filas viejas y las cinco nuevas se prueban aparte
        fase = set("""
            ajuste bloqueo_fase ci_pr cierre_forzado comprobaciones_supervisor
            conciliacion_fase conductor_fase entrada_incorporada entrega_bloqueada
            entrega_devuelta entrega_externa fase_desplegada fase_reabierta
            fase_reactivada inspeccion_entregada inspeccion_solicitud lectura_fallida
            nota_pr politica_despliegue producto_listo resolucion respuesta revision
            pr_observado fase_cancelada pr_otra_base
        """.split())
        intento = set("""
            actividad colgada conductor intento_abierto intento_cerrado
            reserva reserva_cerrada sesion_perdida
        """.split())
        self.assertEqual(
            {kind: fila.alcance for kind, fila in TABLA.items()},
            {kind: ("fase" if kind in fase else "intento" if kind in intento else "encargo")
             for kind in TABLA},
        )

    def test_el_generador_reproduce_la_tabla_vigente(self) -> None:
        from tools.asignar_propiedades_fila import RUTA, asignar

        texto = RUTA.read_text(encoding="utf-8")
        self.assertEqual(asignar(texto), texto)
