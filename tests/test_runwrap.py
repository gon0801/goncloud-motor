"""El envoltorio que corre a cada agente sin ventana."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
import unittest.mock
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
            "pathlib.Path(os.environ['MOTOR_ATTEMPT_DIR'], 'result.json').write_text("
            '\'{"bloqueado": "se quedó pegado"}\')\n'
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

    def test_agente_que_no_escribe_nada_no_toco_la_carpeta(self):
        o, _ = self.corre("print('nada')")
        self.assertIs(o["toco_carpeta"], False)

    def test_un_cambio_de_un_segundo_cuenta_aunque_salga_antes_del_escaneo(self):
        o, dura = self.corre("open('nuevo.txt', 'w').write('x')")
        self.assertLess(dura, 2)
        self.assertIs(o["toco_carpeta"], True)

    def test_sin_shims_quita_toda_carpeta_con_llamada_guard(self):
        t = Path(tempfile.mkdtemp(prefix="runwrap-shims-"))
        ajena, otra = t / "shims-de-otro-motor", t / "bin"
        for c in (ajena, otra):
            c.mkdir()
        (ajena / "llamada_guard.py").write_text("")
        for c in (ajena, otra):
            f = c / "claude"
            f.write_text("#!/bin/sh\n")
            f.chmod(0o755)
        cmd = runwrap._sin_shims(
            ["claude"], {"PATH": os.pathsep.join([str(ajena), str(otra)])}
        )
        self.assertEqual(cmd, [str(otra / "claude")])

    def test_mtime_salta_carpetas_pesadas(self):
        t = Path(tempfile.mkdtemp(prefix="runwrap-mtime-"))
        (t / "real.txt").write_text("x")
        os.utime(t / "real.txt", (1000, 1000))
        for nombre in (".git", ".venv", "node_modules", "__pycache__"):
            d = t / nombre
            d.mkdir()
            (d / "x.txt").write_text("x")
            os.utime(d / "x.txt", (2000, 2000))
        self.assertEqual(runwrap._mtime_arbol(t), 1000)

    def test_avance_se_revisa_cada_10s_no_cada_02(self):
        llamadas = []
        real = runwrap._mtime_arbol

        def cuenta(raiz):
            llamadas.append(1)
            return real(raiz)

        with unittest.mock.patch.object(runwrap, "_mtime_arbol", cuenta):
            self.corre("import time; time.sleep(3)")
        self.assertLessEqual(len(llamadas), 2)

    def test_foto_colgada_no_apaga_el_timeout(self):
        t = Path(tempfile.mkdtemp(prefix="runwrap-foto-"))
        d, cwd = t / "intento", t / "w"
        d.mkdir()
        cwd.mkdir()
        inicio = time.time()
        with unittest.mock.patch.object(
            runwrap, "foto_wip", lambda *a, **k: time.sleep(30)
        ):
            runwrap.main(
                [
                    "--dir", str(d), "--cwd", str(cwd), "--timeout", "2",
                    "--sin-avance", "60", "--cierre", "1", "--wip-rama", "wip",
                    "--wip-cada", "1",
                    "--", sys.executable, "-c", "import time; time.sleep(60)",
                ]
            )  # fmt: skip
        o = json.loads((d / "outcome.json").read_text())
        self.assertTrue(o["timed_out"])
        self.assertLess(time.time() - inicio, 15)


if __name__ == "__main__":
    unittest.main()
