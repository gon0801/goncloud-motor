import os
import unittest
from datetime import datetime, timezone
from unittest import mock

from motor import plataforma
from motor.plataforma import posix, windows


class ArranqueTest(unittest.TestCase):
    def setUp(self):
        posix._cache_boottime[:] = [None, None]
        self.addCleanup(
            lambda: posix._cache_boottime.__setitem__(slice(None), [None, None])
        )

    def test_posix_dos_lecturas_menor_120_es_mismo_arranque(self):
        lecturas = iter(
            [
                "{ sec = 1700000000, usec = 0 }",
                "{ sec = 1700000060, usec = 0 }",
            ]
        )
        a = posix.boot_id_sistema(lectura=lambda: next(lecturas))
        b = posix.boot_id_sistema(lectura=lambda: next(lecturas))
        self.assertEqual(a, b)

    def test_posix_lecturas_lejanas_son_arranques_distintos(self):
        lecturas = iter(
            [
                "{ sec = 1700000000, usec = 0 }",
                "{ sec = 1700001000, usec = 0 }",
            ]
        )
        a = posix.boot_id_sistema(lectura=lambda: next(lecturas))
        b = posix.boot_id_sistema(lectura=lambda: next(lecturas))
        self.assertNotEqual(a, b)

    def test_windows_usa_last_boot_sin_depender_de_otro_reloj(self):
        fijo = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        with mock.patch("time.time", return_value=1000.0):
            a = windows.boot_id_sistema(consulta=lambda: fijo)
        with mock.patch("time.time", return_value=9999999.0):
            b = windows.boot_id_sistema(consulta=lambda: fijo)
        self.assertEqual(a, b)
        self.assertEqual(a, fijo.isoformat())

    def test_windows_fuente_es_win32_operating_system(self):
        import inspect

        fuente = inspect.getsource(windows._last_boot_up_time)
        self.assertIn("Win32_OperatingSystem", fuente)
        self.assertIn("LastBootUpTime", fuente)
        self.assertNotIn("GetTickCount64", inspect.getsource(windows))

    def test_marca_distinta_menor_120_no_es_corte(self):
        with mock.patch.dict(os.environ, {"MOTOR_BOOT_ID": "1700000060"}):
            proc = {"pid": os.getpid(), "boot_id": "1700000000"}
            self.assertNotEqual(plataforma.estado_proc(proc), "corte")

    def test_marca_lejana_si_es_corte(self):
        with mock.patch.dict(os.environ, {"MOTOR_BOOT_ID": "1700002000"}):
            proc = {"pid": os.getpid(), "boot_id": "1700000000"}
            self.assertEqual(plataforma.estado_proc(proc), "corte")


if __name__ == "__main__":
    unittest.main()
