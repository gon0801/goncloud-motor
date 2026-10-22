"""El envoltorio que corre a cada agente sin ventana."""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from motor import runwrap


class CierreTrasResultadoTest(unittest.TestCase):
    def corre(self, script: str, sin_avance: float = 30) -> tuple[dict, float]:
        t = Path(tempfile.mkdtemp(prefix="runwrap-"))
        d, cwd = t / "intento", t / "w"
        d.mkdir()
        cwd.mkdir()
        inicio = time.time()
        runwrap.main(
            [
                "--dir", str(d), "--cwd", str(cwd), "--timeout", "60",
                "--sin-avance", str(sin_avance), "--cierre", "1",
                "--", sys.executable, "-c", script,
            ]
        )  # fmt: skip
        return json.loads((d / "outcome.json").read_text()), time.time() - inicio

    def test_entrego_y_no_sale_se_cierra_como_terminado(self):
        """INGESTA 01, bloque D: Grok dejaba result.json y su proceso no salía; a los 45 min se
        mataba "sin avance" y la entrega se perdía."""
        o, dura = self.corre(
            "import os, time, pathlib\n"
            "pathlib.Path(os.environ['MOTOR_ATTEMPT_DIR'], 'result.json').write_text('{}')\n"
            "time.sleep(60)\n"
        )
        self.assertLess(dura, 20)
        self.assertEqual(
            (o["exit"], o["no_progress"], o["cerrado_tras_resultado"]), (0, False, True)
        )

    def test_sin_resultado_sigue_esperando_al_agente(self):
        o, _ = self.corre("import time; time.sleep(3)")
        self.assertEqual((o["exit"], o["cerrado_tras_resultado"]), (0, False))

    def test_colgado_sin_resultado_sigue_siendo_sin_avance(self):
        o, _ = self.corre("import time; time.sleep(60)", sin_avance=2)
        self.assertEqual((o["no_progress"], o["cerrado_tras_resultado"]), (True, False))


if __name__ == "__main__":
    unittest.main()
