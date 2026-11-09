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

    def test_las_partes_de_la_bateria_son_el_mismo_trabajo(self):
        for nombre in (
            "bateria-partes",
            "bateria-parte-0",
            "bateria-parte-1",
            "bateria-parte-2",
            "bateria-parte-3",
        ):
            with self.subTest(nombre=nombre):
                self.assertEqual(trabajos_ci.trabajo(nombre), "bateria")
                self.assertFalse(trabajos_ci.afecta(nombre, ["ops/senal-kuma.sh"]))
                self.assertTrue(
                    trabajos_ci.afecta(nombre, ["motor/encargos/trabajos_ci.py"])
                )

    def test_un_cambio_de_docs_no_corre_ningun_job_de_pruebas(self):
        self.assertEqual(trabajos_ci.trabajos_a_correr(["docs/flujo.md"]), ())

    def _seleccion(self, evento, diffs, mensaje="", correo="motor-ci@localhost"):
        def falso_git(*args):
            if args[:2] == ("log", "-1"):
                if any("%ae" in a for a in args):
                    return correo + "\n"
                return mensaje + "\n"
            if args[:2] == ("diff", "--name-only"):
                return "".join(
                    linea + "\n" for linea in diffs.get((args[2], args[3]), ())
                )
            raise AssertionError(f"git inesperado: {args}")

        with mock.patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request"}):
            with mock.patch.object(trabajos_ci, "_git", falso_git):
                valores, _modulos = trabajos_ci.seleccion(evento)
                return valores

    @staticmethod
    def _pr(base="base123", rama="encargo/enc_1"):
        return {
            "pull_request": {
                "base": {"sha": base, "repo": {"full_name": "o/r"}},
                "head": {
                    "sha": "head123",
                    "ref": rama,
                    "repo": {"full_name": "o/r"},
                },
            }
        }

    def test_el_mensaje_del_commit_no_puede_saltar_un_job(self):
        # Con motor en el diff, la bateria corre aunque el mensaje diga
        # `jobs=ops-tests`: la seleccion es el diff base..head.
        valores = self._seleccion(
            self._pr(),
            {("base123", "head123"): ("motor/x.py",)},
            mensaje="ci-prueba x jobs=ops-tests",
        )
        self.assertEqual(
            valores,
            {"ops-tests": False, "bateria": True, "macos": True, "windows": True},
        )

    def test_la_firma_forjada_no_decide_los_jobs(self):
        # Ramo `ci-prueba`, commit firmado como motor-ci y un ultimo commit
        # que solo toca ops/: antes bastaba para saltar la bateria. Ahora
        # manda el diff contra la base real (main), que trae motor/.
        valores = self._seleccion(
            self._pr("main-sha", rama="ci-prueba/enc_1-abc1234-1700000000"),
            {
                ("main-sha", "head123"): ("motor/x.py",),
                ("head123^", "head123"): ("ops/x.sh",),
            },
            mensaje="ci-prueba x jobs=ops-tests",
        )
        self.assertTrue(valores["bateria"])

    def test_jobs_extra_solo_anade(self):
        valores = self._seleccion(
            self._pr(),
            {("base123", "head123"): ("ops/senal-kuma.sh",)},
            mensaje="ci-prueba x jobs_extra=bateria",
        )
        self.assertEqual(
            valores,
            {"ops-tests": True, "bateria": True, "macos": False, "windows": False},
        )

    def test_un_job_exacto_sin_cambios_corre_solo_ese(self):
        valores = self._seleccion(
            self._pr(),
            {("base123", "head123"): ()},
            mensaje="ci-prueba x jobs_extra=bateria",
        )
        self.assertEqual(
            valores,
            {"ops-tests": False, "bateria": True, "macos": False, "windows": False},
        )

    def test_la_rama_base_del_commit_temporal_acota_su_diff(self):
        # El PR temporal abre contra ci-base/..., que apunta al verde: su diff
        # es solo la correccion.
        valores = self._seleccion(
            self._pr("ci-base-sha"),
            {("ci-base-sha", "head123"): ("ops/senal-kuma.sh",)},
        )
        self.assertEqual(
            valores,
            {"ops-tests": True, "bateria": False, "macos": False, "windows": False},
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
                cabecera = []
                for linea in lineas:
                    if linea.startswith("    steps:"):
                        break
                    cabecera.append(linea)
                texto = "\n".join(cabecera)
                needs = next(x for x in lineas if x.strip().startswith("needs:"))
                # El needs de la cabecera debe nombrar a prep: no basta que
                # la palabra aparezca en cualquier parte del job.
                self.assertRegex(needs, r"\bprep\b")
                self.assertIn("needs:", texto)
                condicion = next(x for x in lineas if x.strip().startswith("if:"))
                self.assertIn("always()", condicion)
                self.assertIn(f"needs.prep.outputs.{job.replace('-', '_')} != 'false'", condicion)

    def test_la_bateria_espera_a_las_cuatro_partes(self):
        partes = "\n".join(_job("bateria-partes"))
        self.assertIn("needs: prep", partes)
        self.assertIn("always()", partes)
        self.assertIn("needs.prep.outputs.bateria != 'false'", partes)
        self.assertIn("parte: [0, 1, 2, 3]", partes)
        self.assertIn("fail-fast: false", partes)
        final = "\n".join(_job("bateria"))
        self.assertIn("bateria-partes", final)
        self.assertIn("needs.prep.outputs.bateria != 'false'", final)
        self.assertIn("tests.reparto exigir", final)

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


class ModulosWindows(unittest.TestCase):
    def test_dos_origenes_distintos_eligen_modulos_distintos(self):
        self.assertTrue(
            hasattr(trabajos_ci, "modulos_windows"),
            "el job de Windows no elige módulos por las rutas tocadas",
        )
        preparacion = trabajos_ci.modulos_windows(["ops/prepara_pc.py"])
        encargos = trabajos_ci.modulos_windows(["motor/encargos/ciclo.py"])
        self.assertNotEqual(preparacion, encargos)
        self.assertIn("tests.test_prepara_pc", preparacion)
        self.assertIn("tests.test_encargos_despliegue_real", encargos)

    def test_un_cambio_al_bucle_corre_el_reinicio_ordenado_en_windows(self):
        modulos = trabajos_ci.modulos_windows(["motor/encargos/cli.py"])
        self.assertIn(
            "tests.test_reinicio_ordenado",
            modulos,
            "el reinicio del supervisor no se prueba en el job windows",
        )

    def test_una_ruta_desconocida_corre_la_bateria_completa(self):
        self.assertTrue(hasattr(trabajos_ci, "MODULOS_WINDOWS_COMPLETO"))
        self.assertEqual(
            trabajos_ci.modulos_windows(["docs/desconocido.md"]),
            trabajos_ci.MODULOS_WINDOWS_COMPLETO,
        )
        self.assertEqual(
            trabajos_ci.modulos_windows([".github/workflows/pruebas.yml"]),
            trabajos_ci.MODULOS_WINDOWS_COMPLETO,
        )

    def test_una_ruta_desconocida_junto_a_una_conocida_corre_todo(self):
        # motor/io.py no está en ningún grupo pero activa windows: acotar por
        # la ruta conocida dejaría fuera las pruebas que ese cambio importa.
        self.assertTrue(
            hasattr(trabajos_ci, "modulos_windows"),
            "el job de Windows no elige módulos por las rutas tocadas",
        )
        self.assertEqual(
            trabajos_ci.modulos_windows(["ops/prepara_pc.py", "motor/io.py"]),
            trabajos_ci.MODULOS_WINDOWS_COMPLETO,
        )

    def test_una_prueba_de_la_familia_test_windows_cae_en_su_grupo(self):
        # El prefijo `tests/test_windows` no es un directorio: sin el comodín
        # `tests/test_windows_wsl.py` nunca coincidía con su grupo.
        self.assertTrue(
            hasattr(trabajos_ci, "modulos_windows"),
            "el job de Windows no elige módulos por las rutas tocadas",
        )
        modulos = trabajos_ci.modulos_windows(["tests/test_windows_wsl.py"])
        self.assertIn("tests.test_windows_wsl", modulos)
        modulos = trabajos_ci.modulos_windows(
            ["motor/encargos/ciclo.py", "tests/test_windows_wsl.py"]
        )
        self.assertIn("tests.test_windows_wsl", modulos)

    def test_con_git_roto_main_corre_todos_y_publica_la_bateria_completa(self):
        import contextlib
        import io

        salida = io.StringIO()

        def roto(*_args):
            raise RuntimeError("bad object")

        try:
            with contextlib.redirect_stdout(salida), mock.patch.dict(
                os.environ,
                {
                    "GITHUB_EVENT_NAME": "pull_request",
                    "GITHUB_OUTPUT": "",
                    "GITHUB_EVENT_PATH": "",
                },
            ), mock.patch.object(trabajos_ci, "_git", roto):
                rc = trabajos_ci.main(["--github-output"])
        except RuntimeError as exc:
            self.fail(f"con git roto el prep abortó en vez de correr todo: {exc}")
        self.assertEqual(rc, 0)
        texto = salida.getvalue()
        for job in trabajos_ci.TRABAJOS:
            self.assertIn(f"{job.replace('-', '_')}=true", texto)
        self.assertIn(
            "modulos_windows=" + " ".join(trabajos_ci.MODULOS_WINDOWS_COMPLETO),
            texto,
        )

    def test_la_salida_de_github_lleva_los_modulos(self):
        self.assertTrue(hasattr(trabajos_ci, "modulos_windows"))
        import contextlib
        import io

        salida = io.StringIO()
        with contextlib.redirect_stdout(salida), mock.patch.dict(
            os.environ, {"GITHUB_OUTPUT": ""}
        ):
            trabajos_ci.main(["--rutas", "ops/prepara_pc.py", "--github-output"])
        texto = salida.getvalue()
        self.assertIn("modulos_windows=", texto)
        self.assertIn("tests.test_prepara_pc", texto)


if __name__ == "__main__":
    unittest.main()
