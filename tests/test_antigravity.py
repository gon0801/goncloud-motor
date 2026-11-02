"""Antigravity (agy) como agente del motor, con un CLI falso que imprime la
muestra de stream-json medida el 2-nov."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from motor.capacidades_maquina import _agentes
from motor.encargos import agentes
from motor.encargos.aislamiento import _entorno
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


def _herramientas(markdown: str) -> list[str]:
    encontradas = []
    en_lista = False
    for linea in markdown.splitlines():
        if linea.strip() == "tools:":
            en_lista = True
            continue
        if en_lista:
            if linea.startswith("  - "):
                encontradas.append(linea[4:].strip())
            elif linea.strip():
                break
    return encontradas


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

    def test_el_agente_del_turno_no_ofrece_herramientas_de_despues(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            runtime = Path(tmp) / "runtime"
            with mock.patch("motor.encargos.agentes.Path.home", return_value=home):
                _entorno(runtime, True, ["agy", "--print"], agente="antigravity")
            markdown = (home / ".gemini" / "config" / "agents" / "motor-turno.md").read_text(
                encoding="utf-8"
            )
            permitidas = _herramientas(markdown)
            for nombre in TRAS_EL_TURNO:
                self.assertNotIn(nombre, permitidas)
            for nombre in ("view_file", "run_command", "finish", "write_to_file"):
                self.assertIn(nombre, permitidas)
            self.assertIn("excludeDefaultComponents: true", markdown)
            self.assertIn("mainAgent: true", markdown)
            argv = _argv("responsable", "nueva")
            self.assertEqual(argv[argv.index("--agent") + 1], "motor-turno")
            self.assertIn("--dangerously-skip-permissions", argv)
            self.assertNotIn("--sandbox", argv)
            self.assertNotIn("--mode", argv)

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

    def test_la_clave_de_api_exige_proveedor_y_valor(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            cli = home / ".gemini" / "antigravity-cli"
            cli.mkdir(parents=True)
            (cli / "settings.json").write_text(
                '{"modelProvider": "gemini", "theme": "dark"}',
                encoding="utf-8",
            )
            for valor in ("", "   "):
                with mock.patch.dict(os.environ, {"GEMINI_API_KEY": valor}):
                    self.assertFalse(agentes.sesion_antigravity_presente(home))
            with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "clave-de-prueba"}):
                self.assertTrue(agentes.sesion_antigravity_presente(home))
            (cli / "settings.json").write_text(
                '{"modelProvider": "vertex"}', encoding="utf-8"
            )
            with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "clave-de-prueba"}):
                self.assertFalse(agentes.sesion_antigravity_presente(home))

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
                if argv and argv[0] == "secret-tool":
                    return subprocess.CompletedProcess(
                        argv,
                        0,
                        b"attribute.service = gemini\nattribute.username = antigravity\n",
                        b"",
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
            self.assertNotIn("lookup", argv)
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
                        "search",
                        "service",
                        "gemini",
                        "username",
                        "antigravity",
                    ],
                )

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

            with mock.patch.dict(os.environ, entorno):
                with mock.patch("sys.platform", "linux"):
                    with mock.patch("subprocess.run", side_effect=vacio):
                        self.assertFalse(agentes.sesion_antigravity_presente(home))
                with mock.patch("sys.platform", "win32"):
                    with mock.patch("subprocess.run", side_effect=vacio):
                        self.assertFalse(agentes.sesion_antigravity_presente(home))

            def hit(argv, **kwargs):
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    b"attribute.service = gemini\nattribute.username = antigravity\n",
                    b"",
                )

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

    def test_dos_escritores_usan_temporales_distintos(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            vistos = []
            errores = []
            barrera = threading.Barrier(2)
            real = os.replace

            def reemplazo(origen, destino):
                vistos.append(Path(origen).name)
                if str(origen).endswith(".tmp"):
                    barrera.wait(timeout=5)
                return real(origen, destino)

            def escribir(runtime):
                try:
                    _entorno(runtime, True, ["agy", "--print"], agente="antigravity")
                except Exception as exc:
                    errores.append(exc)

            with mock.patch("motor.encargos.agentes.Path.home", return_value=home):
                with mock.patch(
                    "motor.encargos.agentes.os.replace", side_effect=reemplazo
                ):
                    hilos = [
                        threading.Thread(
                            target=escribir, args=(Path(tmp) / f"run-{n}",)
                        )
                        for n in (1, 2)
                    ]
                    for hilo in hilos:
                        hilo.start()
                    for hilo in hilos:
                        hilo.join(timeout=10)
            self.assertEqual(errores, [])
            self.assertEqual(len(vistos), 2)
            self.assertNotEqual(vistos[0], vistos[1])
            self.assertNotIn("motor-turno.md.tmp", vistos)
            markdown = (
                home / ".gemini" / "config" / "agents" / "motor-turno.md"
            ).read_text(encoding="utf-8")
            self.assertIn("excludeDefaultComponents: true", markdown)
            self.assertNotIn("schedule", _herramientas(markdown))
            self.assertEqual(
                list((home / ".gemini" / "config" / "agents").glob("*.tmp")), []
            )


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
