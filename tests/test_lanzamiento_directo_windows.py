"""Lanzamiento directo en Windows: sin AppContainer, sin WSL y con el entorno del usuario.

Decisión del propietario del 2 de noviembre: en sus máquinas los agentes del
motor trabajan como él. La suscripción de cada agente sigue entregándose en su
carpeta del runtime y el temporal del intento sigue siendo propio.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from motor.encargos import agentes, aislamiento
from motor.encargos.aislamiento import ComandoAislado, aislar, delta_de_entorno
from tests import entorno, git_env_limpio

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
class LaGuardiaDeGhEnWindows(unittest.TestCase):
    def test_cmd_resuelve_la_guardia_y_no_llega_al_gh_real_para_mergear(self):
        import subprocess

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp).resolve()
            espacio, runtime = _coincidencia(base)
            real, llamadas = base / "gh-real", base / "llamadas.txt"
            real.mkdir()
            (real / "gh.cmd").write_text(
                f'@echo off\r\n>>"{llamadas}" echo %*\r\n', encoding="utf-8", newline=""
            )
            path = os.pathsep.join([str(real), os.environ.get("PATH", "")])
            with mock.patch.dict(os.environ, {"PATH": path}):
                comando = aislar([sys.executable, "-c", "pass"], espacio, runtime)

            def gh(*args: str) -> int:
                return subprocess.run(
                    ["cmd.exe", "/d", "/c", "gh", *args],
                    cwd=espacio,
                    env=comando.entorno,
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=60,
                ).returncode

            self.assertEqual(gh("pr", "merge", "5", "--squash"), 3)
            self.assertEqual(gh("api", "-X", "PUT", "repos/o/r/pulls/5/merge"), 3)
            self.assertEqual(gh("pr", "view", "5"), 0)
            self.assertEqual(
                llamadas.read_text(encoding="utf-8", errors="replace").split(),
                ["pr", "view", "5"],
            )


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
                    self.assertEqual(
                        env.get("XDG_CONFIG_HOME"),
                        os.environ.get("XDG_CONFIG_HOME"),
                        agente,
                    )

    def test_ningun_agente_cambia_las_variables_generales_del_usuario(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp).resolve()
            home = base / "home"
            (home / ".config" / "muse").mkdir(parents=True)
            (home / ".config" / "muse" / "auth.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "providers": {
                            "meta": {
                                "subs_active": True,
                                "access_token": "prueba",
                                "storage": "file",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            centinelas = {
                "HOME": str(home),
                "USERPROFILE": str(base / "perfil-real"),
                "APPDATA": str(base / "appdata-real"),
                "LOCALAPPDATA": str(base / "localappdata-real"),
                "XDG_CONFIG_HOME": str(base / "config-real"),
                "GH_CONFIG_DIR": str(base / "gh-real"),
                "CODEX_HOME": str(base / "codex-real"),
            }
            for agente in (
                "claude",
                "codex",
                "grok",
                "kimi",
                "muse",
                "opencode",
                "qwen",
                "antigravity",
            ):
                with self.subTest(agente=agente):
                    _, runtime = _coincidencia(base / agente)
                    with mock.patch.object(
                        Path, "home", return_value=home
                    ), mock.patch.dict(os.environ, centinelas):
                        env = self._entorno_de(agente, runtime)
                    for variable, valor in centinelas.items():
                        if agente == "codex" and variable == "CODEX_HOME":
                            continue
                        self.assertEqual(
                            env.get(variable), valor, f"{agente}:{variable}"
                        )
                    if agente == "codex":
                        self.assertEqual(env["CODEX_HOME"], str(runtime / ".codex"))


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
            delta = delta_de_entorno(comando)
        self.assertEqual(delta["MOTOR_DELTA"], "cambiada")
        self.assertEqual(delta["MOTOR_NUEVA"], "1")
        self.assertNotIn("MOTOR_IGUAL", delta)

    def test_el_delta_solo_existe_para_el_lanzamiento_directo(self):
        """El delta se mide contra el entorno del supervisor, que solo hereda el agente lanzado
        directo. Con otro mecanismo daría por quitado casi todo el entorno."""
        with self.assertRaises(ValueError):
            delta_de_entorno(ComandoAislado(["x"], {}, "prueba"))


TOKEN_FALSO = "token-falso-de-prueba"
AYUDANTE_DE_LA_MAQUINA = '"!f() { echo username=maquina; echo password=de-la-maquina; }; f"'
CABECERA_DE_LA_MAQUINA = "Authorization: Basic de-la-maquina"


class ElGitDelAgenteHaciaGitHub(unittest.TestCase):
    """`git` del agente llega a github.com con el token de solo lectura de su base y nunca con la
    credencial que guarda la máquina, también por ssh. Los demás hosts siguen con la suya. Corre
    en las tres plataformas: en Windows el ayudante pasa por el sh de Git."""

    def entorno_del_agente(
        self, base: Path, *, config: str = "", supervisor: dict | None = None
    ) -> tuple[Path, dict, str]:
        """El espacio, el entorno que `aislar` arma para el agente y la bitácora del supervisor,
        con la config git del usuario más `config` y el entorno del supervisor más `supervisor`."""
        gitconfig = base / "gitconfig"
        gitconfig.write_text(
            f"[credential]\n\thelper = {AYUDANTE_DE_LA_MAQUINA}\n"
            f'[credential "https://github.com"]\n\thelper = {AYUDANTE_DE_LA_MAQUINA}\n'
            + config,
            encoding="utf-8",
        )
        espacio, runtime = _coincidencia(base)
        heredado = {
            **git_env_limpio(),
            "GIT_CONFIG_GLOBAL": str(gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
            **(supervisor or {}),
        }
        with (
            mock.patch.dict(os.environ, heredado, clear=True),
            mock.patch("sys.stderr", new_callable=io.StringIO) as bitacora,
        ):
            comando = aislar([sys.executable, "-c", "pass"], espacio, runtime)
        return espacio, comando.entorno, bitacora.getvalue()

    def git(self, espacio: Path, entorno: dict, *args: str, entrada: str | None = None):
        return subprocess.run(
            ["git", *args], cwd=espacio, env=entorno, input=entrada, capture_output=True,
            text=True, timeout=60,
        )

    def credencial(self, espacio: Path, entorno: dict, host: str):
        return self.git(
            espacio, entorno, "credential", "fill", entrada=f"protocol=https\nhost={host}\n\n"
        )

    def cabecera(self, espacio: Path, entorno: dict, url: str) -> str:
        """La cabecera extra que git manda a `url`: un valor vacío deja la lista vacía."""
        return self.git(
            espacio, entorno, "config", "--get-urlmatch", "http.extraHeader", url
        ).stdout.strip()

    def test_da_el_token_de_la_base_y_no_el_de_la_maquina(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp).resolve()
            token = base / "github-agentes.token"
            token.write_text(TOKEN_FALSO + "\n", encoding="utf-8")
            token.chmod(0o600)
            espacio, entorno, _ = self.entorno_del_agente(base)
            github = self.credencial(espacio, entorno, "github.com")
            otro = self.credencial(espacio, entorno, "example.com")
            ssh = self.git(espacio, entorno, "ls-remote", "--get-url", "git@github.com:o/r.git")
            largas = self.git(espacio, entorno, "config", "--get", "core.longpaths")
        self.assertIn(f"password={TOKEN_FALSO}\n", github.stdout, github.stderr)
        self.assertNotIn("de-la-maquina", github.stdout)
        self.assertIn("password=de-la-maquina\n", otro.stdout, otro.stderr)
        self.assertEqual(ssh.stdout.strip(), "https://github.com/o/r.git", ssh.stderr)
        self.assertEqual(largas.stdout.strip(), "true", "la config git del supervisor sigue")

    def test_sin_token_no_da_ninguna_credencial_de_github(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            espacio, entorno, _ = self.entorno_del_agente(Path(tmp).resolve())
            github = self.credencial(espacio, entorno, "github.com")
        self.assertNotEqual(github.returncode, 0, github.stdout)
        self.assertNotIn("password=", github.stdout)

    @unittest.skipIf(os.name == "nt", "git corre el askpass sin shell y el de prueba es un guion sh")
    def test_sin_token_ningun_askpass_heredado_da_la_credencial_de_la_maquina(self):
        for origen in ("GIT_ASKPASS", "SSH_ASKPASS", "core.askPass"):
            with self.subTest(origen=origen), tempfile.TemporaryDirectory(
                ignore_cleanup_errors=True
            ) as tmp:
                base = Path(tmp).resolve()
                askpass = base / "askpass"
                askpass.write_text("#!/bin/sh\necho de-la-maquina\n", encoding="utf-8")
                askpass.chmod(0o755)
                if origen == "core.askPass":
                    heredado = {"config": f"[core]\n\taskPass = {askpass}\n"}
                else:
                    heredado = {"supervisor": {origen: str(askpass)}}
                espacio, entorno, _ = self.entorno_del_agente(base, **heredado)
                github = self.credencial(espacio, entorno, "github.com")
                self.assertNotIn("de-la-maquina", github.stdout)
                self.assertNotEqual(github.returncode, 0, github.stdout)

    def test_ninguna_cabecera_heredada_llega_a_github(self):
        por_contador = {
            "GIT_CONFIG_COUNT": "3",
            "GIT_CONFIG_KEY_1": "http.https://github.com/.extraHeader",
            "GIT_CONFIG_VALUE_1": CABECERA_DE_LA_MAQUINA,
            "GIT_CONFIG_KEY_2": "http.https://example.com/.extraHeader",
            "GIT_CONFIG_VALUE_2": CABECERA_DE_LA_MAQUINA,
        }
        por_parametros = {
            "GIT_CONFIG_PARAMETERS": (
                f"'http.https://github.com/.extraHeader'='{CABECERA_DE_LA_MAQUINA}'"
            ),
        }
        for nombre, supervisor in (("contador", por_contador), ("parametros", por_parametros)):
            with self.subTest(origen=nombre), tempfile.TemporaryDirectory(
                ignore_cleanup_errors=True
            ) as tmp:
                espacio, entorno, _ = self.entorno_del_agente(
                    Path(tmp).resolve(), supervisor=supervisor
                )
                github = self.cabecera(espacio, entorno, "https://github.com/o/r.git")
                otro = self.cabecera(espacio, entorno, "https://example.com/o/r.git")
                self.assertEqual(github, "")
                if nombre == "contador":
                    self.assertEqual(otro, CABECERA_DE_LA_MAQUINA, "otro host conserva la suya")

    def test_un_git_config_count_heredado_que_no_es_numero_no_impide_arrancar(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            espacio, entorno, bitacora = self.entorno_del_agente(
                Path(tmp).resolve(), supervisor={"GIT_CONFIG_COUNT": "uno"}
            )
            ssh = self.git(espacio, entorno, "ls-remote", "--get-url", "git@github.com:o/r.git")
        self.assertEqual(ssh.stdout.strip(), "https://github.com/o/r.git", ssh.stderr)
        self.assertIn("GIT_CONFIG_COUNT", bitacora)


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


@entorno.requiere_windows
class TurnoDeAntigravityWindows(unittest.TestCase):
    """La guarda de sesión del turno también corre en Windows.

    Vive aquí porque este módulo es de los que ejecuta el job windows del CI:
    sin sesión, el turno no lanza agy y no puede abrir el navegador con
    rundll32.
    """

    def test_sin_sesion_el_turno_no_lanza_agy(self):
        """Sin sesión se reporta el estado sin_sesion y no hay proceso."""
        from motor.flota import turnos

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            with mock.patch.object(
                turnos, "sesion_antigravity_presente", return_value=False
            ), mock.patch.object(
                turnos.subprocess,
                "run",
                side_effect=AssertionError("el turno lanzó un proceso"),
            ):
                resultado = turnos._turno_agente("antigravity", None, Path(tmp))
        self.assertEqual(resultado["estado"], "sin_sesion")
        self.assertEqual(resultado["rc"], 126)


if __name__ == "__main__":
    unittest.main()
