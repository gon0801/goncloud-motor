"""Qué jobs toca un cambio y cómo el workflow los salta sin romper la protección.

El filtro va en cada job, no en `paths` del disparador: un job saltado por su
`if` cuenta como éxito para la protección de rama; un workflow saltado deja sus
checks pendientes y bloquea el merge.
"""

from __future__ import annotations

import os
import re
import unittest
from unittest import mock
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
        self.assertFalse(trabajos_ci.afecta("macos", ["ops/prepara_pc.py"]))

    def test_bateria_depende_de_lo_que_leen_sus_pruebas(self):
        for ruta in (
            "conf-ejemplo/rutas.toml",
            "proto/fake_cli.py",
            "skill/otra/SKILL.md",
            "docs/flujo-revisiones.md",
            "ops/prepara_pc.py",
            "ops/iniciar_encargos_pc.py",
            "ops/pstack/recorta_registro.py",
            "ops/lib.sh",
            "ops/tests/omitidas.txt",
        ):
            with self.subTest(ruta=ruta):
                self.assertTrue(trabajos_ci.afecta("bateria", [ruta]))

    def test_macos_y_windows_dependen_del_vigilante_que_leen(self):
        for job in ("macos", "windows"):
            with self.subTest(job=job):
                self.assertTrue(
                    trabajos_ci.afecta(job, ["ops/iniciar_encargos_pc.py"])
                )

    def test_un_cambio_de_ops_que_la_bateria_no_lee_no_la_corre(self):
        self.assertFalse(trabajos_ci.afecta("bateria", ["ops/senal-kuma.sh"]))
        self.assertEqual(
            trabajos_ci.trabajos_a_correr(["ops/senal-kuma.sh"]), ("ops-tests",)
        )

    def test_un_cambio_de_docs_no_corre_ningun_job_de_pruebas(self):
        self.assertEqual(trabajos_ci.trabajos_a_correr(["docs/flujo.md"]), ())

    def _seleccion(
        self, evento, diffs, correo="motor-ci@localhost", mensaje=""
    ):
        def falso_git(*args):
            if args[:2] == ("log", "-1"):
                if any("%B" in a for a in args):
                    return mensaje + "\n"
                return correo + "\n"
            if args[:2] == ("diff", "--name-only"):
                return "".join(
                    linea + "\n" for linea in diffs.get((args[2], args[3]), ())
                )
            raise AssertionError(f"git inesperado: {args}")

        with mock.patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request"}):
            with mock.patch.object(trabajos_ci, "_git", falso_git):
                return trabajos_ci.seleccion(evento)

    @staticmethod
    def _pr(rama, misma=True):
        repo = {"full_name": "o/r"}
        cabeza = {"sha": "head123", "ref": rama, "repo": repo if misma else {"full_name": "x/r"}}
        return {
            "pull_request": {
                "base": {"sha": "base123", "repo": repo},
                "head": cabeza,
            }
        }

    def test_el_mensaje_del_commit_no_puede_saltar_un_job(self):
        # El marcador viejo `jobs=` ya no se lee: con motor en el diff, la
        # bateria corre aunque el mensaje diga jobs=ops-tests.
        valores = self._seleccion(
            self._pr("encargo/enc_1"),
            {("base123", "head123"): ("motor/x.py",)},
            mensaje="ci-prueba x jobs=ops-tests",
        )
        self.assertEqual(
            valores,
            {"ops-tests": False, "bateria": True, "macos": True, "windows": True},
        )
        # Una rama ci-prueba de otro autor tampoco usa el diff de su commit.
        valores = self._seleccion(
            self._pr("ci-prueba/x"),
            {
                ("base123", "head123"): ("motor/x.py",),
                ("head123^", "head123"): ("ops/x.sh",),
            },
            correo="persona@example.com",
            mensaje="ci-prueba x jobs=ops-tests",
        )
        self.assertTrue(valores["bateria"])

    def test_jobs_extra_solo_anade(self):
        valores = self._seleccion(
            self._pr("encargo/enc_1"),
            {("base123", "head123"): ("ops/senal-kuma.sh",)},
            mensaje="ci-prueba x jobs_extra=bateria",
        )
        self.assertEqual(
            valores,
            {"ops-tests": True, "bateria": True, "macos": False, "windows": False},
        )
        valores = self._seleccion(
            self._pr("ci-prueba/enc_1-abc1234-1700000000"),
            {("head123^", "head123"): ("ops/senal-kuma.sh",)},
            mensaje="ci-prueba x jobs_extra=bateria",
        )
        self.assertEqual(
            valores,
            {"ops-tests": True, "bateria": True, "macos": False, "windows": False},
        )

    def test_el_commit_temporal_solo_corre_lo_que_cambia_su_diff(self):
        valores = self._seleccion(
            self._pr("ci-prueba/enc_1-abc1234-1700000000"),
            {("head123^", "head123"): ("ops/senal-kuma.sh",)},
        )
        self.assertEqual(
            valores,
            {"ops-tests": True, "bateria": False, "macos": False, "windows": False},
        )
        valores = self._seleccion(
            self._pr("ci-prueba/enc_1-abc1234-1700000000"),
            {("head123^", "head123"): ()},
        )
        self.assertEqual(
            valores,
            {"ops-tests": True, "bateria": True, "macos": True, "windows": True},
        )


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
