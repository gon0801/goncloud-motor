"""Pruebas que solo tienen sentido en Windows (la PC). En la Mac se saltan con el motivo.

Se corren con `bash ops/prueba-pc.sh <sha>`.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from motor.plataforma import windows

ES_WINDOWS = os.name == "nt"
RAIZ = Path(__file__).resolve().parent.parent

if ES_WINDOWS:
    from motor import plataforma


def _run(*args, **kwargs):
    return subprocess.run(*args, check=False, **kwargs)


def _distro_wsl() -> str:
    """Nombre de la primera distro de WSL, o "" si no hay (wsl.exe -l escribe en UTF-16)."""
    try:
        r = _run(["wsl.exe", "-l", "-q"], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if r.returncode != 0:
        return ""
    for linea in r.stdout.decode("utf-16-le", errors="replace").splitlines():
        if linea.strip("\x00 ﻿\r\n"):
            return linea.strip("\x00 ﻿\r\n")
    return ""


@unittest.skipUnless(
    ES_WINDOWS,
    "solo corre en Windows (la PC); en la Mac se prueba con ops/prueba-pc.sh",
)
class WindowsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="motor-win-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.pids: list[int] = []
        self.addCleanup(self._matar_pids)

    def _matar_pids(self):
        for pid in self.pids:
            _run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)

    def _esperar(self, cond, seg=10.0):
        fin = time.time() + seg
        while time.time() < fin:
            if cond():
                return True
            time.sleep(0.1)
        return cond()

    def test_boot_id_estable_y_no_vacio(self):
        a = plataforma.boot_id()
        self.assertTrue(a)
        self.assertEqual(a, plataforma.boot_id())

    def test_estado_proc_vivo_muerto_corte(self):
        p = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            **windows.kwargs_grupo(),
        )
        self.pids.append(p.pid)
        # el reinicio se simula con MOTOR_BOOT_ID: una marca inventada de otra clase (p. ej.
        # "otro-arranque" contra la fecha real de la PC) no cuenta como arranque distinto
        with mock.patch.dict(os.environ, {"MOTOR_BOOT_ID": "arranque-1"}):
            proc = {"pid": p.pid, "boot_id": plataforma.boot_id()}
            self.assertEqual(plataforma.estado_proc(proc), "vivo")
            os.environ["MOTOR_BOOT_ID"] = "arranque-2"
            self.assertEqual(plataforma.estado_proc(proc), "corte")
            os.environ["MOTOR_BOOT_ID"] = "arranque-1"
        windows.matar_grupo(p)
        self.assertTrue(self._esperar(lambda: plataforma.estado_proc(proc) == "muerto"))

    def test_proceso_separado_sobrevive_al_cierre_del_daemon(self):
        marca = self.tmp / "vivo.txt"
        hijo = self.tmp / "hijo.py"
        hijo.write_text(
            textwrap.dedent(f"""
            import time
            from pathlib import Path
            for i in range(600):
                Path(r"{marca}").write_text(str(i))
                time.sleep(0.1)
        """)
        )
        # El "daemon": lanza el proceso separado, imprime su estado y termina.
        daemon = self.tmp / "daemon.py"
        daemon.write_text(
            textwrap.dedent(f"""
            import json, sys
            from pathlib import Path
            sys.path.insert(0, r"{RAIZ}")
            from motor.plataforma import lanzar_separado
            print(json.dumps(lanzar_separado([sys.executable, r"{hijo}"], Path(r"{self.tmp}"), dict(__import__("os").environ), Path(r"{self.tmp}") / "log.txt")))
        """)
        )
        r = _run(
            [sys.executable, str(daemon)], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        import json

        proc = json.loads(r.stdout.strip().splitlines()[-1])
        self.pids.append(proc["pid"])
        self.assertTrue(self._esperar(marca.exists), "el hijo nunca arrancó")
        # El daemon ya murió; el hijo sigue escribiendo.
        antes = marca.read_text()
        self.assertTrue(
            self._esperar(lambda: marca.read_text() != antes),
            "el hijo se detuvo al cerrar el daemon",
        )
        self.assertEqual(plataforma.estado_proc(proc), "vivo")

    def test_matar_grupo_termina_el_arbol(self):
        nieto_pid = self.tmp / "nieto.pid"
        padre = self.tmp / "padre.py"
        padre.write_text(
            textwrap.dedent(f"""
            import subprocess, sys, time
            n = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
            open(r"{nieto_pid}", "w").write(str(n.pid))
            time.sleep(120)
        """)
        )
        p = subprocess.Popen([sys.executable, str(padre)], **windows.kwargs_grupo())
        self.pids.append(p.pid)
        windows.contener(p)
        self.assertTrue(self._esperar(nieto_pid.exists))
        nieto = int(nieto_pid.read_text())
        self.pids.append(nieto)
        self.assertTrue(windows.pid_vivo(nieto))
        windows.matar_grupo(p)
        self.assertTrue(
            self._esperar(lambda: not windows.pid_vivo(nieto)),
            "el nieto sobrevivió a matar_grupo",
        )

    def test_job_mata_al_arbol_al_cerrar_el_handle(self):
        nieto_pid = self.tmp / "nieto.pid"
        padre = self.tmp / "padre.py"
        padre.write_text(
            textwrap.dedent(f"""
            import subprocess, sys, time
            n = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
            open(r"{nieto_pid}", "w").write(str(n.pid))
            time.sleep(120)
        """)
        )
        p = subprocess.Popen([sys.executable, str(padre)], **windows.kwargs_grupo())
        self.pids.append(p.pid)
        job = windows.JobMatarAlCerrar()
        job.asignar(p)
        self.assertTrue(self._esperar(nieto_pid.exists))
        nieto = int(nieto_pid.read_text())
        self.pids.append(nieto)
        job.cerrar()  # sin taskkill: solo el Job
        self.assertTrue(
            self._esperar(lambda: p.poll() is not None),
            "el padre sigue vivo tras cerrar el Job",
        )
        self.assertTrue(
            self._esperar(lambda: not windows.pid_vivo(nieto)),
            "el nieto sigue vivo tras cerrar el Job",
        )

    def test_shim_cmd_se_ejecuta_con_sus_argumentos(self):
        (self.tmp / "falso-cli.cmd").write_text("@echo off\r\necho shim:%1:%2\r\n")
        argv = windows.resolve_shim("falso-cli", str(self.tmp)) + ["uno", "dos"]
        r = _run(argv, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("shim:uno:dos", r.stdout)

    def test_wsl_e_pasa_variables_por_wslenv(self):
        distro = _distro_wsl()
        if not distro:
            self.skipTest("sin distro WSL instalada en esta PC (wsl.exe -l está vacío)")
        env = windows.env_wsl(
            {**os.environ, "MOTOR_PRUEBA_WSL": "valor-ok"}, ["MOTOR_PRUEBA_WSL"]
        )
        r = _run(
            ["wsl.exe", "-d", distro, "-e", "sh", "-c", "echo $MOTOR_PRUEBA_WSL"],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "valor-ok")

    def test_guardian_wsl_corre_el_comando_y_devuelve_su_codigo(self):
        distro = _distro_wsl()
        if not distro:
            self.skipTest("sin distro WSL instalada en esta PC (wsl.exe -l está vacío)")
        argv = windows.argv_wsl(distro, ["sh", "-c", "exit 7"], timeout=30)
        r = _run(argv, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 7, r.stderr)


class PopenGrupoTest(unittest.TestCase):
    """Corre en cualquier sistema (Popen va mockeado). Falla ya vista en producción en la PC:
    el Job del servicio niega CREATE_BREAKAWAY_FROM_JOB y CreateProcess devuelve WinError 5."""

    def test_reintenta_sin_breakaway_cuando_el_job_lo_niega(self):
        ok = SimpleNamespace(pid=4321)
        with mock.patch.object(
            windows.subprocess,
            "Popen",
            side_effect=[PermissionError(5, "Access is denied"), ok],
        ) as popen:
            r = windows.popen_grupo(["agente"], cwd="/tmp/ag")
        self.assertIs(r, ok)
        self.assertEqual(popen.call_count, 2)
        primera, segunda = (c.kwargs["creationflags"] for c in popen.call_args_list)
        self.assertEqual(primera, windows.flags_creacion())
        self.assertTrue(primera & windows.CREATE_BREAKAWAY_FROM_JOB)
        self.assertEqual(segunda, primera & ~windows.CREATE_BREAKAWAY_FROM_JOB)
        self.assertFalse(segunda & windows.CREATE_BREAKAWAY_FROM_JOB)
        self.assertTrue(segunda & windows.CREATE_NEW_PROCESS_GROUP)
        self.assertTrue(segunda & windows.CREATE_NO_WINDOW)
        self.assertEqual(
            popen.call_args_list[0].args, popen.call_args_list[1].args
        )
        k0 = {
            k: v
            for k, v in popen.call_args_list[0].kwargs.items()
            if k != "creationflags"
        }
        k1 = {
            k: v
            for k, v in popen.call_args_list[1].kwargs.items()
            if k != "creationflags"
        }
        self.assertEqual(k0, k1)
        self.assertEqual(k0, {"cwd": "/tmp/ag"})

    def test_arranque_normal_mantiene_las_flags(self):
        ok = SimpleNamespace(pid=111)
        with mock.patch.object(windows.subprocess, "Popen", return_value=ok) as popen:
            r = windows.popen_grupo(["agente"], cwd="/tmp/ag")
        self.assertIs(r, ok)
        popen.assert_called_once_with(
            ["agente"], creationflags=windows.flags_creacion(), cwd="/tmp/ag"
        )

    def test_error_que_no_es_permiso_se_propaga(self):
        with mock.patch.object(
            windows.subprocess, "Popen", side_effect=FileNotFoundError("no está")
        ) as popen:
            with self.assertRaises(FileNotFoundError):
                windows.popen_grupo(["agente"])
        self.assertEqual(popen.call_count, 1)


if __name__ == "__main__":
    unittest.main()
