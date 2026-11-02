"""Lanzamiento directo en POSIX: sin sandbox-exec y con el entorno del usuario.

Decisión del propietario del 2 de noviembre: en sus Mac los agentes del motor
trabajan como él, sin jaula, sin listas de permitidos y sin entorno recortado.
La suscripción de cada agente sigue entregándose en su carpeta del runtime,
que no bloquea nada, y el HOME del usuario deja de falsearse.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import aislamiento
from motor.encargos.aislamiento import aislar

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
        Linux: sin job propio de macOS, esta prueba corre en la batería de
        ubuntu y cubre por construcción el camino que usa la Mac."""
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
            cuenta = {"subs_active": True, "access_token": "prueba", "storage": "file"}
            home = base / "home"
            (home / ".config" / "muse").mkdir(parents=True)
            (home / ".config" / "muse" / "auth.json").write_text(
                json.dumps({"schema_version": 3, "providers": {"meta": cuenta}}),
                encoding="utf-8",
            )
            with mock.patch.object(Path, "home", return_value=home), mock.patch.object(
                aislamiento, "leer_cuenta", return_value=cuenta
            ), mock.patch(
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
    """Sin falsear HOME ni XDG_CONFIG_HOME, cada agente tiene que seguir
    recibiendo la carpeta de sesión donde el motor prepara su suscripción."""

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
            mock.patch.object(aislamiento, "leer_cuenta", return_value=retorno),
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
                    self.assertNotEqual(env["HOME"], str(runtime), agente)
                    if agente == "muse":
                        # La carpeta de sesión de Muse: su propia XDG del
                        # intento; el resto del entorno conserva la real.
                        self.assertEqual(
                            env["XDG_CONFIG_HOME"], str(runtime / ".config")
                        )
                    else:
                        self.assertNotEqual(
                            env.get("XDG_CONFIG_HOME", ""), str(runtime / ".config"),
                            agente,
                        )


if __name__ == "__main__":
    unittest.main()
