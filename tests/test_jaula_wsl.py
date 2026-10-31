"""Lanzar un agente en WSL con la jaula de bubblewrap.

Lo que no necesita Linux corre en cualquier sistema. Las pruebas del guardián usan un doble de
`wsl.exe` (un script que descarta `-d <distro> --exec` y corre el resto aquí) y la jaula real:
corren donde hay bubblewrap, y en CI (MOTOR_EXIGE_BWRAP=1) una jaula que no arranca es un fallo.
"""

import ast
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest import mock

from motor.encargos import aislamiento, sesion
from motor.plataforma import jaula_linux, jaula_wsl

LINUX = sys.platform.startswith("linux")
CAUSA = jaula_linux.causa_no_disponible() if LINUX else "la jaula de bubblewrap solo corre en Linux"
EXIGIDA = os.environ.get("MOTOR_EXIGE_BWRAP") == "1"

ESPACIO_W = PureWindowsPath(r"C:\Users\ehven\.motor-encargos\espacio\enc_x")
RUNTIME_W = PureWindowsPath(r"C:\Users\ehven\.motor-encargos\runtime\enc_x\intento")
ESPACIO_L = "/mnt/c/Users/ehven/.motor-encargos/espacio/enc_x"
RUNTIME_L = "/mnt/c/Users/ehven/.motor-encargos/runtime/enc_x/intento"


class FuenteTest(unittest.TestCase):
    def test_la_fuente_de_la_distro_es_ascii_y_es_el_mismo_programa(self):
        original = Path(jaula_linux.__file__).read_text(encoding="utf-8")
        enviada = jaula_wsl.fuente()
        self.assertTrue(enviada.isascii())
        self.assertEqual(ast.dump(ast.parse(enviada)), ast.dump(ast.parse(original)))

    def test_el_argv_no_pasa_por_un_shell_y_cabe_en_la_linea_de_comandos_de_windows(self):
        jaula = jaula_wsl.jaula_de(
            ESPACIO_W, RUNTIME_W, lecturas=(PureWindowsPath(r"C:\fuentes\a"),),
            agente="claude",
        )
        cmd = ["claude", "--print", "Lee el pedido en " + str(RUNTIME_W / "prompt.md")]
        env = {"MOTOR_GENERACION_RUTA": r"C:\Users\ehven\flota\g1"}
        argv = jaula_wsl.argv_guardian(jaula, cmd, env, control=ESPACIO_W)
        self.assertEqual(
            argv[:7],
            ["wsl.exe", "--exec", "/usr/bin/python3", "-I", "-S", "-c", jaula_wsl.fuente()],
        )
        self.assertEqual(argv[7], "guardian")
        for pieza in ("/bin/sh", "bash", "cmd.exe", "powershell"):
            self.assertNotIn(pieza, argv[:6])
        self.assertLess(len(subprocess.list2cmdline(argv)), 28000)

    def test_la_distro_elegida_va_con_dash_d(self):
        self.assertEqual(jaula_wsl.argv_orden("Ubuntu", "estado", "{}")[:4], ["wsl.exe", "-d", "Ubuntu", "--exec"])


