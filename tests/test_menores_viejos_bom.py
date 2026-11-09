"""Dos lecturas de archivo del motor aceptan el BOM UTF-8 de Windows.

El 9-nov en `4c11938` un `result.json` válido con BOM no se congelaba como
`entrega.json` y un `cuerpo-pr.md` con BOM se publicaba con U+FEFF al inicio.
Ambas pruebas llaman al lector real y comprueban el efecto observable.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from motor import entrega
from tests.dobles import github_por_numero
from tests.test_flujo_revisiones import _entrega_pr

SHA = "a" * 40
BOM = b"\xef\xbb\xbf"


class ResultadoConBom(unittest.TestCase):
    def test_un_result_json_valido_con_bom_se_acepta_y_se_congela(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            d = Path(tmp) / "intento"
            d.mkdir()
            crudo = BOM + json.dumps(
                {
                    "sha": SHA,
                    "veredicto": "APROBADO",
                    "bloqueantes": [],
                    "notas": [],
                }
            ).encode("utf-8")
            (d / "result.json").write_bytes(crudo)
            espera = entrega.Espera(sha=SHA)

            self.assertTrue(
                espera.acepta(crudo, d),
                "un result.json válido con BOM no se acepta",
            )
            self.assertGreater(
                espera.vigila(d, d),
                0,
                "vigila no congela un result.json válido con BOM",
            )

            congelada = (d / "entrega.json").read_bytes()
            self.assertEqual(json.loads(congelada.decode("utf-8-sig"))["sha"], SHA)
            self.assertFalse((d / "result.ajeno.json").exists())


class CuerpoDelPrConBom(unittest.TestCase):
    def test_el_cuerpo_con_bom_se_publica_sin_ufeFF(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "bom-cuerpo", github_por_numero, nivel="pr"
            )
            interno = r.espacio / ".interno"
            interno.mkdir(exist_ok=True)
            (interno / "cuerpo-pr.md").write_bytes(
                BOM + "Cuerpo con BOM del responsable.".encode("utf-8")
            )

            e = vuelta("uno")

            estado = json.loads((vuelta.gh.d / "github.json").read_text())
            cuerpo = next(
                pr for pr in estado["prs"].values() if pr["numero"] == e.pr_numero
            )["cuerpo"]
            self.assertNotIn(
                "\ufeFF", cuerpo, "el BOM se publicó como U+FEFF en el cuerpo del PR"
            )
            self.assertIn("Cuerpo con BOM del responsable.", cuerpo)
