"""Por OpenCode Go solo DeepSeek; el rechazo ocurre al admitir el encargo."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from motor.encargos.seleccion import solicitar_modelos
from motor.integraciones import cli as director_cli


def _encargar(base: Path, responsable: str) -> list[str]:
    return [
        "encargar",
        "--base",
        str(base),
        "--director",
        "hermes",
        "--via",
        "durable",
        "--canal",
        "hermes",
        "--clave",
        "modelo-opencode",
        "--texto",
        "Un cambio cualquiera",
        "--responsable",
        responsable,
    ]


class CatalogoCerradoDeOpenCodeGo(unittest.TestCase):
    def test_rechaza_otro_modelo_del_plan_al_encargar(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            with contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(
                    director_cli.main(_encargar(base, "opencode-go/gemini-4-argon")),
                    1,
                )
            self.assertIn("opencode-go/deepseek-v4.1-flash", error.getvalue())
            self.assertIn("opencode-go/gemini-4-argon", error.getvalue())

    def test_admite_el_unico_modelo_autorizado(self):
        self.assertEqual(
            solicitar_modelos("opencode-go/deepseek-v4.1-flash", ""),
            (
                (
                    "responsable",
                    "opencode",
                    "opencode-go/deepseek-v4.1-flash",
                ),
            ),
        )

    def test_no_cambia_otros_proveedores_de_opencode(self):
        self.assertEqual(
            solicitar_modelos("zai-coding-plan/glm-5", ""),
            (("responsable", "opencode", "zai-coding-plan/glm-5"),),
        )

    def test_el_encargo_autorizado_se_acepta(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            with contextlib.redirect_stdout(io.StringIO()) as salida:
                self.assertEqual(
                    director_cli.main(
                        _encargar(base, "opencode-go/deepseek-v4.1-flash")
                    ),
                    0,
                )
            self.assertIn("encargo", salida.getvalue())


if __name__ == "__main__":
    unittest.main()
