"""El check `jaula-linux` corre las pruebas de la jaula de Linux y del agente en WSL (#122 F1).

Esas pruebas se saltan donde no hay bubblewrap: si ningún check las corre, el salto es
silencioso. Esta prueba falla si una de ellas sale del comando de CI."""

import unittest
from pathlib import Path

FLUJO = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "pruebas.yml"
PRUEBAS = (
    "tests.test_jaula_linux",
    "tests.test_jaula_wsl",
    "tests.test_rutas_wsl",
    "tests.test_ci_jaula",
)


class PruebasDeLaJaulaEnCi(unittest.TestCase):
    def comando(self) -> str:
        lineas = [
            linea for linea in FLUJO.read_text(encoding="utf-8").splitlines()
            if "python -m unittest" in linea
        ]
        self.assertEqual(len(lineas), 1, lineas)
        return lineas[0]

    def test_el_check_corre_cada_archivo_de_pruebas_de_la_jaula_y_de_wsl(self):
        comando = self.comando().split()
        for prueba in PRUEBAS:
            self.assertIn(prueba, comando)
            self.assertTrue((FLUJO.parents[2] / (prueba.replace(".", "/") + ".py")).is_file())

    def test_el_check_exige_bubblewrap_en_lugar_de_saltar(self):
        self.assertIn('MOTOR_EXIGE_BWRAP: "1"', FLUJO.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