class ConcesionTest(unittest.TestCase):
    def test_la_concesion_va_en_rutas_de_la_distro(self):
        jaula = jaula_wsl.jaula_de(
            ESPACIO_W, RUNTIME_W, lectura=True,
            lecturas=(PureWindowsPath(r"C:\fuentes\datos.db"),), agente="claude",
        )
        self.assertEqual(jaula["espacio"], ESPACIO_L)
        self.assertEqual(jaula["runtime"], RUNTIME_L)
        self.assertEqual(jaula["lecturas"], ["/mnt/c/fuentes/datos.db"])
        self.assertTrue(jaula["lectura"])
        self.assertEqual(jaula["credencial"], "claude")
        self.assertEqual(jaula["red"], jaula_linux.RED_ABIERTA)

    def test_solo_claude_lleva_la_credencial_de_la_distro(self):
        self.assertEqual(jaula_wsl.jaula_de(ESPACIO_W, RUNTIME_W, agente="qwen")["credencial"], "")

    def test_el_spec_traduce_el_comando_el_entorno_y_el_control_y_no_lleva_secretos(self):
        jaula = jaula_wsl.jaula_de(ESPACIO_W, RUNTIME_W, agente="claude")
        prompt = RUNTIME_W / "prompt.md"
        spec = jaula_wsl.spec_guardian(
            jaula,
            ["claude", "--session-id", "s1", f"Lee el pedido en {prompt}. Ejecuta."],
            {
                "MOTOR_GENERACION_RUTA": r"C:\Users\ehven\flota\g1",
                "MOTOR_GENERACION": "g1",
                "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-secreto",
                "PATH": r"C:\Windows",
            },
            control=PureWindowsPath(r"C:\Users\ehven\ejecucion\enc_x\intento-1"),
            cwd=ESPACIO_W,
        )
        self.assertEqual(spec["cmd"][-1], f"Lee el pedido en {RUNTIME_L}/prompt.md. Ejecuta.")
        self.assertEqual(
            spec["env"],
            {"MOTOR_GENERACION_RUTA": "/mnt/c/Users/ehven/flota/g1", "MOTOR_GENERACION": "g1"},
        )
        self.assertEqual(spec["control"], "/mnt/c/Users/ehven/ejecucion/enc_x/intento-1")
        self.assertEqual(spec["cwd"], ESPACIO_L)
        self.assertNotIn("secreto", json.dumps(spec))


class AislarTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name).resolve()
        self.espacio = base / "espacio" / "enc_x"
        self.runtime = base / "runtime" / "enc_x" / "intento"
        self.espacio.mkdir(parents=True)

    def aislar(self, agente, **extra):
        with mock.patch("motor.encargos.aislamiento._en_windows", return_value=True):
            return aislamiento.aislar(
                [agente, "--print", "x"], self.espacio, self.runtime, suscripcion=True,
                agente=agente, **extra,
            )

    def test_claude_en_windows_se_lanza_en_wsl_sin_leer_las_credenciales_de_windows(self):
        with mock.patch("motor.encargos.suscripciones.preparar", side_effect=AssertionError):
            comando = self.aislar("claude")
        self.assertEqual(comando.mecanismo, "wsl-bwrap")
        self.assertEqual(comando.argv, ["claude", "--print", "x"])
        self.assertEqual(comando.entorno, {})
        self.assertEqual(comando.jaula["credencial"], "claude")
        self.assertFalse(comando.jaula["lectura"])

    def test_el_revisor_recibe_el_espacio_de_solo_lectura(self):
        self.assertTrue(self.aislar("claude", lectura=True).jaula["lectura"])

    def test_las_fuentes_autorizadas_van_como_lecturas(self):
        fuente = Path(self._tmp.name).resolve() / "datos.db"
        fuente.write_text("x")
        self.assertEqual(
            self.aislar("claude", lecturas_extra=(fuente,)).jaula["lecturas"], [str(fuente)]
        )

    def test_el_resto_de_los_agentes_y_la_sonda_no_pasan_por_wsl(self):
        self.assertFalse(aislamiento.usa_wsl("codex"))
        self.assertFalse(aislamiento.usa_wsl(None))
        with mock.patch("motor.encargos.aislamiento._en_windows", return_value=False):
            self.assertFalse(aislamiento.usa_wsl("claude"))
        with mock.patch("motor.encargos.aislamiento._en_windows", return_value=True):
            self.assertTrue(aislamiento.usa_wsl("claude"))

    def test_un_comando_de_wsl_no_se_ejecuta_sin_jaula(self):
        comando = self.aislar("claude")
        with self.assertRaises(RuntimeError):
            aislamiento._ejecutar_en_jaula(comando, self.espacio, {}, plazo=1)


class CredencialTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        (self.home / ".claude").mkdir()

    def credenciales(self, **cuenta):
        base = {"accessToken": "sk-ant-oat-acceso", "refreshToken": "sk-ant-ort-renueva",
                "scopes": ["user:inference"], "expiresAt": (time.time() + 3600) * 1000}
        base.update(cuenta)
        (self.home / ".claude" / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": base}), encoding="utf-8"
        )

    def test_la_jaula_recibe_el_token_de_acceso_y_nunca_el_de_renovacion(self):
        self.credenciales()
        entorno = jaula_linux.credencial_de_claude("/no/se/usa", str(self.home))
        self.assertEqual(entorno["CLAUDE_CODE_OAUTH_TOKEN"], "sk-ant-oat-acceso")
        self.assertNotIn("sk-ant-ort-renueva", json.dumps(entorno))
        self.assertEqual(entorno["DISABLE_AUTOUPDATER"], "1")

    def test_sin_acceso_de_inferencia_o_sin_archivo_la_causa_es_concreta(self):
        with self.assertRaisesRegex(ValueError, "no se puede leer la suscripcion"):
            jaula_linux.credencial_de_claude("/x", str(self.home))
        self.credenciales(scopes=["user:profile"])
        with self.assertRaisesRegex(ValueError, "inferencia"):
            jaula_linux.credencial_de_claude("/x", str(self.home))

    @unittest.skipIf(os.name == "nt", "scripts de ensayo")
    def test_un_acceso_vencido_se_renueva_con_el_cliente_oficial_antes_de_entrar(self):
        self.credenciales(expiresAt=1000)
        nueva = json.dumps({"claudeAiOauth": {
            "accessToken": "sk-ant-oat-nuevo", "scopes": ["user:inference"],
            "expiresAt": (time.time() + 3600) * 1000}})
        renovada = self.home / "renovada.json"
        renovada.write_text(nueva)
        cliente = self.home / "claude"
        cliente.write_text(f"#!/bin/sh\ncp '{renovada}' '{self.home}/.claude/.credentials.json'\n")
        cliente.chmod(0o755)
        entorno = jaula_linux.credencial_de_claude(str(cliente), str(self.home))
        self.assertEqual(entorno["CLAUDE_CODE_OAUTH_TOKEN"], "sk-ant-oat-nuevo")

    @unittest.skipIf(os.name == "nt", "scripts de ensayo")
    def test_un_acceso_vencido_que_no_se_renueva_es_una_causa_no_un_token_viejo(self):
        self.credenciales(expiresAt=1000)
        cliente = self.home / "claude"
        cliente.write_text("#!/bin/sh\nexit 0\n")
        cliente.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "no pudo renovar"):
            jaula_linux.credencial_de_claude(str(cliente), str(self.home))

    def test_el_ejecutable_se_busca_en_la_variable_el_home_y_el_path(self):
        propio = self.home / "propio"
        propio.write_text("#!/bin/sh\n")
        propio.chmod(0o755)
        self.assertEqual(jaula_linux.ejecutable_de_claude({"MOTOR_WSL_CLAUDE": str(propio)}), str(propio))
        (self.home / ".local" / "bin").mkdir(parents=True)
        en_home = self.home / ".local" / "bin" / "claude"
        en_home.write_text("#!/bin/sh\n")
        en_home.chmod(0o755)
        with mock.patch("os.path.expanduser", return_value=str(self.home)):
            self.assertEqual(jaula_linux.ejecutable_de_claude({}), str(en_home))


