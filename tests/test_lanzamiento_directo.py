"""Lanzamiento directo en POSIX: sin sandbox-exec y con el entorno del usuario.

Decisión del propietario del 2 de noviembre: en sus Mac los agentes del motor
trabajan como él, sin jaula, sin listas de permitidos y sin entorno recortado.
La suscripción de cada agente sigue entregándose en su carpeta del runtime,
salvo Muse, que usa la configuración real del usuario, y el HOME del usuario
deja de falsearse.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import aislamiento
from motor.encargos.aislamiento import aislar
from motor.encargos.suscripciones import CuentaNoDisponible

SANDBOX = "/usr/bin/sandbox-exec"
ES_POSIX = os.name != "nt"
FUTURO = "2999-01-01T00:00:00+00:00"


def _coincidencia(tmp: Path) -> tuple[Path, Path]:
    espacio = tmp / "espacio" / "enc_x"
    espacio.mkdir(parents=True)
    return espacio, tmp / "runtime"


def _comando_directo(cmd: list[str] | None = None, **kw) -> object:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        base = Path(tmp).resolve()
        espacio, runtime = _coincidencia(base)
        return aislar(cmd or ["true"], espacio, runtime, **kw)


@unittest.skipUnless(ES_POSIX, "el lanzamiento directo es de la rama POSIX")
class SinSandboxExec(unittest.TestCase):
    def test_aislar_ya_no_se_niega_y_el_mecanismo_es_directo(self):
        """En Linux `aislar` se negaba sin sandbox-exec; en macOS envolvía el
        comando. Ahora devuelve el comando tal cual, con mecanismo "directo"."""
        comando = _comando_directo()
        self.assertEqual(comando.mecanismo, "directo")
        self.assertEqual(comando.argv, ["true"])

    def test_en_macos_el_argv_lanzado_no_empieza_por_sandbox_exec(self):
        """El argv que la Mac lanza es el de la rama POSIX, compartida con
        Linux: esta prueba corre en la batería de ubuntu y en el job `macos`
        de `pruebas.yml`, que la ejerce en la Mac de verdad."""
        comando = _comando_directo(["/usr/bin/true"])
        self.assertNotEqual(comando.argv[0], SANDBOX)
        self.assertFalse(any(SANDBOX in parte for parte in comando.argv))

    @unittest.skipUnless(ES_POSIX, "el lanzamiento directo es de la rama POSIX")
    def test_el_comando_se_lanza_de_verdad_con_su_entorno(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp).resolve()
            espacio, runtime = _coincidencia(base)
            comando = aislar(
                [sys.executable, "-c", "print('lanzado')"], espacio, runtime
            )
            hecho = subprocess.run(
                comando.argv,
                cwd=espacio,
                env=comando.entorno,
                capture_output=True,
                text=True,
                timeout=60,
            )
        self.assertEqual(hecho.returncode, 0, hecho.stderr)
        self.assertEqual(hecho.stdout, "lanzado\n")


@unittest.skipUnless(ES_POSIX, "el lanzamiento directo es de la rama POSIX")
class LaGuardiaDeGhEnElPath(unittest.TestCase):
    def test_el_comando_lanzado_no_mergea_y_lo_demas_llega_al_gh_real(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp).resolve()
            espacio, runtime = _coincidencia(base)
            real, llamadas = base / "gh-real", base / "llamadas"
            real.mkdir()
            (real / "gh").write_text(
                f'#!/bin/sh\necho "$@" >> {shlex.quote(str(llamadas))}\n', encoding="utf-8"
            )
            (real / "gh").chmod(0o755)
            guion = (
                'gh pr merge 5 --squash; echo "merge=$?"; '
                'gh api -X PUT repos/o/r/pulls/5/merge; echo "api=$?"; '
                'gh pr view 5; echo "view=$?"'
            )
            path = os.pathsep.join([str(real), os.environ.get("PATH", "")])
            with mock.patch.dict(os.environ, {"PATH": path}):
                comando = aislar(["/bin/sh", "-c", guion], espacio, runtime, interprete=True)
            hecho = subprocess.run(
                comando.argv,
                cwd=espacio,
                env=comando.entorno,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(hecho.stdout.split(), ["merge=3", "api=3", "view=0"], hecho.stderr)
            self.assertEqual(llamadas.read_text(encoding="utf-8").split(), ["pr", "view", "5"])


@unittest.skipUnless(ES_POSIX, "el entorno del usuario es de la rama POSIX")
class EntornoDelUsuario(unittest.TestCase):
    def test_home_real_y_una_variable_cualquiera_del_supervisor(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, runtime = _coincidencia(Path(tmp).resolve())
            with mock.patch.dict(
                os.environ,
                {
                    "MOTOR_PRUEBA_SUPERVISOR": "visible",
                    "XDG_CONFIG_HOME": str(Path(tmp) / "config-del-usuario"),
                },
            ):
                env = aislamiento._entorno(runtime, False, [sys.executable])
        self.assertEqual(env["MOTOR_PRUEBA_SUPERVISOR"], "visible")
        self.assertEqual(env["HOME"], os.environ.get("HOME", str(Path.home())))
        self.assertEqual(env["XDG_CONFIG_HOME"], str(Path(tmp) / "config-del-usuario"))

    def test_el_temporal_sigue_siendo_el_del_intento(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, runtime = _coincidencia(Path(tmp).resolve())
            env = aislamiento._entorno(runtime, False, [sys.executable])
        temporal = (runtime / "tmp").resolve()
        for variable in ("TMPDIR", "TEMP", "TMP", "CLAUDE_CODE_TMPDIR"):
            self.assertEqual(Path(env[variable]).resolve(), temporal, variable)


@unittest.skipUnless(ES_POSIX, "el lanzamiento directo es de la rama POSIX")
class MuseSigueNativo(unittest.TestCase):
    def test_el_argv_usa_el_binario_nativo_y_deja_procedencia(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp).resolve()
            nativo = base / "muse-bin-1.4.11"
            nativo.write_text("#!/bin/sh\n", encoding="utf-8")
            nativo.chmod(0o755)
            espacio, runtime = _coincidencia(base)
            with mock.patch(
                "motor.encargos.autenticacion_muse.ejecutable_muse",
                return_value=nativo,
            ):
                comando = aislar(
                    ["muse", "exec", "--model", "meta/muse-wonder-1.0"],
                    espacio,
                    runtime,
                    suscripcion=True,
                    agente="muse",
                )
            huella = hashlib.sha256(nativo.read_bytes()).hexdigest()
        self.assertNotEqual(comando.argv[0], SANDBOX)
        self.assertEqual(comando.argv, [str(nativo), "exec", "--model", "meta/muse-wonder-1.0"])
        self.assertEqual(comando.mecanismo, "directo")
        self.assertEqual(comando.ejecutable["tipo"], "instalacion-local-firmada")
        self.assertEqual(comando.ejecutable["origen"], str(nativo))
        self.assertEqual(comando.ejecutable["sha256"], huella)


@unittest.skipUnless(ES_POSIX, "el lanzamiento directo es de la rama POSIX")
class CadaAgenteSigueRecibiendoSuSesion(unittest.TestCase):
    """Sin falsear HOME ni XDG_CONFIG_HOME, cada agente con carpeta de
    sesión (claude, codex, grok, kimi, opencode, qwen) tiene que seguir
    recibiendo la suya, donde el motor prepara su suscripción. Muse queda
    fuera: usa la configuración real del usuario (ver MuseUsaLaConfiguracionReal)."""

    def _entorno_de(self, agente: str, runtime: Path) -> dict:
        cmd = [agente]
        retorno: object = {}
        if agente == "opencode":
            cmd = ["opencode", "--model", "zai-coding-plan/glm-4.6"]
            retorno = {"zai-coding-plan": {"type": "api", "key": "clave-prueba"}}
        elif agente == "claude":
            retorno = {"accessToken": "sk-ant-oat-prueba"}
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
                    self.assertNotEqual(env["HOME"], str(runtime), agente)
                    self.assertNotEqual(
                        env.get("XDG_CONFIG_HOME", ""), str(runtime / ".config"),
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


@unittest.skipUnless(ES_POSIX, "el entorno del usuario es de la rama POSIX")
class MuseUsaLaConfiguracionReal(unittest.TestCase):
    """Muse no tiene variable ni bandera propia para su carpeta de cuenta. El
    motor no la copia ni desvía XDG_CONFIG_HOME: el CLI lee la configuración
    real del usuario, como cuando el propietario lo corre a mano."""

    def _auth(self, home: Path, xdg: Path | None = None) -> tuple[Path, str]:
        carpeta = (xdg if xdg is not None else home / ".config") / "muse"
        carpeta.mkdir(parents=True)
        auth = carpeta / "auth.json"
        contenido = json.dumps(
            {
                "schema_version": 3,
                "providers": {
                    "meta": {
                        "subs_active": True,
                        "storage": "file",
                        "access_token": "cuenta-real",
                    }
                },
            }
        )
        auth.write_text(contenido, encoding="utf-8")
        return auth, contenido

    def test_conserva_el_xdg_config_home_del_supervisor(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp).resolve()
            home = base / "home"
            home.mkdir()
            xdg = base / "config-real"
            auth, contenido = self._auth(home, xdg)
            _, runtime = _coincidencia(base)
            with mock.patch.dict(
                os.environ, {"HOME": str(home), "XDG_CONFIG_HOME": str(xdg)},
                clear=True,
            ):
                env = aislamiento._entorno(runtime, True, ["muse"], agente="muse")
            self.assertEqual(env["XDG_CONFIG_HOME"], str(xdg))
            self.assertEqual(env["HOME"], str(home))
            self.assertFalse((runtime / ".config" / "muse" / "auth.json").exists())
            self.assertTrue((Path(env["XDG_CONFIG_HOME"]) / "muse/auth.json").is_file())
            self.assertEqual(auth.read_text(encoding="utf-8"), contenido)

    def test_disponible_usa_la_misma_precedencia_que_el_cliente_nativo(self):
        from motor.encargos.agentes import _disponible

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp).resolve()
            home = base / "home"
            home.mkdir()
            xdg = base / "config-real"
            self._auth(home, xdg)
            with (
                mock.patch.object(Path, "home", return_value=home),
                mock.patch.dict(
                    os.environ,
                    {"HOME": str(home), "XDG_CONFIG_HOME": str(xdg)},
                    clear=True,
                ),
                mock.patch(
                    "motor.encargos.agentes.resolver_binario",
                    return_value="/cliente/muse",
                ),
            ):
                self.assertTrue(_disponible("muse"))

            self._auth(home)
            xdg_vacio = base / "config-sin-cuenta"
            xdg_vacio.mkdir()
            with (
                mock.patch.object(Path, "home", return_value=home),
                mock.patch.dict(
                    os.environ,
                    {"HOME": str(home), "XDG_CONFIG_HOME": str(xdg_vacio)},
                    clear=True,
                ),
                mock.patch(
                    "motor.encargos.agentes.resolver_binario",
                    return_value="/cliente/muse",
                ),
            ):
                self.assertFalse(_disponible("muse"))

            with (
                mock.patch.object(Path, "home", return_value=home),
                mock.patch.dict(os.environ, {"HOME": str(home)}, clear=True),
                mock.patch(
                    "motor.encargos.agentes.resolver_binario",
                    return_value="/cliente/muse",
                ),
            ):
                self.assertTrue(_disponible("muse"))

    def test_sin_xdg_config_home_lo_deja_sin_definir(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp).resolve()
            home = base / "home"
            home.mkdir()
            auth, contenido = self._auth(home)
            _, runtime = _coincidencia(base)
            with mock.patch.dict(os.environ, {"HOME": str(home)}, clear=True):
                env = aislamiento._entorno(runtime, True, ["muse"], agente="muse")
            self.assertNotIn("XDG_CONFIG_HOME", env)
            self.assertEqual(env["HOME"], str(home))
            self.assertFalse((runtime / ".config" / "muse" / "auth.json").exists())
            self.assertTrue((Path(env["HOME"]) / ".config/muse/auth.json").is_file())
            self.assertEqual(auth.read_text(encoding="utf-8"), contenido)


class CredencialPropiaDeDeepSeek(unittest.TestCase):
    """DeepSeek por su API propia recibe solo su credencial, nunca la de Go."""

    def _host(
        self, base: Path, auth: dict, db: dict[str, dict] | None = None
    ) -> Path:
        host = base / "host"
        (host / "opencode").mkdir(parents=True)
        (host / "opencode" / "auth.json").write_text(
            json.dumps(auth), encoding="utf-8"
        )
        if db:
            conexion = sqlite3.connect(host / "opencode" / "opencode.db")
            conexion.execute(
                "CREATE TABLE credential (integration_id TEXT, value TEXT, "
                "active INTEGER, time_updated INTEGER)"
            )
            for proveedor, cuenta in db.items():
                conexion.execute(
                    "INSERT INTO credential VALUES (?, ?, 1, 1)",
                    (proveedor, json.dumps(cuenta)),
                )
            conexion.commit()
            conexion.close()
        return host

    def _lanzar(self, base: Path, host: Path):
        _espacio, runtime = _coincidencia(base)
        cmd = ["opencode", "run", "--model", "deepseek/deepseek-flash"]
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(host)}):
            env = aislamiento._entorno(runtime, True, cmd, agente="opencode")
        return runtime, env

    def _comprobar_aislamiento(self, auth: dict, db: dict[str, dict] | None):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp).resolve()
            host = self._host(base, auth, db)
            runtime, env = self._lanzar(base, host)
            archivo = runtime / ".local/share/opencode/auth.json"
            texto = archivo.read_text(encoding="utf-8")
            self.assertEqual(
                json.loads(texto), {"deepseek": {"type": "api", "key": "clave-propia"}}
            )
            self.assertNotIn("clave-go", texto)
            config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
            self.assertEqual(config["enabled_providers"], ["deepseek"])
            self.assertEqual(env["XDG_DATA_HOME"], str(runtime / ".local/share"))

    def test_entrega_solo_la_credencial_propia_desde_auth_json(self):
        self._comprobar_aislamiento(
            {
                "opencode-go": {"type": "api", "key": "clave-go"},
                "deepseek": {"type": "api", "key": "clave-propia"},
            },
            None,
        )

    def test_entrega_solo_la_credencial_propia_desde_opencode_db(self):
        self._comprobar_aislamiento(
            {"opencode-go": {"type": "api", "key": "clave-go"}},
            {"deepseek": {"type": "api", "key": "clave-propia"}},
        )

    def test_sin_credencial_propia_no_cae_a_go(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp).resolve()
            host = self._host(
                base, {"opencode-go": {"type": "api", "key": "clave-go"}}
            )
            _espacio, runtime = _coincidencia(base)
            cmd = ["opencode", "run", "--model", "deepseek/deepseek-flash"]
            with self.assertRaises(CuentaNoDisponible) as error:
                with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(host)}):
                    aislamiento._entorno(runtime, True, cmd, agente="opencode")
            self.assertIn("deepseek", str(error.exception))
            self.assertFalse(
                (runtime / ".local/share/opencode/auth.json").exists()
            )


if __name__ == "__main__":
    unittest.main()
