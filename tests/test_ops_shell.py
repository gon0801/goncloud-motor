"""Las pruebas de shell de ops/tests corren en un job del CI.

El job `ops-tests` ejecuta `ops/tests/correr.sh`, que recorre cada
`ops/tests/test-*.sh`. Una prueba que no pueda correr en Linux se declara en
`ops/tests/omitidas.txt` con su motivo; no se quita. Esta prueba falla si el
job, el runner o la cobertura de archivos desaparecen.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
OPS = RAIZ / "ops" / "tests"
FLUJO = RAIZ / ".github" / "workflows" / "pruebas.yml"
RUNNER = OPS / "correr.sh"
OMITIDAS = OPS / "omitidas.txt"


def _job(nombre: str) -> list[str]:
    lineas = FLUJO.read_text(encoding="utf-8").splitlines()
    inicio = lineas.index(f"  {nombre}:")
    fin = next(
        (i for i in range(inicio + 1, len(lineas)) if re.match(r"  \S.*:$", lineas[i])),
        len(lineas),
    )
    return lineas[inicio:fin]


def _omitidas() -> dict[str, str]:
    salida = {}
    for linea in OMITIDAS.read_text(encoding="utf-8").splitlines():
        if not linea.strip() or linea.lstrip().startswith("#"):
            continue
        nombre, _, motivo = linea.partition("|")
        salida[nombre.strip()] = motivo.strip()
    return salida


class LasPruebasDeShellDeOpsCorrenEnCi(unittest.TestCase):
    def test_el_flujo_tiene_el_job_y_corre_el_runner(self):
        self.assertTrue(RUNNER.is_file())
        self.assertIn("bash ops/tests/correr.sh", "\n".join(_job("ops-tests")))

    def test_el_runner_recorre_cada_prueba_de_shell(self):
        texto = RUNNER.read_text(encoding="utf-8")
        self.assertIn('"$AQUI"/test-*.sh', texto)
        self.assertIn("omitidas.txt", texto)

    def test_cada_prueba_esta_cubierta_y_las_omitidas_traen_motivo(self):
        pruebas = {p.name for p in OPS.glob("test-*.sh")}
        self.assertTrue(pruebas)
        omitidas = _omitidas()
        self.assertEqual(len(omitidas), len(set(omitidas)), omitidas)
        for nombre, motivo in omitidas.items():
            self.assertIn(nombre, pruebas, nombre)
            self.assertTrue(motivo, nombre)


if __name__ == "__main__":
    unittest.main()
