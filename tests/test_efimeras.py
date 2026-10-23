"""MOTOR 03, bloque A: el despliegue falla si deja una tarea apuntando a su propia copia temporal."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor import efimeras

HOME_MAC = "/Users/gon"
HOME_PC = r"C:\Users\ehven"

# Plist de la Mini (ai.goncloud.claw-autoupdate), con la carpeta del motor puesta donde corre la tarea.
PLIST_MINI = {
    "Label": "ai.goncloud.claw-autoupdate",
    "ProgramArguments": [
        "/usr/bin/python3",
        "/Users/gon/goncloud-autoupdate/claw_autoupdate.py",
        "--config",
        "/Users/gon/.openclaw/openclaw.json",
    ],
    "EnvironmentVariables": {
        "PATH": "/Users/gon/.openclaw/tools/node-v24.19.0/bin:/usr/bin:/bin"
    },
    "StartCalendarInterval": {"Hour": 4, "Minute": 50},
    "RunAtLoad": False,
    "StandardOutPath": "/Users/gon/Library/Logs/goncloud/claw-autoupdate.log",
}

# /Library/LaunchDaemons/com.goncloud.motor.plist de la Mac.
PLIST_SERVICIO = b"""<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
  <key>Label</key><string>com.goncloud.motor</string>
  <key>UserName</key><string>dn</string>
  <key>ProgramArguments</key>
  <array><string>/opt/homebrew/opt/python@3.14/bin/python3.14</string><string>-m</string><string>motor</string><string>servicio</string><string>--home</string><string>/Users/dn/.motor</string></array>
  <key>WorkingDirectory</key><string>/Users/dn/motor</string>
  <key>EnvironmentVariables</key><dict><key>HOME</key><string>/Users/dn</string></dict>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
