"""A.4: los registros reales de las revisiones de MOTOR 03, recortados, pasan por el juez.

`ops/pstack/recorta_registro.py` dejó en `tests/datos/pstack/motor-03/` solo las llamadas a
herramientas del agente principal de cada A-revisar-N; `fuentes.json` nombra el original de cada
uno. Los registros conservan las llamadas a subagentes; su cantidad no impone una cuota.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from motor import pstack as ps

DATOS = Path(__file__).resolve().parent / "datos" / "pstack" / "motor-03"
EXIGENCIA = ps.Exigencia(skills=("poteto-mode", "interrogate"), min_subagentes=2)


def _fuentes() -> list[dict]:
    return json.loads((DATOS / "fuentes.json").read_text(encoding="utf-8"))


class TestJuezMotor03(unittest.TestCase):
    def test_hay_un_registro_por_revision_de_motor03(self):
        fuentes = _fuentes()
        self.assertEqual(
            [f["salida"] for f in fuentes],
            [f"A-revisar-{n}.jsonl" for n in range(1, 10)],
        )
        for f in fuentes:
            self.assertIn(f["cli"], ps.FORMATOS)

    def test_cada_registro_recortado_cumple_la_exigencia_de_revisar_en_gate(self):
        for f in _fuentes():
            with self.subTest(registro=f["salida"]):
                h = ps.huella(
                    (DATOS / f["salida"]).read_text(encoding="utf-8"), f["cli"]
                )
                self.assertTrue(h.legible)
                self.assertGreaterEqual(len(h.subagentes), 2)
                self.assertEqual(ps.juzga(h, EXIGENCIA), ps.Cumple())

    def test_el_primero_sin_sus_llamadas_a_agent_no_falla_por_cantidad(self):
        f = _fuentes()[0]
        self.assertEqual((f["salida"], f["cli"]), ("A-revisar-1.jsonl", "claude"))
        texto = (DATOS / f["salida"]).read_text(encoding="utf-8")
        self.assertIn('"name": "Agent"', texto, "el registro sí trae subagentes")
        sin_agent = "\n".join(
            x for x in texto.splitlines() if '"name": "Agent"' not in x
        )
        h = ps.huella(sin_agent, "claude")
        self.assertTrue(h.legible, "un lector roto no puede saltarse la prueba")
        v = ps.juzga(h, EXIGENCIA)
        self.assertEqual(v, ps.Cumple())


class TestRecortaSaneaTokens(unittest.TestCase):
    def test_un_comando_con_prefijo_de_token_sale_saneado(self):
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops" / "pstack"))
        import recorta_registro as rr

        try:
            comando = "grep -cE '^\\+.*(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9]{20,})' diff"
            registro = json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Bash",
                                "input": {"command": comando},
                            }
                        ]
                    },
                }
            )
            salida = rr.recorta(registro + "\n", "claude")
            self.assertNotIn("ghp_", salida)
            self.assertNotIn("github_pat_", salida)
            self.assertIn('"name": "Bash"', salida, "la llamada se conserva")
        finally:
            sys.path.remove(str(Path(__file__).resolve().parents[1] / "ops" / "pstack"))


if __name__ == "__main__":
    unittest.main()
