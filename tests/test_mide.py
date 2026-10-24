"""C.6: `ops/pstack/mide.sh` arma el encargo con el código del motor y lanza con su mismo prompt."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from motor import effects as ef
from motor import pstack as ps
from motor.conf import load_conf

REPO = Path(__file__).resolve().parent.parent
MIDE_SH = REPO / "ops" / "pstack" / "mide.sh"
CONF_REAL = os.environ.get("MOTOR_CONF_REAL", "")


def _arma_py() -> str:
    return (
        MIDE_SH.read_text(encoding="utf-8")
        .split("<<'PYENC'\n")[1]
        .split("\nPYENC\n")[0]
    )


@unittest.skipUnless(CONF_REAL, "sin MOTOR_CONF_REAL")
class TestMide(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test-mide-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.casa = self.tmp / "casa"
        self.env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("MOTOR_")},
            "MOTOR_CONF_REAL": CONF_REAL,
            "MIDE_HOME": str(self.casa),
        }

    def _efectos(self) -> ef.Effects:
        return ef.Effects(
            SimpleNamespace(
                home=self.casa,
                conf=load_conf(Path(CONF_REAL)),
                ws=SimpleNamespace(rama=lambda p, b: f"motor/{p}/{b}"),
            )
        )

    def _anexo_del_motor(self, cli: str) -> str:
        fx = self._efectos()
        d = self.tmp / "esperado"
        d.mkdir(exist_ok=True)
        info = fx._prepara_pstack(d, "gate", "implementar", cli, self.tmp, "x", False)
        return fx._anexo_guias(info)

    def test_encargo_trae_el_anexo_del_motor_y_el_prompt_compartido(self):
        datos = REPO / "tests" / "datos" / "pstack"
        antes = sorted(str(p) for p in datos.rglob("*"))
        r = subprocess.run(
            ["bash", str(MIDE_SH), "--encargo"],
            capture_output=True,
            text=True,
            env=self.env,
            timeout=120,
            check=False,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(antes, sorted(str(p) for p in datos.rglob("*")))
        marca_encargo, marca_prompt = "=== encargo ===\n", "=== prompt ===\n"
        self.assertTrue(r.stdout.startswith(marca_encargo))
        encargo, prompt = r.stdout[len(marca_encargo) :].split(marca_prompt)
        anexo = self._anexo_del_motor("claude")
        self.assertTrue(
            encargo.endswith(anexo), "el encargo termina en el anexo del motor"
        )
        self.assertEqual(encargo.count("\n## Anexo: tus guías\n"), 1)
        brief = self.casa / "intento-claude" / "brief.md"
        self.assertEqual(prompt, ef.prompt_de_encargo(str(brief), encargo) + "\n")
        self.assertEqual(brief.read_text(encoding="utf-8"), encargo)

    def test_los_datos_que_viajan_traen_el_encargo_de_cada_cli_con_el_anexo_del_motor(
        self,
    ):
        arma = self.tmp / "arma.py"
        arma.write_text(_arma_py(), encoding="utf-8")
        r = subprocess.run(
            [
                sys.executable,
                "-I",
                str(arma),
                str(REPO),
                CONF_REAL,
                str(self.casa),
                "datos",
            ],
            capture_output=True,
            text=True,
            env=self.env,
            timeout=120,
            check=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        datos = json.loads(base64.b64decode(r.stdout))
        self.assertEqual(set(datos["clis"]), set(ps.FORMATOS))
        for cli, x in datos["clis"].items():
            with self.subTest(cli=cli):
                self.assertTrue(x["encargo"].endswith(self._anexo_del_motor(cli)))
                self.assertEqual(
                    x["prompt"],
                    ef.prompt_de_encargo(datos["marca_brief"], x["encargo"]),
                )
                self.assertIn("al menos 2 subagente(s)", x["encargo"])

    def test_el_script_no_escribe_ningun_titulo_del_encargo(self):
        texto = MIDE_SH.read_text(encoding="utf-8")
        for titulo in (
            "## Anexo",
            "## Cómo trabajas",
            "## Cómo se entrega",
            "Lo que manda el motor",
            "ejecútalo completo",
        ):
            self.assertNotIn(titulo, texto)


class TestPromptCompartido(unittest.TestCase):
    def test_el_prompt_dice_la_ruta_el_tamano_y_la_herramienta_de_lectura(self):
        p = ef.prompt_de_encargo("/x/brief.md", "a" * 2048)
        self.assertIn("/x/brief.md (2 KB)", p)
        self.assertIn("herramienta de lectura de archivos, no con `cat`", p)
        self.assertIn("ejecútalo completo sin preguntar nada", p)

    def test_start_attempt_usa_la_funcion_compartida(self):
        fuente = (REPO / "motor" / "effects.py").read_text(encoding="utf-8")
        self.assertEqual(fuente.count("ejecútalo completo sin preguntar nada"), 1)


if __name__ == "__main__":
    unittest.main()
