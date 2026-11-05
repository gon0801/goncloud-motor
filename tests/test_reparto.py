"""La división de la batería no pierde ni repite pruebas."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from tests import reparto

RAIZ = Path(__file__).resolve().parents[1]
FLUJO = RAIZ / ".github" / "workflows" / "pruebas.yml"


class Division(unittest.TestCase):
    def test_una_lista_incompleta_no_pasa(self):
        with self.assertRaises(SystemExit) as ctx:
            reparto.verificar(
                ["tests.test_a.C.test_uno", "tests.test_b.C.test_dos"],
                ["tests.test_a.C.test_uno"],
            )
        self.assertIn("faltan=", str(ctx.exception))
        self.assertIn("tests.test_b.C.test_dos", str(ctx.exception))

    def test_una_prueba_repetida_no_pasa(self):
        with self.assertRaises(SystemExit) as ctx:
            reparto.verificar(
                ["tests.test_a.C.test_uno"],
                ["tests.test_a.C.test_uno", "tests.test_a.C.test_uno"],
            )
        self.assertIn("repetidas=", str(ctx.exception))
        self.assertIn("tests.test_a.C.test_uno", str(ctx.exception))

    def test_e2e_queda_solo_y_un_modulo_nuevo_entra_en_una_parte(self):
        base = ["tests.test_motor_e2e", "tests.test_a", "tests.test_b"]
        partes = reparto.partir(base)
        self.assertEqual(partes[0], ("tests.test_motor_e2e",))
        self.assertEqual(
            sorted(modulo for parte in partes for modulo in parte),
            sorted(base),
        )
        nuevo = reparto.partir([*base, "tests.test_nueva"])
        self.assertEqual(nuevo[0], ("tests.test_motor_e2e",))
        lugares = [
            i for i, parte in enumerate(nuevo) if "tests.test_nueva" in parte
        ]
        self.assertEqual(len(lugares), 1)
        self.assertNotEqual(lugares[0], 0)

    def test_el_discover_real_no_pierde_ni_repite(self):
        reparto.comprobar()

    def test_un_import_roto_no_deja_la_parte_en_verde(self):
        class Rota:
            def id(self):
                return "unittest.loader._FailedTest.tests.test_roto"

        Rota.__module__ = "unittest.loader"
        falsa = [Rota()]
        with mock.patch.object(
            unittest.defaultTestLoader, "discover", return_value=falsa
        ):
            with self.assertRaises(SystemExit) as ctx:
                reparto.descubrir()
            self.assertIn("no importa", str(ctx.exception))
            self.assertIn("test_roto", str(ctx.exception))
            with self.assertRaises(SystemExit) as ctx:
                reparto.correr(1)
            self.assertIn("no importa", str(ctx.exception))

    def test_las_otras_partes_no_superan_el_peso_de_e2e(self):
        partes = reparto.partir(reparto.modulos_descubiertos())
        cargas = [sum(reparto.peso(modulo) for modulo in parte) for parte in partes]
        self.assertLessEqual(max(cargas[1:]), cargas[0])

    def test_la_bateria_solo_esta_verde_si_las_partes_pasaron(self):
        reparto.exigir("success")
        with self.assertRaises(SystemExit) as ctx:
            reparto.exigir("failure")
        self.assertIn("failure", str(ctx.exception))

    def test_el_workflow_no_lista_los_modulos(self):
        texto = FLUJO.read_text(encoding="utf-8")
        self.assertNotIn("test_motor_e2e", texto)
        self.assertIn("tests.reparto correr --parte", texto)
        self.assertIn("tests.reparto comprobar", texto)
        self.assertIn("tests.reparto exigir", texto)
        self.assertEqual(texto.count("bateria-parte-${{ matrix.parte }}"), 1)
        self.assertIn("parte: [0, 1, 2, 3]", texto)


if __name__ == "__main__":
    unittest.main()
