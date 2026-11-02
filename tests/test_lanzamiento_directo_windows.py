"""Lanzamiento directo en Windows: sin AppContainer, sin WSL y con el entorno del usuario.

Decisión del propietario del 2 de noviembre: en sus máquinas los agentes del
motor trabajan como él. La suscripción de cada agente sigue entregándose en su
carpeta del runtime y el temporal del intento sigue siendo propio.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from motor.encargos import agentes, aislamiento
from motor.encargos.aislamiento import ComandoAislado, aislar, entorno_persistente
from tests import entorno

FUTURO = "2999-01-01T00:00:00+00:00"


def _coincidencia(tmp: Path) -> tuple[Path, Path]:
    espacio = tmp / "espacio" / "enc_x"
    espacio.mkdir(parents=True)
    return espacio, tmp / "runtime"


@entorno.requiere_windows
class LanzamientoDirectoWindows(unittest.TestCase):
    def test_el_argv_no_pasa_por_appcontainer_ni_wsl_y_el_mecanismo_es_directo(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            espacio, runtime = _coincidencia(Path(tmp).resolve())
            with mock.patch(
                "motor.encargos.suscripciones.preparar", lambda *a, **k: None
            ):
                comando = aislar(
                    [sys.executable, "-c", "pass"],
                    espacio,
                    runtime,
                    suscripcion=True,
                    agente="claude",
                )
        self.assertEqual(comando.mecanismo, "directo")
        bajo = " ".join(comando.argv).lower()
        self.assertNotIn("wsl.exe", bajo)
        self.assertNotIn("appcontainer", bajo)


@entorno.requiere_windows
class EntornoDelUsuarioWindows(unittest.TestCase):
    def test_userprofile_real_y_una_variable_cualquiera_del_supervisor(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, runtime = _coincidencia(Path(tmp).resolve())
            with mock.patch.dict(
                os.environ, {"MOTOR_PRUEBA_SUPERVISOR": "visible"}
            ):
                env = aislamiento._entorno(runtime, False, [sys.executable])
        self.assertEqual(env["MOTOR_PRUEBA_SUPERVISOR"], "visible")
        self.assertEqual(env["USERPROFILE"], os.environ["USERPROFILE"])
        self.assertEqual(env["APPDATA"], os.environ["APPDATA"])
        self.assertEqual(env["LOCALAPPDATA"], os.environ["LOCALAPPDATA"])
        self.assertEqual(env.get("HOME"), os.environ.get("HOME"))
        self.assertNotEqual(env["USERPROFILE"], str(runtime))

    def test_el_temporal_sigue_siendo_el_del_intento(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, runtime = _coincidencia(Path(tmp).resolve())
            env = aislamiento._entorno(runtime, False, [sys.executable])
        temporal = (runtime / "tmp").resolve()
        for variable in ("TMPDIR", "TEMP", "TMP", "CLAUDE_CODE_TMPDIR"):
            self.assertEqual(Path(env[variable]).resolve(), temporal, variable)


@entorno.requiere_windows
class CadaAgenteRecibeSuSesionWindows(unittest.TestCase):
    def _entorno_de(self, agente: str, runtime: Path) -> dict:
        cmd = [agente]
        retorno: object = {}
        if agente == "opencode":
            cmd = ["opencode", "--model", "zai-coding-plan/glm-4.6"]
            retorno = {"zai-coding-plan": {"type": "api", "key": "clave-prueba"}}
        elif agente == "claude":
            retorno = {
                "accessToken": "sk-ant-oat-prueba",
                "expiresAt": (time.time() + 3600) * 1000,
            }
        elif agente == "grok":
            retorno = {"expires_at": FUTURO, "key": "prueba", "auth_mode": "oidc"}
        elif agente == "qwen":
            retorno = (
                {"model": {"name": "qwen3-coder", "reasoningEffort": "medium"}},
                "clave-prueba",
            )
        parches = [
            mock.patch.object(aislamiento, "leer_cuenta", return_value=retorno),
            mock.patch(
                "motor.encargos.suscripciones.leer_cuenta", return_value=retorno
            ),
        ]
        if agente == "kimi":
            parches.append(
                mock.patch.object(aislamiento, "_preparar_kimi", return_value=1e12)
            )
            parches.append(
                mock.patch(
                    "motor.encargos.suscripciones.preparar_kimi_opencode",
                    lambda *a, **k: None,
                )
            )
        if agente == "codex":
            origen = runtime.parent / "host-codex"
            origen.mkdir(parents=True, exist_ok=True)
            (origen / "auth.json").write_text(
                json.dumps({"auth_mode": "chatgpt"}), encoding="utf-8"
            )
            parches.append(mock.patch.dict(os.environ, {"CODEX_HOME": str(origen)}))
        for parche in parches:
            parche.start()
            self.addCleanup(parche.stop)
        return aislamiento._entorno(runtime, True, cmd, agente=agente)

    def test_cada_agente_recibe_su_variable_de_sesion(self):
        esperadas = {
            "claude": "CLAUDE_CONFIG_DIR",
            "codex": "CODEX_HOME",
            "grok": "GROK_HOME",
            "kimi": "KIMI_CODE_HOME",
            "opencode": "XDG_DATA_HOME",
            "qwen": "QWEN_HOME",
            "muse": "XDG_CONFIG_HOME",
        }
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp).resolve()
            cuenta = {"subs_active": True, "access_token": "prueba", "storage": "file"}
            home = base / "home"
            (home / ".config" / "muse").mkdir(parents=True)
            (home / ".config" / "muse" / "auth.json").write_text(
                json.dumps({"schema_version": 1, "providers": {"meta": cuenta}}),
                encoding="utf-8",
            )
            for agente, variable in esperadas.items():
                with self.subTest(agente=agente):
                    _, runtime = _coincidencia(base / agente)
                    with mock.patch.object(Path, "home", return_value=home):
                        env = self._entorno_de(agente, runtime)
                    self.assertIn(variable, env, agente)
                    self.assertTrue(env[variable].startswith(str(runtime)), agente)
                    self.assertEqual(
                        env["USERPROFILE"], os.environ["USERPROFILE"], agente
                    )


class ArranqueGuardaDelta(unittest.TestCase):
    def test_solo_guarda_lo_que_el_motor_cambia_del_entorno(self):
        with mock.patch.dict(
            os.environ, {"MOTOR_DELTA": "original", "MOTOR_IGUAL": "mismo"}
        ):
            comando = ComandoAislado(
                ["x"],
                {
                    "MOTOR_DELTA": "cambiada",
                    "MOTOR_NUEVA": "1",
                    "MOTOR_IGUAL": "mismo",
                },
                "directo",
            )
            delta = entorno_persistente(comando)
        self.assertEqual(delta["MOTOR_DELTA"], "cambiada")
        self.assertEqual(delta["MOTOR_NUEVA"], "1")
        self.assertNotIn("MOTOR_IGUAL", delta)


class GrokSinTopes(unittest.TestCase):
    def _argv(self, modo: str, previa: str = "", sesion: str = "ses-nueva"):
        enc = SimpleNamespace(
            rol="responsable",
            sesion_responsable=previa,
            sesion_revisor="",
        )
        lanzador = agentes._lanzador("grok", "xai/grok-4.7", modo)
        return lanzador(enc, Path("/p/prompt.md"), Path("/e"), sesion)

    def test_el_argv_no_lleva_max_turns_ni_disable_web_search(self):
        argv = self._argv("nueva")
        self.assertNotIn("--max-turns", argv)
        self.assertNotIn("--disable-web-search", argv)

    def test_un_reintento_que_no_reanuda_no_repite_el_session_id(self):
        primero = self._argv("nueva", sesion="misma-sesion")
        segundo = self._argv("nueva", sesion="misma-sesion")
        self.assertIn("--session-id", primero)
        self.assertIn("--session-id", segundo)
        self.assertNotEqual(
            primero[primero.index("--session-id") + 1],
            segundo[segundo.index("--session-id") + 1],
        )

    def test_el_que_reanuda_usa_resume_con_la_sesion_anterior(self):
        argv = self._argv("reanudada", previa="ses-previa", sesion="otra")
        self.assertIn("--resume", argv)
        self.assertEqual(argv[argv.index("--resume") + 1], "ses-previa")
        self.assertNotIn("--session-id", argv)


if __name__ == "__main__":
    unittest.main()
