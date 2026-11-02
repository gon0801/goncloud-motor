"""El lanzamiento de claude no deja trabajo en segundo plano y el prompt del
revisor lo dice: el turno termina al responder y la bateria va por ci.correr."""

import unittest
from pathlib import Path
from types import SimpleNamespace

from motor.encargos import agentes
from motor.encargos.comprobacion import instrucciones_revision
from motor.encargos.dominio import AlcanceRevision

SESION = "00000000-0000-4000-8000-000000000001"


def _enc(rol: str, sesion: str = "") -> SimpleNamespace:
    return SimpleNamespace(rol=rol, sesion_responsable=sesion, sesion_revisor=sesion)


def _argv(rol: str, modo: str) -> list[str]:
    enc = _enc(rol, "ses-previa")
    lanzador = agentes._lanzador("claude", "anthropic/claude-sonnet-5-5", modo)
    return lanzador(enc, Path("/p/prompt.md"), Path("/e"), SESION)


class RestriccionSegundoPlano(unittest.TestCase):
    def test_argv_de_claude_niega_segundo_plano_en_los_cuatro_casos(self):
        for rol in ("responsable", "revisor"):
            for modo in ("nueva", "reanudada"):
                with self.subTest(rol=rol, modo=modo):
                    argv = _argv(rol, modo)
                    self.assertEqual(argv[0], "claude")
                    if modo == "reanudada":
                        self.assertIn("--resume", argv)
                    else:
                        self.assertIn("--session-id", argv)
                    posicion = argv.index("--disallowedTools")
                    regla = argv[posicion + 1]
                    self.assertIn("Bash(run_in_background:true)", regla)
                    self.assertIn("Monitor", regla)
                    self.assertEqual(argv[posicion + 2], "--dangerously-skip-permissions")
                    self.assertTrue(argv[-1].startswith("Lee el pedido"))

    def test_la_regla_niega_una_llamada_de_fondo_y_no_bash_entero(self):
        argv = _argv("responsable", "nueva")
        regla = argv[argv.index("--disallowedTools") + 1]
        self.assertNotEqual(regla.strip(), "Bash")
        self.assertIn("Monitor", regla.split(","))


class PromptRevisor(unittest.TestCase):
    def test_el_prompt_nombra_el_turno_que_termina_y_la_bateria_por_ci(self):
        texto = instrucciones_revision(
            AlcanceRevision(clase="completa", version="v1", base_version=""),
            Path("/p/resultado.json"),
        )
        plano = " ".join(texto.split())
        self.assertTrue(plano.startswith("Tu turno termina cuando respondes"))
        self.assertIn("nadie te despierta", plano)
        self.assertIn("no dejes nada corriendo en segundo plano", plano)
        self.assertIn("La bateria completa no se corre en local", plano)
        self.assertIn("se pide con la inspeccion ci.correr", plano)


if __name__ == "__main__":
    unittest.main()
