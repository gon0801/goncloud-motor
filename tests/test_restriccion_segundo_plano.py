"""El lanzamiento de claude no deja trabajo en segundo plano y el prompt del
revisor lo dice: el turno termina al responder y la bateria va por ci.correr."""

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from motor.encargos import agentes, suscripciones
from motor.encargos.aislamiento import _entorno
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
                    # Un solo argumento, separado por comas: la opcion es
                    # variadica y un valor con espacios se comeria el prompt.
                    self.assertNotIn(" ", regla)
                    self.assertEqual(
                        regla,
                        "Bash(run_in_background:true),Monitor,CronCreate,"
                        "ScheduleWakeup,Workflow",
                    )
                    self.assertEqual(argv[posicion + 2], "--dangerously-skip-permissions")
                    self.assertTrue(argv[-1].startswith("Lee el pedido"))

    def test_la_regla_niega_una_llamada_de_fondo_y_no_bash_entero(self):
        argv = _argv("responsable", "nueva")
        regla = argv[argv.index("--disallowedTools") + 1]
        self.assertNotEqual(regla.strip(), "Bash")
        self.assertIn("Monitor", regla.split(","))


class EntornoDelLanzamiento(unittest.TestCase):
    """El subagente (Agent/Task) arranca en segundo plano por defecto y ninguna
    regla de --disallowedTools lo cubre: el entorno del lanzamiento trae
    CLAUDE_CODE_DISABLE_BACKGROUND_TASKS, medida en el binario de este host."""

    def _entorno_de_lanzamiento(self) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as t:
            with mock.patch.object(
                suscripciones,
                "leer_cuenta",
                return_value={
                    "accessToken": "sk-ant-oat01-prueba",
                    "expiresAt": (time.time() + 3600) * 1000,
                },
            ):
                env = _entorno(Path(t), True, ["claude", "--print"], agente="claude")
        return env

    def test_el_entorno_del_lanzamiento_apaga_las_tareas_de_fondo(self):
        env = self._entorno_de_lanzamiento()
        self.assertEqual(env.get("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"), "1")


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
