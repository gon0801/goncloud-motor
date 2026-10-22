"""Lo que la máquina no tiene: `python` en la Mac y la Mini, o cualquier comando que el plan pida."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from motor import machine as m
from motor.effects import comandos_faltantes, con_python
from motor.notify import render
from tests.test_puro import PLAN, entregado, ev, parse_plan, pol


@unittest.skipIf(os.name == "nt", "en Windows `python` siempre existe")
class ConPythonTest(unittest.TestCase):
    def test_sin_python_pero_con_python3_el_motor_pone_uno(self):
        """INGESTA 01, bloque C: la Acepta decía `python -c ...` y la Mini solo tiene python3."""
        with tempfile.TemporaryDirectory() as t:
            Path(t, "python3").symlink_to(sys.executable)
            env = con_python({"PATH": t})
            r = subprocess.run(
                ["python", "-c", "print('hola')"],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual((r.returncode, r.stdout), (0, "hola\n"), r.stderr)

    def test_con_python_no_toca_el_path(self):
        with tempfile.TemporaryDirectory() as t:
            Path(t, "python").symlink_to(sys.executable)
            Path(t, "python3").symlink_to(sys.executable)
            self.assertEqual(con_python({"PATH": t})["PATH"], t)


class ComandoFaltanteTest(unittest.TestCase):
    def test_lee_bash_y_windows(self):
        self.assertEqual(
            comandos_faltantes("/bin/bash: python: command not found\n"), ["python"]
        )
        self.assertEqual(
            comandos_faltantes(
                "'test' is not recognized as an internal or external command,\n"
            ),
            ["test"],
        )
        self.assertEqual(comandos_faltantes("AssertionError: 1 != 0"), [])

    def test_avisa_una_vez_por_comando_y_no_por_ronda(self):
        p, now = parse_plan(PLAN), time.time()

        def ronda(sha, n):
            return entregado(sha=sha, n=n) + [
                ev(
                    "Verified",
                    "A",
                    {
                        "sha": sha,
                        "ok": False,
                        "detalle": "A.1 salió 127",
                        "falta_comando": ["python"],
                    },
                )
            ]

        def avisos(evs):
            return [
                a
                for a in m.decide(m.fold(p, evs), pol(), now)
                if isinstance(a, m.Notify) and a.template == "atorado"
            ]

        (a,) = avisos(ronda("a" * 40, 1))
        motivo = dict(a.campos)["motivo"]
        self.assertIn("python", motivo)
        self.assertIn(
            "Motivo:", render("atorado", dict(a.campos), "Mini", "INGESTA 01")
        )
        evs = ronda("a" * 40, 1) + [ev("NoticeQueued", "A", {"key": a.key})]
        self.assertEqual(avisos(evs + ronda("b" * 40, 2)), [], "una vez por comando")


if __name__ == "__main__":
    unittest.main()