"""

CRON_LINEA = "50 4 * * * /usr/bin/python3 /Users/gon/.motor/wt/demo/A/limpia.py >> /tmp/limpia.log 2>&1"

# `ssh gwpc "schtasks /query /fo csv /v"`, recortado: salió 0 y no trajo filas negadas. La columna
# «Task To Run» trae comillas sin duplicar, así que el CSV no se puede leer con `csv`.
SCHTASKS_PC = (
    '"HostName","TaskName","Next Run Time","Status","Logon Mode","Last Run Time","Last Result","Author","Task To Run","Start In","Comment","Scheduled Task State"\n'
    '"GON-PC","\\Forja_TabGuard","10/23/2026 10:00:00 AM","Ready","Interactive only","10/23/2026 9:50:01 AM","0","N/A","C:\\Python314\\pythonw.exe "C:\\Users\\ehven\\AppData\\Local\\hermes\\forja\\forja_pc_tab_guard.py"","C:\\Users\\ehven\\AppData\\Local\\hermes\\forja","guarda de pestanas","Enabled"\n'
    '"GON-PC","\\GoncloudMotor","N/A","Running","Interactive/Background","10/22/2026 8:57:02 AM","267009","N/A","C:\\Users\\ehven\\AppData\\Roaming\\uv\\python\\cpython-3.12-windows-x86_64-none\\python.exe -m motor servicio --home "C:\\Users\\ehven\\.motor"","C:\\Users\\ehven\\motor","motor de planes","Enabled"\n'
)
SCHTASKS_NEGADA = '"GON-PC","\\Microsoft\\Windows\\Defrag\\ScheduledDefrag","ERROR: Access is denied."\n'

# Un trabajo de Hermes en jobs.json, con las rutas de Windows escapadas para JSON.
JOBS_HERMES = json.dumps(
    {
        "jobs": [
            {
                "id": "resumen",
                "command": "C:\\Users\\ehven\\.motor\\wt\\demo\\A\\resumen.cmd",
            }
        ]
    },
    indent=2,
)


def _cp(rc: int, out: str = "", err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess("x", rc, out, err)


class ContieneTest(unittest.TestCase):
    def test_ruta_en_program_arguments_y_en_working_directory(self):
        ruta = "/Users/gon/.motor/tmp/despliega-p-A"
        pa = dict(PLIST_MINI, ProgramArguments=["/usr/bin/python3", f"{ruta}/x.py"])
        wd = dict(PLIST_MINI, WorkingDirectory=ruta)
        for plist in (pa, wd):
            texto = efimeras._texto_plist(plistlib.dumps(plist))
            self.assertTrue(efimeras.contiene(texto, ruta, HOME_MAC), texto)
        sana = efimeras._texto_plist(plistlib.dumps(PLIST_MINI))
        self.assertFalse(efimeras.contiene(sana, ruta, HOME_MAC))

    def test_una_ruta_parecida_no_cuenta(self):
        ruta = "/Users/gon/.motor/tmp/despliega-p-A"
        for otra in (
            "/Users/gon/.motor/tmp/despliega-otro-A",
            "/Users/gon/.motor/tmp/despliega-p-AB",
            "/Users/gon/.motor/tmp/despliega-p-A-viejo/x",
            "/mnt/Users/gon/.motor/tmp/despliega-p-A",
        ):
            self.assertFalse(
                efimeras.contiene(f"python {otra}/x.py", ruta, HOME_MAC), otra
            )
        self.assertTrue(efimeras.contiene(f"python {ruta}/x.py", ruta, HOME_MAC))
        self.assertTrue(efimeras.contiene(f"cd {ruta}", ruta, HOME_MAC))

    def test_variables_y_tilde(self):
        ruta = "/Users/gon/.motor/wt/p/A"
        for texto in (
            "~/.motor/wt/p/A/run.sh",
            "$HOME/.motor/wt/p/A/run.sh",
            "${HOME}/.motor/wt/p/A/run.sh",
            "/Users//gon/.motor//wt/p/A/run.sh",
        ):
            self.assertTrue(efimeras.contiene(texto, ruta, HOME_MAC), texto)
        self.assertTrue(
            efimeras.contiene(
                "run /Users/gon/.motor/wt/p/A", "~/.motor/wt/p/A", HOME_MAC
            )
        )

    def test_windows_barras_variables_git_bash_y_mayusculas(self):
        ruta = r"C:\Users\ehven\.motor\wt\demo\A"
        fila = SCHTASKS_PC.replace(r"\.motor", r"\.motor\wt\demo\A")
        self.assertTrue(efimeras.contiene(fila, ruta, HOME_PC))
        self.assertTrue(efimeras.contiene(JOBS_HERMES, ruta, HOME_PC))
        for texto in (
            r"%USERPROFILE%\.motor\wt\demo\A\resumen.cmd",
            "/c/Users/ehven/.motor/wt/demo/A/resumen.sh",
            "c:\\\\users\\\\EHVEN\\\\.MOTOR\\\\wt\\\\demo\\\\a",
        ):
            self.assertTrue(efimeras.contiene(texto, ruta, HOME_PC), texto)
        self.assertTrue(
            efimeras.contiene(
                r"%LOCALAPPDATA%\hermes\forja\x.py",
                r"C:\Users\ehven\AppData\Local\hermes\forja",
                HOME_PC,
            )
        )
        self.assertFalse(
            efimeras.contiene(JOBS_HERMES, r"C:\Users\ehven\.motor\wt\demo\B", HOME_PC)
        )

    def test_mac_distingue_mayusculas(self):
        self.assertFalse(
            efimeras.contiene(
                "/Users/gon/.Motor/wt/p/A", "/Users/gon/.motor/wt/p/A", HOME_MAC
            )
        )


class FuentesTest(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="efimeras-"))
        self.agents = self.t / "LaunchAgents"
        self.agents.mkdir()
        self.conf = self.t / "conf"
        self.conf.mkdir()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.t, ignore_errors=True))

    def mac(self, crontab=None, dirs=None, **kw):
        crontab = crontab or _cp(1, "", "crontab: no crontab for gon")
        dirs = dirs if dirs is not None else [self.agents]
        real_ejecuta = efimeras.ejecuta
        with (
            mock.patch.object(efimeras, "_dirs_mac", lambda home: dirs),
            mock.patch.object(
                efimeras,
                "ejecuta",
                side_effect=lambda cmd, *a, **k: (
                    crontab if cmd == efimeras.CRONTAB else real_ejecuta(cmd, *a, **k)
                ),
            ),
        ):
            return efimeras.lee_tareas(
                "mac", self.conf, home=self.t, plataforma_="darwin", **kw
            )

    def test_plist_de_la_mini_y_del_servicio_se_leen_enteros(self):
        (self.agents / "ai.goncloud.claw-autoupdate.plist").write_bytes(
            plistlib.dumps(PLIST_MINI)
        )
        (self.agents / "com.goncloud.motor.plist").write_bytes(PLIST_SERVICIO)
        tareas, leidas, sin_leer, negados = self.mac()
        self.assertEqual((sin_leer, negados), ({}, {}))
        self.assertIn(str(self.agents), leidas)
        mini = tareas[str(self.agents / "ai.goncloud.claw-autoupdate.plist")]
        self.assertIn("/Users/gon/goncloud-autoupdate/claw_autoupdate.py", mini)
        self.assertIn("claw-autoupdate.log", mini)
        servicio = tareas[str(self.agents / "com.goncloud.motor.plist")]
        self.assertIn("/Users/dn/motor", servicio)
        self.assertIn("--home", servicio)

    def test_crontab_cada_linea_y_sin_crontab_cuenta_como_leida(self):
        _, leidas, sin_leer, _ = self.mac()
        self.assertIn(efimeras.CRONTAB, leidas)
        self.assertEqual(sin_leer, {})
        tareas, leidas, sin_leer, _ = self.mac(
            crontab=_cp(0, f"# nota\n\n{CRON_LINEA}\n")
        )
        self.assertEqual(list(tareas.values()), [CRON_LINEA])
        self.assertIn(efimeras.CRONTAB, leidas)

    def test_crontab_que_falla_de_verdad_queda_sin_leer(self):
        _, leidas, sin_leer, _ = self.mac(crontab=_cp(2, "", "crontab: boom"))
        self.assertNotIn(efimeras.CRONTAB, leidas)
        self.assertIn("boom", sin_leer[efimeras.CRONTAB])

    @unittest.skipIf(
        os.name == "nt" or os.geteuid() == 0, "necesita permisos de POSIX sin ser root"
    )
    def test_plist_sin_permiso_va_a_negados_y_la_carpeta_sigue_leida(self):
        bueno = self.agents / "bueno.plist"
        malo = self.agents / "root.plist"
        bueno.write_bytes(plistlib.dumps(PLIST_MINI))
        malo.write_bytes(PLIST_SERVICIO)
        malo.chmod(0o000)
        self.addCleanup(lambda: malo.chmod(0o600))
        tareas, leidas, sin_leer, negados = self.mac()
        self.assertIn(str(self.agents), leidas)
        self.assertNotIn(str(self.agents), sin_leer)
        self.assertIn(str(malo), negados)
        self.assertIn(str(bueno), tareas)
        self.assertNotIn(str(malo), tareas)

    def test_carpeta_que_no_se_puede_listar_queda_sin_leer(self):
        with mock.patch.object(
            efimeras.os, "listdir", side_effect=PermissionError(13, "Permission denied")
        ):
            tareas, leidas, sin_leer, _ = self.mac()
        self.assertIn(str(self.agents), sin_leer)
        self.assertNotIn(str(self.agents), leidas)

    def test_schtasks_con_salida_distinta_de_0_y_filas_cuenta_como_leida(self):
        salida = SCHTASKS_PC + SCHTASKS_NEGADA
        with mock.patch.object(efimeras, "ejecuta", return_value=_cp(1, salida, "")):
            tareas, leidas, sin_leer, negados = efimeras.lee_tareas(
                "pc", self.conf, home=HOME_PC, plataforma_="win32"
            )
        self.assertEqual(leidas, ["schtasks"])
        self.assertEqual(sin_leer, {})
        self.assertEqual(sorted(tareas), ["\\Forja_TabGuard", "\\GoncloudMotor"])
        self.assertIn("forja_pc_tab_guard.py", tareas["\\Forja_TabGuard"])
        self.assertIn("\\Microsoft\\Windows\\Defrag\\ScheduledDefrag", negados)
        self.assertTrue(
            efimeras.contiene(
                tareas["\\GoncloudMotor"], r"C:\Users\ehven\motor", HOME_PC
            )
        )

    def test_schtasks_sin_filas_queda_sin_leer(self):
        with mock.patch.object(
            efimeras, "ejecuta", return_value=_cp(1, "", "ERROR: algo")
        ):
            _, leidas, sin_leer, _ = efimeras.lee_tareas(
                "pc", self.conf, home=HOME_PC, plataforma_="win32"
            )
        self.assertEqual(leidas, [])
        self.assertIn("schtasks", sin_leer)

    def test_linux_solo_lee_crontab(self):
        with mock.patch.object(
            efimeras, "ejecuta", return_value=_cp(0, CRON_LINEA + "\n")
        ) as ej:
            tareas, leidas, _, _ = efimeras.lee_tareas(
                "srv", self.conf, home=self.t, plataforma_="linux"
            )
        ej.assert_called_once()
        self.assertEqual(leidas, [efimeras.CRONTAB])
        self.assertEqual(list(tareas.values()), [CRON_LINEA])

    def test_sin_programadas_toml_no_hay_extras_ni_error(self):
        self.assertFalse((self.conf / "programadas.toml").exists())
        self.assertEqual(
            efimeras.lee_tareas("mac", self.conf, sistema=False), ({}, [], {}, {})
        )
        self.assertEqual(
            efimeras.lee_tareas("mac", None, sistema=False), ({}, [], {}, {})
        )

    def test_sistema_false_no_toca_el_sistema(self):
        with mock.patch.object(
            efimeras, "ejecuta", side_effect=AssertionError("leyó el sistema")
        ):
            efimeras.lee_tareas("mac", self.conf, sistema=False)

    def test_extras_se_suman_a_las_del_sistema(self):
        extra = self.t / "extra"
        extra.mkdir()
        (extra / "a.plist").write_bytes(plistlib.dumps(PLIST_MINI))
        (extra / "b.json").write_text(JOBS_HERMES, encoding="utf-8")
        (self.conf / "programadas.toml").write_text(
            f'[mac]\narchivos = ["{extra}/*.json", "{extra}/*.plist"]\ncomandos = ["echo tarea-extra"]\n'
            '[pc]\ncomandos = ["echo de-la-pc"]\n'
        )
        (self.agents / "s.plist").write_bytes(plistlib.dumps(PLIST_MINI))
        tareas, leidas, sin_leer, _ = self.mac()
        self.assertEqual(sin_leer, {})
        self.assertIn(str(self.agents / "s.plist"), tareas)
        self.assertIn(str(extra / "a.plist"), tareas)
        self.assertIn("ehven", tareas[str(extra / "b.json")])
        self.assertEqual(tareas["echo tarea-extra"].strip(), "tarea-extra")
        self.assertNotIn("echo de-la-pc", tareas)
        self.assertIn("echo tarea-extra", leidas)

    def test_una_fuente_que_pasa_del_tope_queda_sin_leer(self):
        (self.conf / "programadas.toml").write_text(
            '[mac]\ncomandos = ["sleep 5", "echo bien"]\n'
        )
        with mock.patch.object(efimeras, "TOPE_FUENTE_S", 0.3):
            tareas, leidas, sin_leer, _ = efimeras.lee_tareas(
                "mac", self.conf, sistema=False
            )
        self.assertIn("sleep 5", sin_leer)
        self.assertIn("pasó de", sin_leer["sleep 5"])
        self.assertNotIn("sleep 5", leidas)
        self.assertIn("echo bien", leidas)

    def test_comando_extra_que_falla_queda_sin_leer(self):
        (self.conf / "programadas.toml").write_text(
            '[mac]\ncomandos = ["echo no; exit 3"]\n'
        )
        _, leidas, sin_leer, _ = efimeras.lee_tareas("mac", self.conf, sistema=False)
        self.assertEqual(leidas, [])
        self.assertIn("salió 3", sin_leer["echo no; exit 3"])


if __name__ == "__main__":
    unittest.main()
