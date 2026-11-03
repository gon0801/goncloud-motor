"""Ensayo optativo del agy instalado y su sesión de usuario.

MOTOR_PROBAR_AGY_VIVO=1 python3 -m unittest tests.test_antigravity_vivo -v

Usa la suscripción real y un espacio temporal. No lee credenciales ni imprime
el stream completo. La batería normal lo omite porque necesita login y red.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from motor.encargos.agentes import _lanzador
from motor.encargos.aislamiento import aislar


@unittest.skipUnless(
    os.environ.get("MOTOR_PROBAR_AGY_VIVO") == "1",
    "requiere activar el ensayo con agy instalado y sesión de usuario",
)
@unittest.skipUnless(os.name == "posix", "el ensayo vivo usa un comando POSIX")
class AntigravityVivo(unittest.TestCase):
    def test_arranca_ejecuta_comandos_y_el_hook_deniega_la_espera(self):
        self.assertIsNotNone(shutil.which("agy"), "falta agy en PATH")
        version = subprocess.run(
            ["agy", "--version"],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        ).stdout.strip()
        with tempfile.TemporaryDirectory(prefix="motor-agy-vivo-") as tmp:
            raiz = Path(tmp)
            espacio = raiz / "espacio" / "ensayo"
            espacio.mkdir(parents=True)
            prompt = raiz / "pedido.md"
            prompt.write_text(
                "Prueba de integración del motor. Usa run_command para ejecutar "
                "el comando `printf agy-vivo-ok > resultado.txt` en el directorio "
                "de trabajo actual. Después intenta llamar una sola vez a la "
                "herramienta wait para esperar un segundo. Si wait no está "
                "disponible, llama a schedule para esa espera. Si el hook la "
                "deniega, no uses alternativas ni reintentes. Responde agy-vivo-ok.",
                encoding="utf-8",
            )
            encargo = SimpleNamespace(
                rol="responsable", sesion_responsable="", sesion_revisor=""
            )
            argv = _lanzador("antigravity", "antigravity/gemini-4-argon-low")(
                encargo, prompt, espacio, ""
            )
            comando = aislar(
                argv,
                espacio,
                raiz / "runtime",
                suscripcion=True,
                agente="antigravity",
            )
            corrida = subprocess.run(
                [*comando.argv, "--print-timeout", "120s"],
                cwd=espacio,
                env={**os.environ, **comando.entorno},
                capture_output=True,
                text=True,
                timeout=150,
            )
            eventos = [
                json.loads(linea)
                for linea in corrida.stdout.splitlines()
                if linea.startswith("{")
            ]
            inicios = [e["init"] for e in eventos if e.get("event") == "init"]
            resultados = [e["result"] for e in eventos if e.get("event") == "result"]
            denegadas = [
                e["step_update"].get("tool_name")
                for e in eventos
                if e.get("event") == "step_update"
                and "tool call denied by pre-tool hook" in json.dumps(e["step_update"])
            ]
            print(
                json.dumps(
                    {
                        "agy": version,
                        "returncode": corrida.returncode,
                        "init": bool(inicios),
                        "herramientas_denegadas": denegadas,
                        "resultados": [r.get("status") for r in resultados],
                    },
                    sort_keys=True,
                )
            )
            self.assertEqual(corrida.returncode, 0)
            self.assertEqual(len(inicios), 1)
            self.assertTrue(
                {"run_command", "browser_click_element", "call_mcp_tool"}
                <= set(inicios[0]["tools"])
            )
            self.assertEqual((espacio / "resultado.txt").read_text(), "agy-vivo-ok")
            self.assertTrue(set(denegadas) & {"wait", "schedule"}, denegadas)
            self.assertEqual([r.get("status") for r in resultados], ["SUCCESS"])