class SupervisionTest(unittest.TestCase):
    """El supervisor ve a wsl.exe; lo de la distro lo consulta y lo cierra por su cuenta."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        (self.dir / "arranque.json").write_text(json.dumps({"aislamiento": "wsl-bwrap"}))
        # Un intento de otro arranque ("corte") y con un pid que no puede existir: Windows no
        # ve nada de el y ningun proceso ajeno corre riesgo.
        arranque = mock.patch.dict(os.environ, {"MOTOR_BOOT_ID": "arranque-actual"})
        arranque.start()
        self.addCleanup(arranque.stop)
        self.proc = {"pid": 2**22 + 12345, "inicio": "no existe", "host": socket.gethostname(),
                     "boot_id": "arranque-anterior", "token": "t"}

    def control(self, estado):
        (self.dir / jaula_wsl.ARCHIVO_CONTROL).write_text(json.dumps(
            {"guardian": 1, "guardian_inicio": "1", "bwrap": 2, "bwrap_inicio": "2", "estado": estado}))

    def test_wsl_exe_ya_no_esta_pero_la_jaula_sigue_viva_en_la_distro(self):
        self.control("activo")
        with mock.patch.object(jaula_wsl, "estado_en_distro", return_value="vivo") as q:
            self.assertEqual(sesion.observar(self.dir, self.proc), "vivo")
        q.assert_called_once()

    def test_si_la_distro_no_responde_el_estado_es_incierto_no_ausente(self):
        self.control("activo")
        with mock.patch.object(jaula_wsl, "estado_en_distro", return_value="incierto"):
            self.assertEqual(sesion.observar(self.dir, self.proc), "incierto")

    def test_un_intento_de_windows_no_consulta_a_wsl(self):
        (self.dir / "arranque.json").write_text(json.dumps({"aislamiento": "appcontainer"}))
        with mock.patch.object(jaula_wsl, "estado_en_distro", side_effect=AssertionError):
            self.assertEqual(sesion.observar(self.dir, self.proc), "ausente")

    def test_parar_cierra_la_jaula_de_la_distro_aunque_wsl_exe_ya_murio(self):
        self.control("activo")
        with mock.patch.object(jaula_wsl, "cerrar_en_distro", return_value="ausente") as c:
            sesion.cerrar(self.dir, self.proc)
        c.assert_called_once()
        self.assertEqual(c.call_args.args[0], self.dir)

    def test_un_intento_de_windows_no_pide_cerrar_nada_a_wsl(self):
        (self.dir / "arranque.json").write_text(json.dumps({"aislamiento": "appcontainer"}))
        with mock.patch.object(jaula_wsl, "cerrar_en_distro", side_effect=AssertionError):
            sesion.cerrar(self.dir, self.proc)

    def test_lo_ya_cerrado_no_arranca_wsl_otra_vez(self):
        self.control("cerrado")
        correr = mock.Mock(side_effect=AssertionError)
        self.assertEqual(jaula_wsl.estado_en_distro(self.dir, correr=correr), "ausente")
        self.assertEqual(jaula_wsl.cerrar_en_distro(self.dir, correr=correr), "ausente")

    def test_ausente_en_la_distro_deja_el_control_cerrado_para_no_preguntar_de_nuevo(self):
        self.control("activo")
        respuesta = mock.Mock(stdout=json.dumps({"vivo": False}) + "\n")
        self.assertEqual(
            jaula_wsl.estado_en_distro(self.dir, correr=mock.Mock(return_value=respuesta)), "ausente"
        )
        self.assertEqual(jaula_wsl.control_de(self.dir)["estado"], "cerrado")

    def test_una_distro_que_no_contesta_es_incierto(self):
        self.control("activo")
        correr = mock.Mock(side_effect=subprocess.TimeoutExpired("wsl.exe", 30))
        self.assertEqual(jaula_wsl.cerrar_en_distro(self.dir, correr=correr), "incierto")
        self.assertEqual(jaula_wsl.control_de(self.dir)["estado"], "activo")

    def test_una_herramienta_abierta_en_el_flujo_cuenta_aunque_windows_no_vea_hijos(self):
        (self.dir / "herramienta.json").write_text(
            json.dumps({"observada": True, "activas": ["t1"]})
        )
        (self.dir / "agent.pid").write_text("999999")
        self.assertTrue(sesion.herramienta_en_curso(self.dir))
        (self.dir / "arranque.json").write_text(json.dumps({"aislamiento": "appcontainer"}))
        with mock.patch.object(sesion, "descendientes", return_value=None):
            self.assertIsNone(sesion.herramienta_en_curso(self.dir))


class ComprobacionesDelAgenteTest(unittest.TestCase):
    """Lo que el agente de WSL declara en .interno/comprobaciones.json se ejecuta en el host."""

    ARGV = ["python3", "/mnt/c/Users/ehven/x/ops/e.py", "--out=/mnt/c/t/o", "-v"]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.espacio = base / "espacio" / "enc_x"
        self.interno = self.espacio / ".interno"
        self.interno.mkdir(parents=True)
        self.directorio = base / "ejecucion"
        self.directorio.mkdir()
        self.declarado = json.dumps(
            {"comprobaciones": [{"objetivo": "ensayo", "argv": self.ARGV}]}
        )
        (self.interno / "comprobaciones.json").write_text(self.declarado)

    def ejecutadas(self, aislamiento):
        from motor.encargos import comprobacion

        (self.directorio / "arranque.json").write_text(json.dumps({"aislamiento": aislamiento}))
        with mock.patch.object(comprobacion, "_ejecutar_declaradas", return_value={}) as ejecutar:
            comprobacion.observar_comprobaciones(
                self.espacio, self.interno, self.directorio, self.directorio
            )
        return ejecutar.call_args.args[0][0]["argv"]

    def test_un_intento_de_wsl_declara_rutas_de_la_distro_y_el_host_las_recibe_de_windows(self):
        self.assertEqual(
            self.ejecutadas("wsl-bwrap"),
            ["python3", r"C:\Users\ehven\x\ops\e.py", r"--out=C:\t\o", "-v"],
        )
        self.assertEqual((self.interno / "comprobaciones.json").read_text(), self.declarado)

    def test_un_intento_de_windows_conserva_el_argv_tal_cual(self):
        self.assertEqual(self.ejecutadas("appcontainer"), self.ARGV)


class ArranqueTest(unittest.TestCase):
    """El wrapper lanza `wsl.exe` contenido en un Job; lo de la distro no pasa por ningun shell."""

    def test_el_spec_del_intento_se_convierte_en_el_guardian_dentro_de_wsl(self):
        from motor.encargos import arranque

        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            control = str(d)
            spec = {
                "aislamiento": "wsl-bwrap",
                "cmd": ["claude", "--print", r"Lee el pedido en C:\r\enc_x\intento\prompt.md."],
                "cwd": r"C:\Users\ehven\espacio\enc_x",
                "env": {"MOTOR_GENERACION_RUTA": r"C:\flota\g1", "MOTOR_GENERACION": "g1"},
                "jaula": jaula_wsl.jaula_de(ESPACIO_W, RUNTIME_W, agente="claude"),
            }
            opciones = {"cwd": spec["cwd"], "env": {"SECRETO": "x"}, "stdin": subprocess.DEVNULL}
            hecho = mock.Mock()
            with mock.patch("motor.plataforma.crear_proceso", return_value=hecho) as crear:
                self.assertIs(arranque.crear_agente(spec, d, opciones), hecho)
        argv, propiedad = crear.call_args.args
        nuevas = crear.call_args.kwargs
        self.assertEqual(argv[0], "wsl.exe")
        self.assertEqual(argv[1:6], ["--exec", "/usr/bin/python3", "-I", "-S", "-c"])
        guion = json.loads(argv[-1])
        self.assertEqual(guion["cmd"][-1], "Lee el pedido en /mnt/c/r/enc_x/intento/prompt.md.")
        self.assertEqual(guion["env"]["MOTOR_GENERACION_RUTA"], "/mnt/c/flota/g1")
        self.assertEqual(guion["control"], control)
        self.assertEqual(guion["jaula"]["espacio"], ESPACIO_L)
        self.assertEqual(propiedad.name, "COMANDO_CONTENIDO")
        self.assertNotIn("SECRETO", nuevas["env"])
        self.assertEqual(nuevas["cwd"], spec["cwd"])


class PedidoAlAgenteTest(unittest.TestCase):
    """El pedido que lee el agente de WSL lleva rutas de la distro; el de otro agente, no."""

    TEXTO = r"Todo lo interno va en C:\Users\ehven\espacio\enc_x\.interno y el buzon en C:\Users\ehven\espacio\enc_x\.interno\buzon\cliente.py."

    def setUp(self):
        from tests.test_bucle_instrumento import Banco, RECUPERADO

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(mock.patch.stopall)
        self.preparar = mock.patch("motor.encargos.ciclo.preparar_administrado").start()
        mock.patch(
            "motor.encargos.ciclo.lanzar_administrado",
            return_value={"pid": os.getpid(), "inicio": "ensayo"},
        ).start()
        mock.patch(
            "motor.flota.runtime.exigir_poteto",
            return_value=(Path("/tmp/poteto-mode/SKILL.md"), "digest-de-ensayo"),
        ).start()
        mock.patch("motor.encargos.aislamiento._en_windows", return_value=True).start()
        mock.patch("motor.encargos.aislamiento.sondear", return_value=RECUPERADO).start()
        mock.patch("motor.encargos.ciclo.prompt_responsable", return_value=self.TEXTO).start()
        self.banco = Banco(self._tmp.name, {"actual": RECUPERADO})
        self.addCleanup(self.banco.reg.close)
        (self.banco.espacio / "modelos.toml").write_text("modelos = {}\n")

    def lanzar(self, agente):
        self.banco.anotar(
            "ruta_agentes", {"agentes": [agente], "hora": self.banco.hora}, "ruta:ensayo"
        )
        self.banco.lanzador = lambda enc, prompt, espacio, sesion: (
            [agente, "--print", f"Lee el pedido en {prompt}"], {"agente": agente}
        )
        self.banco.barrer(2)
        prompts = list(self.banco.base.rglob("prompt.md"))
        self.assertEqual(len(prompts), 1, prompts)
        return prompts[0].read_text(encoding="utf-8")

    def test_claude_lee_su_pedido_con_rutas_de_la_distro(self):
        texto = self.lanzar("claude")
        self.assertIn("/mnt/c/Users/ehven/espacio/enc_x/.interno y el buzon en", texto)
        self.assertIn("/mnt/c/Users/ehven/espacio/enc_x/.interno/buzon/cliente.py.", texto)
        self.assertNotIn("C:\\Users", texto)
        self.preparar.assert_called_once()

    def test_las_observaciones_json_del_pedido_llegan_intactas_y_con_rutas_de_la_distro(self):
        """El prompt del revisor lleva las observaciones como json.dumps (h_d1dba93b): una ruta
        seguida de \\n o de \\" no puede perder el escape ni romper el JSON."""
        espacio = r"C:\Users\ehven\espacio\enc_x"
        observacion = {
            "ejecuciones": [
                {
                    "argv": ["python3", espacio + r"\tests\test_x.py"],
                    "stderr": 'Traceback\n  File "' + espacio + '\\tests\\test_x.py", line 3\n'
                    + espacio + "\\a.py:12:\terror ñ",
                }
            ]
        }
        mock.patch(
            "motor.encargos.ciclo.prompt_responsable",
            return_value="Observaciones:\n" + json.dumps(observacion, ensure_ascii=False, indent=2),
        ).start()
        texto = self.lanzar("claude")
        inicio = texto.index("Observaciones:\n") + len("Observaciones:\n")
        traducido, _ = json.JSONDecoder().raw_decode(texto[inicio:])
        base = "/mnt/c/Users/ehven/espacio/enc_x"
        self.assertEqual(
            traducido["ejecuciones"][0],
            {
                "argv": ["python3", base + "/tests/test_x.py"],
                "stderr": 'Traceback\n  File "' + base + '/tests/test_x.py", line 3\n'
                + base + "/a.py:12:\terror ñ",
            },
        )

    def test_otro_agente_conserva_las_rutas_de_windows(self):
        self.assertIn(r"C:\Users\ehven\espacio\enc_x\.interno\buzon", self.lanzar("codex"))


