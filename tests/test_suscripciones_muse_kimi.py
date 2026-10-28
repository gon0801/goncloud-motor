"""Los agentes elegidos conservan su suscripción dentro de su propio runtime."""

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs

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
            reloj = [1000.0]

            def token(tipo, numero, expira):
                claims = {
                    "iss": "kimi",
                    "sub": "cuenta",
                    "user_id": "cuenta",
                    "client_id": "17e5f671-d194-4dfb-9706-5516cb48c098",
                    "device_id": "device-prueba",
                    "scope": "kimi-code",
                    "type": tipo,
                    "exp": int(expira),
                    "jti": str(numero),
                }
                body = (
                    base64.urlsafe_b64encode(json.dumps(claims).encode())
                    .decode()
                    .rstrip("=")
                )
                return "e30." + body + ".firma"

            def credencial(numero, expira):
                return {
                    "access_token": token("access", numero, expira),
                    "refresh_token": token("refresh", numero, 100000),
                    "expires_at": expira,
                    "expires_in": 900,
                    "scope": "kimi-code",
                    "token_type": "Bearer",
                }

            host_auth = kimi / "credentials/kimi-code.json"
            host_auth.write_text(json.dumps(credencial(0, 900)))
            (kimi / "device_id").write_text("device-prueba")
            http = Mock()
            respuesta = http.return_value.getresponse.return_value
            respuesta.status = 200
            respuesta.read.side_effect = lambda: json.dumps(
                credencial(http.call_count, reloj[0] + 900)
            ).encode()
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
                patch(
                    "motor.encargos.autenticacion_kimi.time.time",
                    side_effect=lambda: reloj[0],
                ),
                patch(
                    "motor.encargos.autenticacion_kimi.http.client.HTTPSConnection",
                    http,
                ),
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
                privado = tomllib.loads((destino / "config.toml").read_text())
                self.assertEqual(privado["default_model"], "kimi-code/k3.1-pro")
                self.assertEqual(
                    privado["providers"]["managed:kimi-code"]["api_key"],
                    credencial(1, 1900)["access_token"],
                )
                self.assertNotIn("oauth", privado["providers"]["managed:kimi-code"])
                self.assertFalse((destino / "credentials").exists())
                self.assertEqual(float(env_kimi["MOTOR_KIMI_ACCESS_EXPIRES_AT"]), 1900)
                self.assertEqual(json.loads(host_auth.read_text()), credencial(1, 1900))
                segundo = _entorno(runtime / "segundo", True, ["kimi"], agente="kimi")
                self.assertEqual(http.call_count, 1)
                self.assertFalse(
                    (Path(segundo["KIMI_CODE_HOME"]) / "credentials").exists()
                )
                reloj[0] = 2000
                tercero = _entorno(runtime / "tercero", True, ["kimi"], agente="kimi")
                self.assertEqual(http.call_count, 2)
                body = http.return_value.request.call_args.args[2]
                self.assertEqual(
                    parse_qs(body)["refresh_token"],
                    [credencial(1, 1900)["refresh_token"]],
                )
                self.assertEqual(json.loads(host_auth.read_text()), credencial(2, 2900))
                self.assertEqual(float(tercero["MOTOR_KIMI_ACCESS_EXPIRES_AT"]), 2900)
                self.assertFalse(
                    (Path(tercero["KIMI_CODE_HOME"]) / "credentials").exists()
                )
                # Estado real de archivos tras interrumpir la escritura canónica de R3.
                baseline = host_auth.read_bytes()
                pendiente = host_auth.with_name("kimi-code.motor-pending.json")
                pendiente.write_text(
                    json.dumps(
                        {
                            "baseline_sha256": hashlib.sha256(baseline).hexdigest(),
                            "baseline_base64": base64.b64encode(baseline).decode(),
                            "response_base64": base64.b64encode(
                                json.dumps(credencial(3, 3900)).encode()
                            ).decode(),
                        }
                    )
                )
                host_auth.write_bytes(b'{"access_token":')
                reloj[0] = 3000
                _entorno(runtime / "recuperado", True, ["kimi"], agente="kimi")
                self.assertEqual(http.call_count, 2)
                self.assertEqual(json.loads(host_auth.read_text()), credencial(3, 3900))
                self.assertFalse(pendiente.exists())
                reloj[0] = 4000
                respuesta.read.side_effect = lambda: json.dumps(
                    credencial(4, 3999)
                ).encode()
                with self.assertRaisesRegex(RuntimeError, "vencida"):
                    _entorno(
                        runtime / "servidor-vencido", True, ["kimi"], agente="kimi"
                    )
                self.assertEqual(http.call_count, 3)
                (kimi / "config.toml").write_text(
                    config.replace('api_key = ""', 'api_key = "directa"'),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(RuntimeError, "suscripcion OAuth"):
                    _entorno(runtime / "rechazado", True, ["kimi"], agente="kimi")
