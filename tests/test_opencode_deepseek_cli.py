"""La ruta deepseek/ elige la superficie del OpenCode que liga la flota."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from motor.flota import runtime


def _cmd() -> list[str]:
    return [
        "opencode",
        "run",
        "--standalone",
        "--auto",
        "--format",
        "json",
        "--model",
        "deepseek/deepseek-flash",
        "--session",
        "ses_anterior",
        "Lee el pedido y ejecuta el trabajo autorizado.",
    ]


def _valor(argumentos: list[str], bandera: str) -> str:
    return argumentos[argumentos.index(bandera) + 1]


class SuperficieDeDeepSeek(unittest.TestCase):
    def _generacion(
        self, base: Path, opciones: str, destino: str
    ) -> tuple[Path, Path]:
        generacion = base / "generacion"
        (generacion / "bin").mkdir(parents=True)
        guion = generacion / "bin" / "opencode_cli.py"
        marca = generacion / "bin" / "consultado"
        guion.write_text(
            "import pathlib, sys\n"
            f"pathlib.Path({str(marca)!r}).write_text('si', encoding='utf-8')\n"
            f"print({opciones!r}, file=sys.{destino})\n",
            encoding="utf-8",
        )
        (generacion / "runtime.json").write_text(
            json.dumps(
                {
                    "lanzadores": {
                        "opencode": [sys.executable, "bin/opencode_cli.py"]
                    }
                }
            ),
            encoding="utf-8",
        )
        return generacion, marca

    def test_el_cli_anterior_recibe_pure_y_conserva_lo_demas(self):
        with tempfile.TemporaryDirectory() as tmp:
            generacion, marca = self._generacion(
                Path(tmp), "--pure --model --format --auto --variant", "stderr"
            )
            argumentos = runtime.vincular(generacion, _cmd())
            self.assertTrue(marca.is_file())
            self.assertEqual(argumentos[0], sys.executable)
            self.assertEqual(
                argumentos[1], str((generacion / "bin" / "opencode_cli.py").resolve())
            )
            self.assertIn("--pure", argumentos)
            self.assertNotIn("--standalone", argumentos)
            self.assertIn("--auto", argumentos)
            self.assertEqual(_valor(argumentos, "--format"), "json")
            self.assertEqual(
                _valor(argumentos, "--model"), "deepseek/deepseek-flash"
            )
            self.assertEqual(_valor(argumentos, "--session"), "ses_anterior")

    def test_el_cli_nuevo_conserva_standalone(self):
        with tempfile.TemporaryDirectory() as tmp:
            generacion, marca = self._generacion(
                Path(tmp), "--standalone --model --format --auto", "stdout"
            )
            argumentos = runtime.vincular(generacion, _cmd())
            self.assertTrue(marca.is_file())
            self.assertIn("--standalone", argumentos)
            self.assertNotIn("--pure", argumentos)
            self.assertIn("--auto", argumentos)
            self.assertEqual(_valor(argumentos, "--format"), "json")
            self.assertEqual(
                _valor(argumentos, "--model"), "deepseek/deepseek-flash"
            )
            self.assertEqual(_valor(argumentos, "--session"), "ses_anterior")

    def test_sin_superficie_compatible_falla_claramente(self):
        with tempfile.TemporaryDirectory() as tmp:
            generacion, marca = self._generacion(
                Path(tmp), "--model --format --auto", "stderr"
            )
            with self.assertRaises(runtime.SuperficieOpenCodeNoAcreditada) as error:
                runtime.vincular(generacion, _cmd())
            self.assertTrue(marca.is_file())
        self.assertIn("--standalone", str(error.exception))
        self.assertIn("--pure", str(error.exception))

    def test_otros_proveedores_no_se_detectan_ni_cambian(self):  # no-roja-en-base: los demas proveedores no cambian; el candado es la ruta deepseek
        cmd = ["opencode", "run", "--auto", "--format", "json", "--model",
               "zai-coding-plan/glm-5.3", "Lee el pedido."]
        with tempfile.TemporaryDirectory() as tmp:
            generacion, marca = self._generacion(
                Path(tmp), "--pure --model --format --auto", "stderr"
            )
            argumentos = runtime.vincular(generacion, cmd)
            self.assertFalse(marca.exists())
            self.assertEqual(argumentos[0], sys.executable)
            self.assertEqual(
                argumentos[1], str((generacion / "bin" / "opencode_cli.py").resolve())
            )
            self.assertNotIn("--standalone", argumentos)
            self.assertNotIn("--pure", argumentos)
            self.assertEqual(
                _valor(argumentos, "--model"), "zai-coding-plan/glm-5.3"
            )


if __name__ == "__main__":
    unittest.main()