class DisponibleTest(unittest.TestCase):
    def setUp(self):
        jaula_wsl._CACHE_DISPONIBLE.clear()
        self.addCleanup(jaula_wsl._CACHE_DISPONIBLE.clear)

    def test_la_causa_de_la_distro_llega_y_se_recuerda_un_minuto(self):
        correr = mock.Mock(return_value=mock.Mock(stdout=json.dumps({"causa": "falta bwrap"})))
        hora = [0.0]

        def pregunta():
            return jaula_wsl.causa_no_disponible(
                "claude", "Ubuntu", correr=correr, ahora=lambda: hora[0]
            )

        self.assertEqual(pregunta(), "falta bwrap")
        hora[0] = 10.0
        self.assertEqual(pregunta(), "falta bwrap")
        self.assertEqual(correr.call_count, 1)
        hora[0] = 100.0
        pregunta()
        self.assertEqual(correr.call_count, 2)

    def test_sin_respuesta_de_wsl_la_causa_lo_dice(self):
        correr = mock.Mock(side_effect=OSError("no hay wsl.exe"))
        self.assertIn("wsl.exe", jaula_wsl.causa_no_disponible("claude", "", correr=correr))

    def test_agentes_con_wsl_se_juzgan_por_la_distro(self):
        from motor.encargos import agentes

        with (
            mock.patch("motor.encargos.aislamiento._en_windows", return_value=True),
            mock.patch.object(jaula_wsl, "causa_no_disponible", return_value="") as causa,
        ):
            self.assertTrue(agentes._disponible("claude"))
        causa.assert_called_once_with("claude")
        with (
            mock.patch("motor.encargos.aislamiento._en_windows", return_value=True),
            mock.patch.object(jaula_wsl, "causa_no_disponible", return_value="falta bwrap"),
        ):
            self.assertFalse(agentes._disponible("claude"))


