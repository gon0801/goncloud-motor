"""Antigravity (agy) como agente del motor, con un CLI falso que imprime la
muestra de stream-json medida el 2-nov."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from motor.capacidades_maquina import _agentes
from motor.encargos import agentes
from motor.encargos.aislamiento import _entorno, aislar
from motor.encargos.arranque import Observacion, SalidaRuntime
from motor.encargos.catalogo_agentes import ADAPTADORES, agente_del_modelo, cliente_para
from motor.encargos.sesion import herramienta_en_curso
from motor.plataforma.windows import resolve_shim

CONVERSACION = "73d78444-6b28-4f9e-922e-81d33fca4288"
MUESTRA = """\
{"event": "init", "conversation_id": "73d78444-6b28-4f9e-922e-81d33fca4288", "init": {"model": "gemini-4-argon-low", "cwd": "<espacio>", "tools": ["ask_custom_permission", "ask_permission", "ask_question", "browser_click_element", "browser_drag_pixel_to_pixel", "browser_get_dom", "browser_get_network_request", "browser_input", "browser_list_network_requests", "browser_mouse_down", "browser_mouse_up", "browser_move_mouse", "browser_press_key", "browser_refresh_page", "browser_resize_window", "browser_scroll", "browser_scroll_dom", "browser_select_option", "browser_subagent", "call_mcp_tool", "capture_browser_console_logs", "capture_browser_screenshot", "click_browser_pixel", "command_status", "define_subagent", "delete_knowledge", "execute_browser_javascript", "find_by_name", "finish", "generate_image", "grep_search", "invoke_subagent", "list_browser_pages", "list_dir", "list_permissions", "list_plugin_accounts", "list_resources", "manage_inbox", "manage_subagents", "manage_task", "multi_replace_file_content", "notebook_edit", "notebook_execution", "open_browser_url", "read_browser_page", "read_resource", "read_url_content", "replace_file_content", "run_command", "run_workflow", "schedule", "search_marketplace", "search_web", "sed_file", "send_command_input", "send_message", "view_file", "wait", "wait_5_seconds", "write_to_file"], "permission_mode": "request-review"}}
{"event": "step_update", "step_update": {"conversation_id": "73d78444-6b28-4f9e-922e-81d33fca4288", "step_index": 0, "state": "DONE", "step_type": "user_input"}}
{"event": "step_update", "step_update": {"conversation_id": "73d78444-6b28-4f9e-922e-81d33fca4288", "step_index": 1, "state": "ACTIVE", "step_type": "agent_response", "text_delta": "hola"}}
{"event": "step_update", "step_update": {"conversation_id": "73d78444-6b28-4f9e-922e-81d33fca4288", "step_index": 1, "state": "DONE", "step_type": "agent_response", "text_delta": "\\n", "duration_seconds": 3.566657, "usage": {"input_tokens": 6865, "output_tokens": 5, "thinking_tokens": 0, "cache_read_tokens": 0, "total_tokens": 6870}}}
{"event": "result", "result": {"conversation_id": "73d78444-6b28-4f9e-922e-81d33fca4288", "status": "SUCCESS", "response": "hola\\n", "duration_seconds": 3.616216, "num_turns": 1, "usage": {"input_tokens": 6865, "output_tokens": 5, "thinking_tokens": 0, "cache_read_tokens": 0, "total_tokens": 6870}}}
"""
TRAS_EL_TURNO = (
    "schedule",
    "wait",
    "wait_5_seconds",
    "manage_task",
    "send_command_input",
    "command_status",
    "run_workflow",
    "manage_inbox",
)


def _enc(rol: str, sesion: str = "") -> SimpleNamespace:
    return SimpleNamespace(rol=rol, sesion_responsable=sesion, sesion_revisor=sesion)


def _argv(rol: str, modo: str, modelo: str = "") -> list[str]:
    lanzador = agentes._lanzador("antigravity", modelo, modo)
    return lanzador(_enc(rol, CONVERSACION), Path("/p/prompt.md"), Path("/e"), "sesion-motor")


def _aislado(
    tmp: Path, rol: str = "responsable", modo: str = "nueva", modelo: str = ""
):
    espacio = tmp / "espacio" / "enc_x"
    espacio.mkdir(parents=True, exist_ok=True)
    runtime = tmp / "runtime"
    comando = aislar(
        _argv(rol, modo, modelo),
        espacio,
        runtime,
        suscripcion=True,
        agente="antigravity",
    )
    return comando, espacio, runtime


def _hooks_tras_el_turno(comando) -> tuple[Path, dict]:
    carpeta = Path(comando.argv[comando.argv.index("--add-dir") + 1])
    return carpeta, json.loads(
        (carpeta / ".agents" / "hooks.json").read_text(encoding="utf-8")
    )


class Catalogo(unittest.TestCase):
    def test_prefijo_modelo_y_cliente(self):
        adaptador = ADAPTADORES["antigravity"]
        self.assertEqual(adaptador.prefijos, ("antigravity/",))
        self.assertEqual(adaptador.modelo, "antigravity/gemini-4-argon-high")
        self.assertEqual(
            agente_del_modelo("antigravity/gemini-4-argon-high"), "antigravity"
        )
        self.assertEqual(cliente_para("antigravity"), "agy")

    def test_en_windows_el_nombre_agy_resuelve_agy_exe(self):
        with mock.patch("motor.encargos.catalogo_agentes.os.name", "nt"):
            self.assertEqual(cliente_para("antigravity"), "agy")
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "agy.exe"
            exe.write_bytes(b"")
            self.assertEqual(
                resolve_shim("agy", tmp, ".COM;.EXE;.BAT;.CMD"), [str(exe)]
            )


class Argv(unittest.TestCase):
    def test_sesion_nueva_y_reanudada(self):
        for rol in ("responsable", "revisor"):
            nueva = _argv(rol, "nueva", "antigravity/gemini-4-argon-low")
            self.assertEqual(nueva[0], "agy")
            self.assertTrue(nueva[nueva.index("--print") + 1].startswith("Lee el pedido"))
            self.assertEqual(nueva[nueva.index("--model") + 1], "gemini-4-argon-low")
            self.assertEqual(
                nueva[nueva.index("--output-format") + 1], "stream-json"
            )
            self.assertNotIn("--conversation", nueva)
            self.assertNotIn("--continue", nueva)
            self.assertNotIn("-c", nueva)
            reanudada = _argv(rol, "reanudada")
            self.assertEqual(
                reanudada[reanudada.index("--model") + 1], "gemini-4-argon-high"
            )
            self.assertEqual(
                reanudada[reanudada.index("--conversation") + 1], CONVERSACION
            )
            self.assertNotIn("--continue", reanudada)

    def test_sin_id_previo_no_hay_lanzador_de_reanudacion(self):
        lanzador = agentes._lanzador("antigravity", "", "reconstruida")
        argv = lanzador(_enc("responsable", ""), Path("/p/prompt.md"), Path("/e"), "s")
        self.assertNotIn("--conversation", argv)


class EntornoYHerramientas(unittest.TestCase):
    def test_el_entorno_conserva_la_sesion_del_usuario(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "runtime"
            home = Path(tmp) / "home"
            original = {
                "HOME": "/Users/gon",
                "USERPROFILE": r"C:\Users\gon",
                "XDG_CONFIG_HOME": "/Users/gon/.config",
                "XDG_DATA_HOME": "/Users/gon/.local/share",
                "XDG_CACHE_HOME": "/Users/gon/.cache",
                "XDG_STATE_HOME": "/Users/gon/.local/state",
            }
            with mock.patch.dict(os.environ, original, clear=False):
                with mock.patch("motor.encargos.agentes.Path.home", return_value=home):
                    env = _entorno(runtime, True, ["agy", "--print"], agente="antigravity")
                base = dict(os.environ)
            for clave, valor in original.items():
                self.assertEqual(env.get(clave), valor, clave)
            self.assertFalse((runtime / ".gemini").exists())
            self.assertFalse((runtime / "antigravity-cli").exists())
            for clave in (
                "CODEX_HOME",
                "CLAUDE_CONFIG_DIR",
                "KIMI_CODE_HOME",
                "MUSE_AUTH_PATH",
            ):
                self.assertEqual(env.get(clave), base.get(clave), clave)

    def test_la_cache_no_habilita_y_el_token_no_se_reescribe(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            with mock.patch("motor.encargos.agentes.resolver_binario", return_value="/falso/agy"):
                with mock.patch("motor.encargos.agentes.Path.home", return_value=home):
                    self.assertFalse(agentes._disponible("antigravity"))
                    sesion = home / ".gemini" / "antigravity-cli"
                    cache = sesion / "cache"
                    cache.mkdir(parents=True)
                    (cache / "CHANGELOG.md").write_text("changelog", encoding="utf-8")
                    self.assertFalse(agentes._disponible("antigravity"))
                    marca = b'{"token":"no-leer"}'
                    (sesion / "secreto.json").write_bytes(marca)
                    self.assertFalse(agentes._disponible("antigravity"))
                    self.assertEqual((sesion / "secreto.json").read_bytes(), marca)
                    token = sesion / "antigravity-oauth-token"
                    token.write_bytes(b"")
                    self.assertFalse(agentes._disponible("antigravity"))
                    token.write_bytes(b"marca")
                    self.assertTrue(agentes._disponible("antigravity"))
                    self.assertEqual(token.read_bytes(), b"marca")
            with mock.patch("motor.encargos.agentes.resolver_binario", return_value=None):
                with mock.patch("motor.encargos.agentes.Path.home", return_value=home):
                    self.assertFalse(agentes._disponible("antigravity"))

    def test_la_clave_de_api_no_es_la_suscripcion(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cli = home / ".gemini" / "antigravity-cli"
            cli.mkdir(parents=True)
            (cli / "settings.json").write_text(
                '{"modelProvider": "gemini"}', encoding="utf-8"
            )
            self.assertFalse((cli / "antigravity-oauth-token").exists())

            def llavero_ausente(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 44, b"", b"")

            entorno = {
                "HOME": str(home),
                "USERPROFILE": str(home),
                "GEMINI_API_KEY": "clave-de-prueba",
            }

            def which(nombre):
                return "/falso/agy" if nombre == "agy" else None

            with mock.patch.dict(os.environ, entorno):
                with mock.patch("subprocess.run", side_effect=llavero_ausente):
                    with mock.patch("motor.encargos.agentes.Path.home", return_value=home):
                        with mock.patch(
                            "motor.encargos.agentes.resolver_binario",
                            return_value="/falso/agy",
                        ):
                            self.assertFalse(agentes.sesion_antigravity_presente())
                            self.assertFalse(agentes._disponible("antigravity"))
                        filas = {f["agente"]: f for f in _agentes(which)}
                    self.assertIn("sin sesión", filas["antigravity"]["detalle"])
                    self.assertNotIn("con sesión", filas["antigravity"]["detalle"])
                    self.assertTrue((os.environ.get("GEMINI_API_KEY") or "").strip())

    def test_el_llavero_cuenta_solo_en_el_home_del_proceso(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cache = home / ".gemini" / "antigravity-cli" / "cache"
            cache.mkdir(parents=True)
            (cache / "CHANGELOG.md").write_text("changelog", encoding="utf-8")
            self.assertFalse(agentes.sesion_antigravity_presente(home))

            def ausente(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 44, b"", b"")

            entorno = {"HOME": str(home), "USERPROFILE": str(home)}
            with mock.patch.dict(os.environ, entorno):
                with mock.patch("subprocess.run", side_effect=ausente):
                    self.assertFalse(agentes.sesion_antigravity_presente(home))

            solo = Path(tmp) / "solo-llavero"
            vistos = {}

            def presente(argv, **kwargs):
                vistos["argv"] = list(argv)
                vistos["kwargs"] = kwargs
                if argv and argv[0] == "cmdkey":
                    return subprocess.CompletedProcess(
                        argv, 0, "gemini:antigravity".encode("utf-16-le"), b""
                    )
                return subprocess.CompletedProcess(argv, 0, b"", b"")

            with mock.patch.dict(
                os.environ, {"HOME": str(solo), "USERPROFILE": str(solo)}
            ):
                with mock.patch("subprocess.run", side_effect=presente):
                    self.assertTrue(agentes.sesion_antigravity_presente(solo))
            argv = vistos["argv"]
            self.assertNotIn("-w", argv)
            self.assertNotIn("-g", argv)
            self.assertNotIn("--all", argv)
            if sys.platform == "darwin":
                self.assertEqual(
                    argv,
                    [
                        "security",
                        "find-generic-password",
                        "-s",
                        "gemini",
                        "-a",
                        "antigravity",
                    ],
                )
                self.assertIs(vistos["kwargs"]["stdout"], subprocess.DEVNULL)
                self.assertIs(vistos["kwargs"]["stderr"], subprocess.DEVNULL)
            elif sys.platform == "win32":
                self.assertEqual(argv, ["cmdkey", "/list"])
            else:
                self.assertEqual(
                    argv,
                    [
                        "secret-tool",
                        "lookup",
                        "service",
                        "gemini",
                        "username",
                        "antigravity",
                    ],
                )
                self.assertIs(vistos["kwargs"]["stdout"], subprocess.DEVNULL)
                self.assertIs(vistos["kwargs"]["stderr"], subprocess.DEVNULL)

    def test_linux_y_windows_no_confunden_el_codigo_de_salida_con_sesion(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            entorno = {"HOME": str(home), "USERPROFILE": str(home)}

            def vacio(argv, **kwargs):
                if argv and argv[0] == "cmdkey":
                    return subprocess.CompletedProcess(
                        argv, 0, b"Target: LegacyGeneric:target=otro\r\n", b""
                    )
                return subprocess.CompletedProcess(argv, 0, b"", b"")

            def sin_item(argv, **kwargs):
                # `secret-tool lookup` sale 1 cuando no hay ítem.
                return subprocess.CompletedProcess(argv, 1, b"", b"")

            with mock.patch.dict(os.environ, entorno):
                with mock.patch("sys.platform", "linux"):
                    with mock.patch("subprocess.run", side_effect=sin_item):
                        self.assertFalse(agentes.sesion_antigravity_presente(home))
                with mock.patch("sys.platform", "win32"):
                    with mock.patch("subprocess.run", side_effect=vacio):
                        self.assertFalse(agentes.sesion_antigravity_presente(home))

            def hit(argv, **kwargs):
                # lookup imprime el secreto por stdout; el código lo descarta.
                return subprocess.CompletedProcess(argv, 0, b"SECRETO-FALSO\n", b"")

            with mock.patch.dict(os.environ, entorno):
                with mock.patch("sys.platform", "linux"):
                    with mock.patch("subprocess.run", side_effect=hit):
                        self.assertTrue(agentes.sesion_antigravity_presente(home))

            def hit_windows(argv, **kwargs):
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    "Target: LegacyGeneric:target=gemini:antigravity\r\n".encode(
                        "utf-16-le"
                    ),
                    b"",
                )

            with mock.patch.dict(os.environ, entorno):
                with mock.patch("sys.platform", "win32"):
                    with mock.patch("subprocess.run", side_effect=hit_windows):
                        self.assertTrue(agentes.sesion_antigravity_presente(home))

    def test_el_llavero_de_linux_usa_el_codigo_de_salida_sin_leer_el_secreto(
        self,
    ):
        """`secret-tool lookup` sale 0 si el ítem existe, sin importar el
        reparto de streams. El doble manda el secreto por stdout y el código
        lo descarta en /dev/null: solo cuenta el código de salida."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            bin_ = Path(tmp) / "bin"
            bin_.mkdir()
            falso = bin_ / "secret-tool"
            falso.write_text(
                "#!/bin/sh\n"
                "echo 'SECRETO-FALSO'\n"
                "exit 0\n",
                encoding="utf-8",
            )
            falso.chmod(0o755)
            entorno = {
                "HOME": str(home),
                "USERPROFILE": str(home),
                "PATH": f"{bin_}{os.pathsep}{os.environ.get('PATH', '')}",
            }
            with mock.patch.dict(os.environ, entorno):
                with mock.patch("sys.platform", "linux"):
                    self.assertTrue(agentes.sesion_antigravity_presente(home))

            falso.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            falso.chmod(0o755)
            with mock.patch.dict(os.environ, entorno):
                with mock.patch("sys.platform", "linux"):
                    self.assertFalse(agentes.sesion_antigravity_presente(home))


