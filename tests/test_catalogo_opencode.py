"""Por OpenCode Go solo DeepSeek; el rechazo ocurre al admitir el encargo."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from motor.encargos import agentes
from motor.encargos.seleccion import solicitar_modelos
from motor.integraciones import cli as director_cli


def _encargar(base: Path, responsable: str, revisor: str = "") -> list[str]:
    argv = [
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
    if revisor:
        argv.extend(["--revisor", revisor])
    return argv


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

    def test_admite_el_unico_modelo_autorizado(self):  # no-roja-en-base: la admisión de DeepSeek y de zai ya existía; el candado nuevo es el rechazo
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

    def test_no_cambia_otros_proveedores_de_opencode(self):  # no-roja-en-base: la admisión de DeepSeek y de zai ya existía; el candado nuevo es el rechazo
        self.assertEqual(
            solicitar_modelos("zai-coding-plan/glm-5", ""),
            (("responsable", "opencode", "zai-coding-plan/glm-5"),),
        )

    def test_el_encargo_autorizado_se_acepta(self):  # no-roja-en-base: la admisión de DeepSeek y de zai ya existía; el candado nuevo es el rechazo
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


class DeepSeekPorSuApiPropia(unittest.TestCase):
    """La API propia de DeepSeek corre por OpenCode sin abrir el plan Go."""

    def test_admite_flash_con_opus_de_revisor(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()) as salida:
                self.assertEqual(
                    director_cli.main(
                        _encargar(
                            Path(tmp) / "base",
                            "deepseek/deepseek-flash",
                            "anthropic/claude-opus-5-5",
                        )
                    ),
                    0,
                )
            self.assertIn("encargo", salida.getvalue())

    def test_el_lanzador_usa_la_superficie_del_cli_actual(self):
        enc = SimpleNamespace(
            rol="responsable",
            sesion_responsable="ses_anterior",
            sesion_revisor="",
        )
        argv = agentes._lanzador(
            "opencode", "deepseek/deepseek-flash", "reanudada"
        )(enc, Path("/intento/prompt.md"), Path("/intento"), "sesion-motor")
        self.assertEqual(
            argv[:9],
            [
                "opencode",
                "run",
                "--standalone",
                "--auto",
                "--format",
                "json",
                "--model",
                "deepseek/deepseek-flash",
                "--session",
            ],
        )
        self.assertEqual(argv[9], "ses_anterior")
        self.assertNotIn("--pure", argv)
        self.assertNotIn("--variant", argv)
        self.assertEqual(
            argv[-1],
            "Lee el pedido y las instrucciones en "
            "/intento/prompt.md. Ejecuta el trabajo autorizado.",
        )

    def test_sin_sesion_previa_estrena_sesion(self):  # no-roja-en-base: la ausencia de --session ya existía; el candado nuevo es la superficie del CLI
        enc = SimpleNamespace(
            rol="responsable", sesion_responsable="", sesion_revisor=""
        )
        argv = agentes._lanzador("opencode", "deepseek/deepseek-flash", "nueva")(
            enc, Path("/intento/prompt.md"), Path("/intento"), "sesion-motor"
        )
        self.assertNotIn("--session", argv)


if __name__ == "__main__":
    unittest.main()
