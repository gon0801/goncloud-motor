"""Qué jobs toca un cambio y cómo el workflow los salta sin romper la protección.

El filtro va en cada job, no en `paths` del disparador: un job saltado por su
`if` cuenta como éxito para la protección de rama; un workflow saltado deja sus
checks pendientes y bloquea el merge.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from motor.encargos import trabajos_ci

RAIZ = Path(__file__).resolve().parents[1]
FLUJO = RAIZ / ".github" / "workflows" / "pruebas.yml"


def _job(nombre: str) -> list[str]:
    lineas = FLUJO.read_text(encoding="utf-8").splitlines()
    inicio = lineas.index(f"  {nombre}:")
    fin = next(
        (i for i in range(inicio + 1, len(lineas)) if re.match(r"  \S.*:$", lineas[i])),
        len(lineas),
    )
    return lineas[inicio:fin]


class Dependencias(unittest.TestCase):
    def test_ops_tests_solo_depende_de_ops(self):
        self.assertTrue(trabajos_ci.afecta("ops-tests", ["ops/senal-kuma.sh"]))
        self.assertFalse(trabajos_ci.afecta("ops-tests", ["motor/encargos/ciclo.py"]))
        self.assertEqual(
            trabajos_ci.trabajos_a_correr(["ops/senal-kuma.sh"]), ("ops-tests",)
        )

    def test_un_cambio_de_motor_corre_los_jobs_de_pruebas_del_motor(self):
        corren = trabajos_ci.trabajos_a_correr(["motor/encargos/ciclo.py"])
        self.assertEqual(corren, ("bateria", "macos", "windows"))

    def test_un_cambio_de_tests_conftest_o_verificacion_corre_lo_mismo(self):
        for ruta in ("tests/test_algo.py", "conftest.py", "verificacion/inspeccion/main.cfg"):
            with self.subTest(ruta=ruta):
                corren = trabajos_ci.trabajos_a_correr([ruta])
                self.assertIn("bateria", corren)
                self.assertIn("macos", corren)
                self.assertIn("windows", corren)
                self.assertNotIn("ops-tests", corren)

    def test_un_cambio_al_workflow_corre_todos(self):
        self.assertEqual(
            trabajos_ci.trabajos_a_correr([".github/workflows/pruebas.yml"]),
            trabajos_ci.TRABAJOS,
        )
        self.assertEqual(
            trabajos_ci.trabajos_a_correr([".github/workflows/quality.yml"]),
            trabajos_ci.TRABAJOS,
        )

    def test_windows_depende_del_script_que_ejecuta(self):
        self.assertTrue(trabajos_ci.afecta("windows", ["ops/prepara_pc.py"]))
        self.assertFalse(trabajos_ci.afecta("bateria", ["ops/prepara_pc.py"]))
        self.assertFalse(trabajos_ci.afecta("macos", ["ops/prepara_pc.py"]))

    def test_un_cambio_de_docs_no_corre_ningun_job_de_pruebas(self):
        self.assertEqual(trabajos_ci.trabajos_a_correr(["docs/flujo.md"]), ())

    def test_el_marcador_del_commit_temporal_manda_sobre_las_rutas(self):
        self.assertEqual(
            trabajos_ci._marcador_de_jobs("ci-prueba x jobs=ops-tests,bateria"),
            ("ops-tests", "bateria"),
        )
        self.assertEqual(
            trabajos_ci._marcador_de_jobs("ci-prueba x jobs=todos"),
            trabajos_ci.TRABAJOS,
        )
        self.assertIsNone(trabajos_ci._marcador_de_jobs("commit normal"))


class Workflow(unittest.TestCase):
    def test_hay_un_job_prep_que_decide(self):
        lineas = FLUJO.read_text(encoding="utf-8").splitlines()
        self.assertIn("  prep:", lineas)
        self.assertIn(
            "        run: python3 -m motor.encargos.trabajos_ci --github-output",
            lineas,
        )

    def test_cada_job_espera_prep_y_se_salta_solo_con_false(self):
        for job in trabajos_ci.TRABAJOS:
            with self.subTest(job=job):
                lineas = _job(job)
                self.assertIn("    needs: prep", lineas)
                condicion = next(x for x in lineas if x.strip().startswith("if:"))
                self.assertIn("always()", condicion)
                self.assertIn(f"needs.prep.outputs.{job.replace('-', '_')} != 'false'", condicion)

    def test_las_rutas_de_los_comandos_estan_declaradas(self):
        dependencias = {
            "ops-tests": ("ops/tests/correr.sh",),
            "bateria": ("tests",),
            "macos": ("tests",),
            "windows": ("tests", "ops/prepara_pc.py"),
        }
        for job, rutas in dependencias.items():
            texto = "\n".join(_job(job))
            for ruta in rutas:
                with self.subTest(job=job, ruta=ruta):
                    self.assertIn(ruta, texto)
                    self.assertTrue(
                        trabajos_ci.afecta(job, [ruta]),
                        f"{job} ejecuta {ruta} pero no la declara",
                    )


if __name__ == "__main__":
    unittest.main()