@unittest.skipUnless(LINUX, "el guardián corre en Linux")
@unittest.skipIf(CAUSA and not EXIGIDA, CAUSA)
class GuardianTest(unittest.TestCase):
    """El guardián real con la jaula real, detrás de un doble de `wsl.exe`."""

    def setUp(self):
        self.assertEqual(CAUSA, "")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name).resolve()
        self.espacio = base / "espacio" / "enc_x"
        self.runtime = base / "runtime" / "enc_x" / "intento"
        self.control = base / "ejecucion"
        for d in (self.espacio, self.runtime, self.control):
            d.mkdir(parents=True)
        bin_ = base / "bin"
        bin_.mkdir()
        doble = bin_ / "wsl.exe"
        doble.write_text(
            '#!/bin/sh\n[ "$1" = "-d" ] && shift 2\n[ "$1" = "--exec" ] && shift\nexec "$@"\n'
        )
        doble.chmod(0o755)
        entorno = mock.patch.dict(os.environ, {"PATH": f"{bin_}:{os.environ['PATH']}"})
        entorno.start()
        self.addCleanup(entorno.stop)
        self.procesos = []
        self.addCleanup(self.limpiar)

    def limpiar(self):
        for p in self.procesos:
            if p.poll() is None:
                p.kill()
            p.wait()
        control = jaula_wsl.control_de(self.control)
        if control:
            jaula_linux.cerrar_identidad(control)

    def lanzar(self, cmd, *, lectura=False, plazo=None, padre=None):
        jaula = jaula_wsl.jaula_de(self.espacio, self.runtime, lectura=lectura)
        argv = jaula_wsl.argv_guardian(
            jaula, cmd, {}, control=self.control, cwd=self.espacio, plazo=plazo
        )
        opciones = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, env=jaula_wsl.entorno_windows())
        if padre:
            p = subprocess.Popen(
                ["/usr/bin/python3", "-c", padre, json.dumps(argv)], **opciones
            )
        else:
            p = subprocess.Popen(argv, **opciones)
        self.procesos.append(p)
        return p

    def esperar(self, condicion, plazo=15.0):
        fin = time.monotonic() + plazo
        while time.monotonic() < fin:
            valor = condicion()
            if valor:
                return valor
            time.sleep(0.05)
        self.fail("la condicion no se cumplio a tiempo")

    def activo(self):
        control = jaula_wsl.control_de(self.control)
        return control if control and control.get("estado") == "activo" else None

    def procesos_con(self, texto):
        encontrados = []
        for nombre in os.listdir("/proc"):
            if nombre.isdigit():
                try:
                    linea = Path(f"/proc/{nombre}/cmdline").read_bytes().replace(b"\0", b" ")
                except OSError:
                    continue
                if texto.encode() in linea and int(nombre) != os.getpid():
                    encontrados.append(int(nombre))
        return encontrados

    def test_la_salida_y_el_codigo_del_agente_llegan_y_el_control_queda_cerrado(self):
        p = self.lanzar(["/bin/sh", "-c", "echo hola; exit 7"])
        salida, error = p.communicate(timeout=30)
        self.assertEqual((p.returncode, salida.strip()), (7, "hola"), error)
        self.assertEqual(jaula_wsl.control_de(self.control)["estado"], "cerrado")

    def test_el_agente_escribe_en_el_espacio_y_el_revisor_no(self):
        escribe = ["/bin/sh", "-c", f"echo x > {self.espacio}/f && echo ok"]
        salida, error = (p := self.lanzar(escribe)).communicate(timeout=30)
        self.assertEqual((p.returncode, salida.strip()), (0, "ok"), error)
        self.assertEqual((self.espacio / "f").read_text().strip(), "x")
        p = self.lanzar(["/bin/sh", "-c", f"echo y > {self.espacio}/g"], lectura=True)
        p.communicate(timeout=30)
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse((self.espacio / "g").exists())

    def dormilones(self, *segundos):
        """Un agente que deja `sleep <n>` en segundo plano. El script va en el espacio para que
        la linea de comandos de esos `sleep` sea lo unico que lleva `sleep <n>` en todo /proc
        (el spec del guardian solo nombra el script)."""
        guion = self.espacio / "dormilones.sh"
        guion.write_text(
            "#!/bin/sh\n" + "".join(f"sleep {n} &\n" for n in segundos) + "wait\n"
        )
        guion.chmod(0o755)
        return [str(guion)]

    def vivos(self, *segundos):
        return [x for n in segundos for x in self.procesos_con(f"sleep {n}")]

    def test_el_plazo_cierra_todo_y_sale_con_124(self):
        p = self.lanzar(self.dormilones(4311), plazo=3)
        self.esperar(lambda: self.vivos(4311))
        p.communicate(timeout=30)
        self.assertEqual(p.returncode, jaula_linux.SALIDA_PARADA)
        self.assertEqual(self.vivos(4311), [])

    def test_sigterm_al_guardian_no_deja_nada_vivo_en_la_distro(self):
        p = self.lanzar(self.dormilones(4312, 4313))
        self.esperar(lambda: self.activo() and self.vivos(4312) and self.vivos(4313))
        p.send_signal(signal.SIGTERM)
        p.communicate(timeout=30)
        self.assertEqual(p.returncode, jaula_linux.SALIDA_PARADA)
        self.assertEqual(self.vivos(4312, 4313), [])

    def test_si_wsl_exe_muere_el_guardian_cierra_la_jaula(self):
        padre = (
            "import json,subprocess,sys,time\n"
            "p=subprocess.Popen(json.loads(sys.argv[1]));print(p.pid,flush=True);time.sleep(600)\n"
        )
        p = self.lanzar(self.dormilones(4314), padre=padre)
        self.esperar(lambda: self.activo() and self.vivos(4314))
        p.kill()
        p.wait()
        self.esperar(lambda: (jaula_wsl.control_de(self.control) or {}).get("estado") == "cerrado")
        self.assertEqual(self.vivos(4314), [])

    def test_parar_desde_el_supervisor_mata_la_jaula_y_al_guardian(self):
        p = self.lanzar(self.dormilones(4315, 4316))
        control = self.esperar(lambda: self.activo() and self.vivos(4315) and self.vivos(4316) and self.activo())
        self.assertEqual(jaula_wsl.estado_en_distro(self.control), "vivo")
        self.assertEqual(jaula_wsl.cerrar_en_distro(self.control), "ausente")
        p.wait(timeout=15)
        self.assertEqual(self.vivos(4315, 4316), [])
        self.assertFalse(jaula_linux.vivo_identidad(control))
        self.assertEqual(jaula_wsl.estado_en_distro(self.control), "ausente")
        self.assertEqual(jaula_wsl.cerrar_en_distro(self.control), "ausente")

    def test_sin_el_ejecutable_de_claude_la_causa_sale_por_stderr(self):
        jaula = jaula_wsl.jaula_de(self.espacio, self.runtime, agente="claude")
        argv = jaula_wsl.argv_guardian(jaula, ["claude", "--print"], {}, control=self.control)
        entorno = {**jaula_wsl.entorno_windows(), "HOME": str(self.runtime),
                   "MOTOR_WSL_CLAUDE": "/no/existe"}
        p = subprocess.run(argv, capture_output=True, text=True, timeout=30,
                           stdin=subprocess.DEVNULL, env=entorno)
        self.assertEqual(p.returncode, jaula_linux.SALIDA_SIN_EJECUTABLE)
        self.assertIn("Claude Code", p.stderr)


if __name__ == "__main__":
    unittest.main()
