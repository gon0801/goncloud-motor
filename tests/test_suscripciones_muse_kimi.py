"""Los agentes elegidos conservan su suscripción dentro de su propio runtime."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tomllib

from motor.encargos.aislamiento import _entorno


class SuscripcionesMuseKimi(unittest.TestCase):
    def test_credenciales_de_suscripcion_aisladas(self):
        with tempfile.TemporaryDirectory() as temporal:
            home = Path(temporal) / "home"
            meta = home / ".config/muse"
            meta.mkdir(parents=True)
            (meta / "auth.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "providers": {
                            "meta": {
                                "subs_active": True,
                                "access_token": "prueba",
                                "api_base_url": "https://api.meta.ai/v1",
                            },
                            "otro": {"api_key": "no-copiar"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            kimi = home / ".kimi-code"
            (kimi / "credentials").mkdir(parents=True)
            (kimi / "credentials/kimi-code.json").write_text(
                json.dumps({"access_token": "prueba"})
            )
            config = """default_model = "kimi-code/k3.1-pro"
[providers."managed:kimi-code"]
type = "kimi"
api_key = ""
base_url = "https://api.kimi.com/coding/v1"
[providers."managed:kimi-code".oauth]
storage = "file"
key = "oauth/kimi-code"
[models."kimi-code/k3.1-pro"]
provider = "managed:kimi-code"
model = "k3.1-pro"
max_context_size = 1048576
capabilities = ["thinking", "tool_use"]
"""
            (kimi / "config.toml").write_text(config, encoding="utf-8")
            with (
                patch.object(Path, "home", return_value=home),
                patch.dict("os.environ", {}, clear=True),
            ):
                runtime = Path(temporal) / "runtime"
                env_meta = _entorno(runtime / "meta", True, ["muse"], agente="muse")
                copiada = json.loads(
                    (Path(env_meta["XDG_CONFIG_HOME"]) / "muse/auth.json").read_text()
                )
                self.assertEqual(set(copiada["providers"]), {"meta"})
                env_kimi = _entorno(
                    runtime / "kimi",
                    True,
                    ["kimi", "--model", "kimi-code/k3.1-pro"],
                    agente="kimi",
                )
                destino = Path(env_kimi["KIMI_CODE_HOME"])
                self.assertNotEqual(destino, kimi)
                self.assertEqual(
                    json.loads((destino / "credentials/kimi-code.json").read_text()),
                    {"access_token": "prueba"},
                )
                self.assertEqual(
                    tomllib.loads((destino / "config.toml").read_text())[
                        "default_model"
                    ],
                    "kimi-code/k3.1-pro",
                )
                (kimi / "config.toml").write_text(
                    config.replace('api_key = ""', 'api_key = "directa"'),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(RuntimeError, "suscripcion OAuth"):
                    _entorno(runtime / "rechazado", True, ["kimi"], agente="kimi")