class LanzamientoSinAgenteDelTurno(unittest.TestCase):
    def test_el_argv_no_nombra_el_agente_ni_excluye_componentes(self):
        for rol in ("responsable", "revisor"):
            for modo in ("nueva", "reanudada", "reconstruida"):
                with self.subTest(rol=rol, modo=modo):
                    argv = _argv(rol, modo, "antigravity/gemini-4-argon-low")
                    self.assertNotIn("--agent", argv)
                    self.assertNotIn("motor-turno", argv)
                    self.assertFalse(
                        any("excludeDefaultComponents" in parte for parte in argv)
                    )
                    self.assertIn("--dangerously-skip-permissions", argv)

    def test_no_escribe_en_la_configuracion_real_del_usuario(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            home = base / "home"
            home.mkdir()
            with mock.patch("pathlib.Path.home", return_value=home):
                comando, espacio, _ = _aislado(base)
            self.assertEqual(comando.argv[:1], ["agy"])
            self.assertFalse((home / ".gemini" / "config").exists())
            self.assertFalse((espacio / ".gemini").exists())

    def test_el_hook_del_runtime_bloquea_solo_las_herramientas_tras_el_turno(self):
        with tempfile.TemporaryDirectory() as tmp:
            comando, espacio, runtime = _aislado(Path(tmp))
            carpeta, hooks = _hooks_tras_el_turno(comando)
            self.assertTrue(carpeta.is_relative_to(runtime))
            self.assertFalse(carpeta.is_relative_to(espacio))
            entrada = hooks["motor-turno-sin-segundo-plano"]["PreToolUse"][0]
            patron = entrada["matcher"]
            for nombre in TRAS_EL_TURNO:
                self.assertIsNotNone(re.fullmatch(patron, nombre), nombre)
            for nombre in (
                "view_file",
                "run_command",
                "search_web",
                "browser_click_element",
                "call_mcp_tool",
            ):
                self.assertIsNone(re.fullmatch(patron, nombre), nombre)

    @unittest.skipUnless(os.name != "nt", "el guion del hook se prueba en POSIX")
    def test_el_hook_responde_deny(self):
        with tempfile.TemporaryDirectory() as tmp:
            comando, _, _ = _aislado(Path(tmp))
            _, hooks = _hooks_tras_el_turno(comando)
            orden = hooks["motor-turno-sin-segundo-plano"]["PreToolUse"][0]["hooks"][
                0
            ]["command"]
            for nombre in TRAS_EL_TURNO:
                with self.subTest(nombre=nombre):
                    hecho = subprocess.run(
                        orden,
                        shell=True,
                        input=json.dumps({"toolCall": {"name": nombre}}),
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(hecho.returncode, 0, hecho.stderr)
                    self.assertEqual(json.loads(hecho.stdout)["decision"], "deny")

    def test_el_hook_es_idempotente_y_no_deja_temporales(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            primero, espacio, runtime = _aislado(base)
            carpeta, _ = _hooks_tras_el_turno(primero)
            archivo = carpeta / ".agents" / "hooks.json"
            antes = archivo.read_text(encoding="utf-8")
            segundo = aislar(
                _argv("responsable", "nueva"),
                espacio,
                runtime,
                suscripcion=True,
                agente="antigravity",
            )
            self.assertEqual(
                (carpeta / ".agents" / "hooks.json").read_text(encoding="utf-8"),
                antes,
            )
            self.assertEqual(segundo.argv.count("--add-dir"), 1)
            self.assertEqual(list(carpeta.rglob("*.tmp")), [])


class StreamJson(unittest.TestCase):
    def test_el_falso_agy_informa_avance_y_cierre(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            falso = raiz / "agy"
            falso.write_text(
                "#!/bin/sh\ncat <<'EOF'\n" + MUESTRA + "EOF\n", encoding="utf-8"
            )
            falso.chmod(falso.stat().st_mode | stat.S_IEXEC)
            corrida = subprocess.run(
                [str(falso)], capture_output=True, text=True, check=True
            )
            directorio = raiz / "run"
            directorio.mkdir()
            observacion = Observacion(directorio, "antigravity")
            salida = SalidaRuntime("antigravity")
            for linea in corrida.stdout.splitlines():
                if not linea.strip():
                    continue
                data = json.loads(linea)
                observacion.evento(data)
                salida.evento(data)
            self.assertEqual(salida.sesion_runtime, CONVERSACION)
            self.assertEqual(salida.terminal, "")
            self.assertEqual(salida.respuesta, "hola\n")
            self.assertGreater(observacion.secuencia, 0)
            self.assertFalse(observacion.modelo_en_curso)
            self.assertFalse(observacion.abiertas)
            self.assertFalse(herramienta_en_curso(directorio))

    def test_un_result_distinto_de_success_usa_la_respuesta(self):
        salida = SalidaRuntime("antigravity")
        salida.evento(
            {
                "event": "result",
                "result": {
                    "status": "ERROR",
                    "response": "se corto el turno",
                    "conversation_id": "abc",
                },
            }
        )
        self.assertEqual(salida.sesion_runtime, "abc")
        self.assertIn("se corto el turno", salida.terminal)

    def test_el_init_de_agy_deja_ver_las_herramientas_tras_el_turno(self):
        with tempfile.TemporaryDirectory() as tmp:
            directorio = Path(tmp)
            observacion = Observacion(directorio, "antigravity")
            observacion.evento(
                {
                    "event": "init",
                    "conversation_id": CONVERSACION,
                    "init": {
                        "tools": ["view_file", "wait", "schedule", "manage_task"]
                    },
                }
            )
            datos = json.loads(
                (directorio / "herramienta.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                datos["herramientas_tras_el_turno"],
                ["manage_task", "schedule", "wait"],
            )
            self.assertIn("init con herramientas tras el turno", datos["hecho"])
            limpia = Observacion(directorio, "antigravity")
            limpia.evento(
                {
                    "event": "init",
                    "conversation_id": CONVERSACION,
                    "init": {"tools": ["view_file", "run_command", "finish"]},
                }
            )
            datos = json.loads(
                (directorio / "herramienta.json").read_text(encoding="utf-8")
            )
            self.assertEqual(datos["herramientas_tras_el_turno"], [])
            self.assertEqual(datos["hecho"], "")

    def test_una_herramienta_activa_mantiene_el_turno(self):
        with tempfile.TemporaryDirectory() as tmp:
            directorio = Path(tmp)
            observacion = Observacion(directorio, "antigravity")
            observacion.evento(
                {
                    "event": "step_update",
                    "step_update": {
                        "step_index": 2,
                        "state": "ACTIVE",
                        "step_type": "tool",
                    },
                }
            )
            self.assertTrue(observacion.modelo_en_curso)
            self.assertTrue(observacion.abiertas)
            self.assertTrue(herramienta_en_curso(directorio))


class Capacidades(unittest.TestCase):
    def test_con_y_sin_agy(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cli = home / ".gemini" / "antigravity-cli"
            cache = cli / "cache"
            cache.mkdir(parents=True)
            (cache / "CHANGELOG.md").write_text("changelog", encoding="utf-8")

            def which(nombre):
                if nombre == "agy":
                    return "/falso/bin/agy"
                return None

            with mock.patch("motor.encargos.agentes.Path.home", return_value=home):
                filas = {f["agente"]: f for f in _agentes(which)}
            self.assertEqual(filas["antigravity"]["estado"], "ok")
            self.assertIn("/falso/bin/agy", filas["antigravity"]["detalle"])
            self.assertIn("sin sesión", filas["antigravity"]["detalle"])
            self.assertNotIn("con sesión", filas["antigravity"]["detalle"])
            (cli / "antigravity-oauth-token").write_bytes(b"marca")
            with mock.patch("motor.encargos.agentes.Path.home", return_value=home):
                filas = {f["agente"]: f for f in _agentes(which)}
            self.assertEqual(filas["antigravity"]["estado"], "ok")
            self.assertIn("con sesión", filas["antigravity"]["detalle"])
            with mock.patch(
                "motor.encargos.agentes.Path.home", return_value=home / "vacio"
            ):
                filas = {f["agente"]: f for f in _agentes(which)}
            self.assertEqual(filas["antigravity"]["estado"], "ok")
            self.assertIn("sin sesión", filas["antigravity"]["detalle"])
            filas = {f["agente"]: f for f in _agentes(lambda nombre: None)}
            self.assertEqual(filas["antigravity"]["estado"], "falta")
            self.assertIn("agy", filas["antigravity"]["detalle"])

    def test_sin_agy_en_la_generacion_sigue_el_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            generacion = Path(tmp) / "generacion"
            generacion.mkdir()
            (generacion / "runtime.json").write_text(
                '{"lanzadores": {}}', encoding="utf-8"
            )

            def which(nombre):
                return {
                    "agy": "/usuario/bin/agy",
                    "muse": "/usuario/bin/muse",
                }.get(nombre)

            self.assertEqual(
                agentes.resolver_lanzador("antigravity", generacion, which=which),
                ["/usuario/bin/agy"],
            )
            self.assertEqual(
                agentes.resolver_lanzador("muse", generacion, which=which),
                ["/usuario/bin/muse"],
            )
            self.assertIsNone(
                agentes.resolver_lanzador("claude", generacion, which=which)
            )
            with mock.patch(
                "motor.encargos.agentes.Path.home", return_value=Path(tmp) / "vacio"
            ):
                filas = {f["agente"]: f for f in _agentes(which, generacion)}
            self.assertEqual(filas["antigravity"]["estado"], "ok")
            self.assertIn("/usuario/bin/agy", filas["antigravity"]["detalle"])
            self.assertIn("sin sesión", filas["antigravity"]["detalle"])
            self.assertNotIn("no está en la generación", filas["antigravity"]["detalle"])

    def test_el_lanzador_de_la_generacion_manda_y_el_roto_no_cae_al_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            generacion = Path(tmp) / "generacion"
            binario = generacion / "bin" / "agy"
            binario.parent.mkdir(parents=True)
            binario.write_text("#!/bin/sh\nprintf 'agy 9\\n'\n", encoding="utf-8")
            binario.chmod(0o755)
            (generacion / "runtime.json").write_text(
                json.dumps({"lanzadores": {"agy": ["bin/agy"]}}),
                encoding="utf-8",
            )

            def which(nombre):
                return "/usuario/bin/agy" if nombre == "agy" else None

            self.assertEqual(
                agentes.resolver_lanzador("antigravity", generacion, which=which),
                [str(binario.resolve())],
            )
            (generacion / "runtime.json").write_text(
                json.dumps({"lanzadores": {"agy": ["bin/ausente"]}}),
                encoding="utf-8",
            )
            self.assertIsNone(
                agentes.resolver_lanzador("antigravity", generacion, which=which)
            )


if __name__ == "__main__":
    unittest.main()
