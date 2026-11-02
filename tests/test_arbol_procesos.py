"""La adopción de un proceso por PPID exige que cada eslabón nazca en o después de su padre."""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

from motor.encargos import sesion
from motor.plataforma import posix
from motor.plataforma.arbol import descendientes_validos


class ArbolDeProcesosTest(unittest.TestCase):
    def test_no_adopta_un_proceso_viejo_cuyo_ppid_apunta_a_un_hijo_nuevo(self):
        snapshot = {
            10: (0, 100),
            20: (10, 110),
            30: (20, 50),
        }
        self.assertEqual(descendientes_validos(snapshot, 10), [20])

    def test_adopta_un_hijo_y_un_nieto_reales(self):
        snapshot = {
            10: (0, 100),
            20: (10, 110),
            30: (20, 120),
        }
        self.assertEqual(descendientes_validos(snapshot, 10), [20, 30])

    def test_un_eslabon_sin_hora_de_creacion_corta_esa_rama(self):
        snapshot = {
            10: (0, 100),
            20: (10, 110),
            30: (20, None),
            40: (30, 130),
        }
        self.assertEqual(descendientes_validos(snapshot, 10), [20])

    def test_una_raiz_sin_hora_de_creacion_no_adopta_nada(self):
        snapshot = {
            10: (0, None),
            20: (10, 110),
        }
        self.assertEqual(descendientes_validos(snapshot, 10), [])

    def test_un_hijo_nacido_en_la_misma_hora_que_el_padre_si_cuenta(self):
        snapshot = {
            10: (0, 100),
            20: (10, 100),
        }
        self.assertEqual(descendientes_validos(snapshot, 10), [20])


class InstantaneaPosixTest(unittest.TestCase):
    def test_parsea_pid_ppid_y_lstart(self):
        salida = (
            "  10   1 Sun Nov  1 19:56:00 2026\n"
            "  20  10 Sun Nov  1 19:56:01 2026\n"
        )
        snapshot = posix.instantanea_procesos(lectura=lambda: salida)
        self.assertEqual(snapshot[20][0], 10)
        self.assertGreater(snapshot[20][1], snapshot[10][1])

    def test_una_hora_ilegible_queda_sin_hora(self):
        salida = (
            "  10   1 Sun Nov  1 19:56:00 2026\n"
            "  20  10 no-es-fecha\n"
        )
        snapshot = posix.instantanea_procesos(lectura=lambda: salida)
        self.assertIsNone(snapshot[20][1])
        self.assertEqual(descendientes_validos(snapshot, 10), [])


@unittest.skipUnless(sys.platform.startswith("linux"), "ps real se valida en Linux")
class InstantaneaRealLinuxTest(unittest.TestCase):
    def test_lee_el_proceso_actual_y_su_hora(self):
        snapshot = posix.instantanea_procesos()
        self.assertIsNotNone(snapshot)
        self.assertIn(os.getpid(), snapshot)
        self.assertIsNotNone(snapshot[os.getpid()][1])


@unittest.skipIf(os.name == "nt", "la rama POSIX no corre en Windows")
class DescendientesPosixTest(unittest.TestCase):
    def test_descendientes_posix_no_adopta_el_proceso_viejo(self):
        snapshot = {
            10: (0, 100),
            20: (10, 110),
            30: (20, 50),
        }
        with (
            mock.patch(
                "motor.plataforma.posix.instantanea_procesos", return_value=snapshot
            ),
            mock.patch.object(
                sesion.plataforma, "hora_inicio", side_effect=lambda pid: f"hora-{pid}"
            ),
            mock.patch.object(sesion.plataforma, "boot_id", return_value="b"),
        ):
            identidades = sesion.descendientes(10)
        self.assertEqual([x["pid"] for x in identidades], [20])
