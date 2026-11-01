"""El job `windows` corre las pruebas propias de Windows que hoy se saltan fuera de Windows.

Esas pruebas se saltan donde no hay Windows (y en la PC las prueba ops/prueba-pc.sh):
si ningún job las corre, el salto es silencioso. Esta prueba falla si el job deja de
correrlas o de poner MOTOR_EXIGE_WINDOWS=1, con el que un salto es un fallo.
"""

import os
import re
import unittest
from pathlib import Path
from unittest import mock

from tests import entorno

FLUJO = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "pruebas.yml"
PRUEBAS = (
    "tests.test_windows",
    "tests.test_windows_identidad",
    "tests.test_windows_wsl",
    "tests.test_relanzamiento_pc",
    "tests.test_prepara_pc",
    "tests.test_director_capacidades",
)


class PruebasDeWindowsEnCi(unittest.TestCase):
    def job(self, nombre: str) -> list[str]:
        lineas = FLUJO.read_text(encoding="utf-8").splitlines()
        inicio = lineas.index(f"  {nombre}:")
        fin = next(
            (i for i in range(inicio + 1, len(lineas)) if re.match(r"  \S.*:$", lineas[i])),
            len(lineas),
        )
        return lineas[inicio:fin]

    def comando(self) -> str:
        lineas = [linea for linea in self.job("windows") if "python -m unittest" in linea]
        self.assertEqual(len(lineas), 1, lineas)
        return lineas[0]

    def test_el_job_corre_cada_modulo_de_pruebas_de_windows(self):
        comando = self.comando().split()
        for prueba in PRUEBAS:
            self.assertIn(prueba, comando)
            self.assertTrue((FLUJO.parents[2] / (prueba.replace(".", "/") + ".py")).is_file())

    def test_el_job_corre_en_windows_y_exige_windows_en_lugar_de_saltar(self):
        self.assertIn("    runs-on: windows-latest", self.job("windows"))
        self.assertIn('      MOTOR_EXIGE_WINDOWS: "1"', self.job("windows"))

    def test_los_modulos_propios_de_windows_usan_requiere_windows(self):
        for prueba in ("tests.test_windows", "tests.test_windows_wsl"):
            fuente = (
                FLUJO.parents[2] / (prueba.replace(".", "/") + ".py")
            ).read_text(encoding="utf-8")
            self.assertIn("requiere_windows", fuente)
            self.assertNotIn('skipUnless(os.name == "nt"', fuente)

    def test_con_la_marca_un_salto_es_fallo_y_sin_ella_es_salto_con_motivo(self):
        clase = type("Propia", (unittest.TestCase,), {"test_x": lambda self: None})
        with mock.patch.object(entorno, "EXIGE_WINDOWS", True), mock.patch.object(
            os, "name", "posix"
        ):
            decorada = entorno.requiere_windows(clase)
            with self.assertRaises(AssertionError):
                decorada.setUpClass()
        with mock.patch.object(entorno, "EXIGE_WINDOWS", False), mock.patch.object(
            os, "name", "posix"
        ):
            saltada = entorno.requiere_windows(clase)
            self.assertTrue(saltada.__unittest_skip__)
            self.assertIn("job windows", saltada.__unittest_skip_why__)


if __name__ == "__main__":
    unittest.main()
