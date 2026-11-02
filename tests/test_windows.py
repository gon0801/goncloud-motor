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

from motor.plataforma import windows
from tests import entorno

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


@entorno.requiere_windows
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

    @entorno.requiere_breakaway
    def test_estado_proc_vivo_muerto_corte(self):
        p = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            **windows.kwargs_grupo(),
        )
        self.pids.append(p.pid)
        proc = {"pid": p.pid, "boot_id": plataforma.boot_id()}
        self.assertEqual(plataforma.estado_proc(proc), "vivo")
        self.assertEqual(
            plataforma.estado_proc({**proc, "boot_id": "otro-arranque"}), "corte"
        )
        windows.matar_grupo(p)
        self.assertTrue(self._esperar(lambda: plataforma.estado_proc(proc) == "muerto"))

    @entorno.requiere_breakaway
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

    @entorno.requiere_breakaway
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

    @entorno.requiere_breakaway
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

    def test_job_restringido_permite_lanzar_y_cerrar(self):
        """Dentro de un Job sin breakaway, el árbol se crea sin esa bandera y se cierra entero."""
        from motor.effects import evento_de_arranque

        self.assertIsNone(evento_de_arranque({"estado": "creacion_fallida", "proc": {}}))
        self.assertIsNone(
            evento_de_arranque({"estado": "arranque_por_conciliar", "proc": {}})
        )
        self.assertIsNone(evento_de_arranque({"proc": {}}))
        self.assertIsNotNone(
            evento_de_arranque(
                {"estado": "proceso_observado", "proc": {"pid": 1, "inicio": "1"}}
            )
        )
        hijo_py = self.tmp / "hijo.py"
        padre_py = self.tmp / "padre.py"
        hijo_pid = self.tmp / "hijo.pid"
        nieto_pid = self.tmp / "nieto.pid"
        resultado = self.tmp / "resultado.json"
        helper = self.tmp / "helper.py"
        hijo_py.write_text(
            textwrap.dedent(
                f"""
                import subprocess, sys, time
                from pathlib import Path
                n = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(180)"])
                Path({str(nieto_pid)!r}).write_text(str(n.pid))
                time.sleep(180)
                """
            )
        )
        padre_py.write_text(
            textwrap.dedent(
                f"""
                import subprocess, sys, time
                from pathlib import Path
                h = subprocess.Popen([sys.executable, {str(hijo_py)!r}])
                Path({str(hijo_pid)!r}).write_text(str(h.pid))
                time.sleep(180)
                """
            )
        )
        helper.write_text(
            textwrap.dedent(
                f"""
                import json, subprocess, sys, time
                from pathlib import Path
                sys.path.insert(0, {str(RAIZ)!r})
                from motor.plataforma import (
                    Propiedad, crear_proceso, hora_inicio, matar_grupo, presencia_grupo,
                )
                from motor.plataforma.windows import pid_vivo
                creado = crear_proceso(
                    [sys.executable, {str(padre_py)!r}],
                    Propiedad.COMANDO_CONTENIDO,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                creado.liberar()
                p = creado.proceso
                fin = time.time() + 20
                while time.time() < fin and not (
                    Path({str(hijo_pid)!r}).exists() and Path({str(nieto_pid)!r}).exists()
                ):
                    time.sleep(0.1)
                inicio = hora_inicio(p.pid)
                hijo = int(Path({str(hijo_pid)!r}).read_text()) if Path({str(hijo_pid)!r}).exists() else 0
                nieto = int(Path({str(nieto_pid)!r}).read_text()) if Path({str(nieto_pid)!r}).exists() else 0
                matar_grupo(p)

                def muerto(pid):
                    return pid > 0 and not pid_vivo(pid)

                fin = time.time() + 15
                while time.time() < fin and not (
                    muerto(p.pid) and muerto(hijo) and muerto(nieto)
                ):
                    time.sleep(0.1)
                Path({str(resultado)!r}).write_text(json.dumps({{
                    "separacion": creado.separacion,
                    "job": creado.job,
                    "padre": p.pid,
                    "hijo": hijo,
                    "nieto": nieto,
                    "padre_muerto": muerto(p.pid),
                    "hijo_muerto": muerto(hijo),
                    "nieto_muerto": muerto(nieto),
                    "observacion": presencia_grupo(p.pid, inicio, creado.job),
                }}))
                """
            )
        )
        job = windows.JobMatarAlCerrar(matar_al_cerrar=False)
        self.addCleanup(job.cerrar)
        flags = windows.CREATE_NEW_PROCESS_GROUP | windows.CREATE_NO_WINDOW | windows.CREATE_SUSPENDED
        proc = subprocess.Popen(
            [sys.executable, str(helper)],
            creationflags=flags,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.pids.append(proc.pid)
        job.asignar(proc)
        windows._reanudar(proc.pid)
        out, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 0, err.decode("utf-8", "replace") + out.decode("utf-8", "replace"))
        import json

        dato = json.loads(resultado.read_text(encoding="utf-8"))
        self.assertEqual(dato["separacion"], "sin_breakaway", dato)
        self.assertTrue(dato["padre_muerto"] and dato["hijo_muerto"] and dato["nieto_muerto"], dato)
        self.assertEqual(dato["observacion"], "ausente", dato)

        marca = self.tmp / "sep-ejecuto"
        sep_res = self.tmp / "sep-resultado.json"
        sep = self.tmp / "sep.py"
        sep.write_text(
            textwrap.dedent(
                f"""
                import json, subprocess, sys
                from pathlib import Path
                sys.path.insert(0, {str(RAIZ)!r})
                from motor import plataforma
                marca = Path({str(marca)!r})
                try:
                    plataforma.crear_proceso(
                        [sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('ejecuto')", str(marca)],
                        plataforma.Propiedad.TRABAJADOR_SEPARADO,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    dato = {{"rechazado": False, "agente_ejecuto": marca.exists(), "causa": ""}}
                except OSError as e:
                    dato = {{"rechazado": True, "agente_ejecuto": marca.exists(), "causa": str(e)}}
                Path({str(sep_res)!r}).write_text(json.dumps(dato))
                """
            )
        )
        job_sep = windows.JobMatarAlCerrar(matar_al_cerrar=False)
        self.addCleanup(job_sep.cerrar)
        proc_sep = subprocess.Popen(
            [sys.executable, str(sep)],
            creationflags=flags,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.pids.append(proc_sep.pid)
        job_sep.asignar(proc_sep)
        windows._reanudar(proc_sep.pid)
        out, err = proc_sep.communicate(timeout=30)
        self.assertEqual(
            proc_sep.returncode,
            0,
            err.decode("utf-8", "replace") + out.decode("utf-8", "replace"),
        )
        sep_dato = json.loads(sep_res.read_text(encoding="utf-8"))
        self.assertTrue(sep_dato["rechazado"], sep_dato)
        self.assertFalse(sep_dato["agente_ejecuto"], sep_dato)
        self.assertIn("separar el trabajador", sep_dato["causa"])
        self.assertFalse(marca.exists(), sep_dato)

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


if __name__ == "__main__":
    unittest.main()
