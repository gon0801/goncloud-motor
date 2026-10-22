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


class ShellTest(unittest.TestCase):
    def test_por_omision_es_git_bash(self):
        argv, shell, _exe = windows.argv_shell("echo ok")
        self.assertEqual(argv[0], r"C:\Program Files\Git\bin\bash.exe")
        self.assertEqual(argv[1:], ["-c", "echo ok"])
        self.assertFalse(shell)

    def test_clave_bash_de_maquina_cambia_la_ruta(self):
        from motor.conf import valida_maquina

        valida_maquina({"nombre": "pc", "conf": "x", "bash": r"D:\Git\bin\bash.exe"})
        argv, _shell, _exe = windows.argv_shell("echo ok", r"D:\Git\bin\bash.exe")
        self.assertEqual(argv[0], r"D:\Git\bin\bash.exe")

    def test_nunca_cmd_ni_bash_de_wsl(self):
        argv, _shell, _exe = windows.argv_shell(
            "echo ok", r"C:\Windows\System32\bash.exe"
        )
        self.assertEqual(argv[0], r"C:\Program Files\Git\bin\bash.exe")
        self.assertNotIn("cmd.exe", argv[0].lower())
        self.assertFalse(windows.es_bash_de_wsl(argv[0]))

    def test_python_sin_stub_de_la_tienda(self):
        path = r"C:\Users\x\AppData\Local\Microsoft\WindowsApps;C:\Python312;C:\Windows"
        limpio = windows.path_sin_stub_tienda(path)
        self.assertNotIn("WindowsApps", limpio)
        self.assertIn(r"C:\Python312", limpio)
        self.assertTrue(
            windows.es_stub_tienda(
                r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\python.exe"
            )
        )

    def test_maquina_rechaza_bash_vacio_si_viene(self):
        from motor.conf import ConfError, valida_maquina

        with self.assertRaises(ConfError):
            valida_maquina({"nombre": "pc", "conf": "x", "bash": ""})

    @unittest.skipUnless(os.name == "nt", "en la PC corre con Git Bash real")
    def test_pc_test_f_sustitucion_y_python3(self):
        import subprocess
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as t:
            marca = Path(t) / "marca.txt"
            marca.write_text("ok", encoding="utf-8")
            cmd = f'test -f "{marca}" && echo "$(python3 -c \'print(40+2)\')"'
            argv, shell, exe = windows.argv_shell(cmd)
            r = subprocess.run(
                argv,
                shell=shell,
                executable=exe,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("42", r.stdout)


if __name__ == "__main__":
    unittest.main()
