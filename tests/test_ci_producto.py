"""El CI del producto corre al entregar, se aplica a la fase y se reusa por árbol.

Las pruebas no llaman a GitHub: prueban la petición que deja el motor, la
reducción del resultado y la cobertura del verde previo.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

from motor.encargos import capacidades, ciclo, necesidades, trabajos_ci
from motor.encargos.comprobacion import version_producto
from motor.encargos.dominio import (
    alcance_de,
    prompt_responsable,
    prompt_revisor,
    reducir,
)
from tests.test_flujo_revisiones import (
    Recorrido,
    _entrega_pr,
    ahora_iso,
    sonda_sin_aislamiento_del_host,
)
from tests.test_entrega_checks_pr import _GhConCorridas


def setUpModule():
    sonda_sin_aislamiento_del_host()


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _ci_resuelto(r: Recorrido, conclusion: str = "failure"):
    """Deja una solicitud ci.correr del motor ya resuelta sobre la versión vigente."""
    e = r.fold()
    version = e.fase_actual.producto.firma
    ident = "ci-producto-prueba"
    marca = r.base / "ci-producto"
    marca.mkdir(parents=True, exist_ok=True)
    (marca / f"{ident}.json").write_text(
        json.dumps({"version": version, "fase": e.fase_activa, "hora": r.hora}),
        encoding="utf-8",
    )
    r.reg.anotar(
        r.enc,
        {
            "kind": "solicitud",
            "data": {
                "id": ident,
                "operacion": capacidades.CI,
                "recurso": "ci-linux",
                "fase": e.fase_activa,
                "intento": e.intento,
                "generacion": e.generacion,
                "conductor": "motor-ci",
                "parametros": [["job", "todos"], ["base_arbol", ""]],
                "estado": "pendiente",
            },
        },
        f"test-solicitud-{ident}",
    )
    r.reg.anotar(
        r.enc,
        {
            "kind": "herramienta",
            "data": {
                "id": ident,
                "carpeta": str(r.d),
                "version": version,
                "hora": r.hora,
            },
        },
        f"test-herramienta-{ident}",
    )
    e = r.fold()
    evidencia = {
        "tipo": "ci",
        "sha": "sha-temporal",
        "arbol": "arbol-v1",
        "run": "77",
        "url": "https://example/run/77",
        "job": "todos",
        "conclusion": "failure",
        "jobs": [
            {
                "nombre": "bateria",
                "conclusion": conclusion,
                "reusado": False,
                "log": ".interno/logs/bateria-run77.log",
                "url": "https://example/job/77",
                "cola": "FALLA literal de bateria",
            }
        ],
        "fallas": ["bateria"],
        "limpieza": {"rama_borrada": True, "pr_cerrado": True},
    }
    r.reg.anotar(
        r.enc,
        {
            "kind": "resolucion",
            "data": {
                "id": ident,
                "fase": e.fase_activa,
                "intento": e.intento,
                "estado": "resuelta",
                "evidencia": json.dumps(evidencia),
            },
        },
        f"test-resolucion-{ident}",
    )
    return r.fold()


def _con_repo(tmp: str, clave: str) -> Recorrido:
    r = Recorrido(
        tmp,
        clave,
        "Crea saludo.txt con hola",
        entrega_nivel="pr",
        repo="gon0801/goncloud-motor",
        base_rama="main",
    )
    _git("init", "-b", "main", "--quiet", cwd=r.espacio)
    (r.espacio / "base.txt").write_text("base\n")
    _git("add", "-A", cwd=r.espacio)
    _git(
        "-c", "user.name=t", "-c", "user.email=t@l",
        "commit", "--quiet", "-m", "base", cwd=r.espacio,
    )
    (r.espacio / "saludo.txt").write_text("hola\n")
    r.reg.anotar(
        r.enc,
        {
            "kind": "intento_abierto",
            "data": {
                "id": "tok",
                "pid": os.getpid(),
                "inicio": "t",
                "rol": "responsable",
                "hora": r.hora,
            },
        },
        "t-intento-base",
    )
    (r.base / "capacidades.json").write_text(
        json.dumps(
            {
                "recursos": {
                    "ci-linux": {
                        "tipo": "ci",
                        "repo": "gon0801/goncloud-motor",
                        "base": "main",
                        "workflow": "pruebas.yml",
                    }
                }
            }
        )
    )
    return r


class ResultadoDelCi(unittest.TestCase):
    def test_la_peticion_del_motor_se_ejecuta_y_su_resultado_llega_a_la_fase(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _con_repo(tmp, "ci-resultado")
            self.addCleanup(r.reg.close)
            r.incorporar()
            with mock.patch(
                "motor.encargos.necesidades._ejecutar",
                return_value={"estado": "pendiente"},
            ):
                necesidades.atender(r.reg, r.fold(), r.espacio, r.d, r.hora)
            e = r.fold()
            sol = next(
                s for s in e.solicitudes if s.conductor == "motor-ci"
            )
            self.assertEqual(sol.estado, "pendiente")
            self.assertEqual(sol.version, e.fase_actual.producto.firma)
            carpeta = Path(sol.carpeta)
            carpeta.mkdir(parents=True, exist_ok=True)
            (carpeta / "resultado.json").write_text(
                json.dumps(
                    {
                        "estado": "ok",
                        "datos": {"job": "todos"},
                        "evidencia": {
                            "tipo": "ci",
                            "sha": "sha-temporal",
                            "arbol": "arbol-v1",
                            "run": "9",
                            "url": "https://example/run/9",
                            "job": "todos",
                            "conclusion": "failure",
                            "jobs": [
                                {
                                    "nombre": "bateria",
                                    "conclusion": "failure",
                                    "reusado": False,
                                    "log": ".interno/logs/bateria.log",
                                    "cola": "FALLA",
                                }
                            ],
                            "fallas": ["bateria"],
                            "limpieza": {"rama_borrada": True, "pr_cerrado": True},
                        },
                    }
                )
            )
            with mock.patch(
                "motor.encargos.necesidades._ejecutar",
                return_value={"estado": "pendiente"},
            ):
                necesidades.atender(r.reg, r.fold(), r.espacio, r.d, r.hora)
            r.reg.anotar(
                r.enc,
                {"kind": "intento_cerrado", "data": {"hora": r.hora}},
                "t-intento-cerrado",
            )
            e = r.fold()
            sol = next(s for s in e.solicitudes if s.conductor == "motor-ci")
            self.assertEqual(sol.estado, "resuelta")
            e = ciclo._aplicar_ci_producto(r.reg, e, r.hora)
            self.assertEqual(e.fase_actual.ci_fallas, ("bateria",))
            self.assertEqual(e.rol, "responsable")
            self.assertIn("bateria", prompt_responsable(e))

    def test_un_ci_rojo_llega_al_revisor_que_sigue_revisando(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _con_repo(tmp, "ci-rojo-revisor")
            self.addCleanup(r.reg.close)
            e = r.incorporar()
            self.assertEqual(e.rol, "revisor")
            # El intento del revisor sigue vivo cuando llega el rojo.
            self.assertIsNotNone(e.proc)
            e = _ci_resuelto(r, conclusion="failure")
            e = ciclo._aplicar_ci_producto(r.reg, e, r.hora)
            self.assertEqual(e.fase_actual.ci_fallas, ("bateria",))
            self.assertTrue(
                any(
                    ev["kind"] == "ci_revision_rojo"
                    and ev["data"].get("generacion") == e.generacion
                    for ev in r.eventos("ci_revision_rojo")
                ),
                "no se marcó que el rojo llegó durante la revisión",
            )
            # Su juicio se emitió sin la evidencia: no se acepta. La vuelta
            # siguiente del revisor se arma con el job y el log literal.
            r.juicio({"veredicto": "aprobado", "evidencia": "revise sin el CI"})
            e2 = ciclo._incorporar(r.reg, r.fold(), r.espacio, r.hora, r.d)
            self.assertEqual(e2.rol, "revisor")
            self.assertIsNone(e2.revision_vigente)
            texto = prompt_revisor(e2, alcance_de(e2))
            self.assertIn("CI que el motor corrió", texto)
            self.assertIn("bateria", texto)
            self.assertIn(".interno/logs/bateria-run77.log", texto)
            self.assertIn("FALLA literal de bateria", texto)


class PideElCi(unittest.TestCase):
    def test_al_marcar_producto_listo_se_pide_el_ci_sin_esperar_al_revisor(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _con_repo(tmp, "ci-pide")
            self.addCleanup(r.reg.close)
            e = r.incorporar()
            peticiones = sorted(
                (r.espacio / ".interno" / "buzon" / "peticiones").glob("*.json")
            )
            self.assertEqual(len(peticiones), 1, peticiones)
            crudo = json.loads(peticiones[0].read_text(encoding="utf-8"))
            self.assertEqual(crudo["conductor"], "motor-ci")
            self.assertEqual(crudo["operacion"], "ci.correr")
            self.assertEqual(crudo["parametros"]["job"], "todos")
            # La primera corrida no tiene verde previo: el workflow corre todo.
            self.assertEqual(crudo["parametros"]["base_arbol"], "")
            self.assertEqual(e.rol, "revisor")
            self.assertEqual(e.estrategia, "revisar_evidencia")

    def test_un_ci_verde_del_mismo_arbol_no_espera_otra_corrida(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _con_repo(tmp, "ci-verde")
            self.addCleanup(r.reg.close)
            e = r.incorporar()
            sha, arbol = capacidades._commit_temporal(r.espacio, r.d, "test")
            version = e.fase_actual.producto.firma
            e = reducir(
                e,
                {
                    "kind": "ci_producto",
                    "data": {
                        "fase": e.fase_activa,
                        "version": version,
                        "arbol": arbol,
                        "run": "12",
                        "url": "https://example/run/12",
                        "jobs": [
                            {"nombre": j, "conclusion": "success", "reusado": False}
                            for j in trabajos_ci.TRABAJOS
                        ],
                        "fallas": [],
                    },
                },
            )
            veredicto = SimpleNamespace(
                faltan=("bateria",),
                fallas=(),
                en_marcha=("bateria",),
                corridas=(),
            )
            self.assertTrue(ciclo._pre_ci_cubre(e, r.espacio, sha, veredicto))

    def test_un_ci_de_una_version_vieja_no_cuenta_para_la_nueva(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _con_repo(tmp, "ci-viejo")
            self.addCleanup(r.reg.close)
            e = r.incorporar()
            version1 = e.fase_actual.producto.firma
            e = reducir(
                e,
                {
                    "kind": "ci_producto",
                    "data": {
                        "fase": e.fase_activa,
                        "version": version1,
                        "arbol": "arbol-v1",
                        "jobs": [{"nombre": "bateria", "conclusion": "success", "reusado": False}],
                        "fallas": [],
                    },
                },
            )
            self.assertEqual(e.fase_actual.ci_base_arbol, "arbol-v1")

            (r.espacio / "saludo.txt").write_text("hola mundo\n")
            v2 = version_producto(r.espacio)
            e2 = reducir(
                e,
                {
                    "kind": "producto_listo",
                    "data": {
                        "version": v2.firma,
                        "manifiesto": [list(x) for x in v2.manifiesto],
                        "sha_git": "",
                    },
                },
            )
            self.assertEqual(e2.fase_actual.ci_version, "")
            self.assertEqual(e2.fase_actual.ci_fallas, ())
            self.assertEqual(e2.fase_actual.ci_base_arbol, "arbol-v1")

            e3 = reducir(
                e2,
                {
                    "kind": "ci_producto",
                    "data": {
                        "fase": e2.fase_activa,
                        "version": version1,
                        "arbol": "arbol-v1",
                        "jobs": [{"nombre": "bateria", "conclusion": "failure", "reusado": False}],
                        "fallas": ["bateria"],
                    },
                },
            )
            self.assertEqual(e3.fase_actual.ci_version, "")
            self.assertEqual(e3.fase_actual.ci_fallas, ())
            self.assertTrue(
                any("descartado" in nota for nota in e3.procedimiento), e3.procedimiento
            )

    def test_un_ci_rojo_llega_al_responsable_aunque_el_revisor_apruebe(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _con_repo(tmp, "ci-rojo")
            self.addCleanup(r.reg.close)
            e = r.incorporar()
            version = e.fase_actual.producto.firma
            r.reg.anotar(
                r.enc,
                {
                    "kind": "ci_producto",
                    "data": {
                        "fase": e.fase_activa,
                        "version": version,
                        "arbol": "arbol-v1",
                        "run": "77",
                        "jobs": [
                            {
                                "nombre": "bateria",
                                "conclusion": "failure",
                                "reusado": False,
                                "log": ".interno/logs/bateria-run77.log",
                                "cola": "FALLA literal de bateria",
                            }
                        ],
                        "fallas": ["bateria"],
                    },
                },
                "ci-prueba-rojo",
            )
            r.juicio({"veredicto": "aprobado", "evidencia": "lei saludo.txt"})
            e = r.incorporar()
            self.assertEqual(e.rol, "responsable")
            self.assertEqual(e.fase_actual.ci_fallas, ("bateria",))
            texto = prompt_responsable(e)
            self.assertIn("bateria", texto)
            self.assertIn("FALLA literal de bateria", texto)
            self.assertIn("log literal completo en .interno/logs/bateria-run77.log", texto)

    def test_el_revisor_ve_el_ci_rojo_y_los_cierres_del_operador(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _con_repo(tmp, "ci-revisor")
            self.addCleanup(r.reg.close)
            e = r.incorporar()
            version = e.fase_actual.producto.firma
            e = reducir(
                e,
                {
                    "kind": "ci_producto",
                    "data": {
                        "fase": e.fase_activa,
                        "version": version,
                        "arbol": "arbol-v1",
                        "jobs": [
                            {
                                "nombre": "bateria",
                                "conclusion": "failure",
                                "reusado": False,
                                "log": ".interno/logs/bateria.log",
                                "cola": "traza",
                            }
                        ],
                        "fallas": ["bateria"],
                    },
                },
            )
            texto = prompt_revisor(e, alcance_de(e))
            self.assertIn("CI que el motor corrió", texto)
            self.assertIn("bateria", texto)
            self.assertIn(".interno/logs/bateria.log", texto)


class _GhAprobado(_GhConCorridas):
    titulos = ()


class FastPathDelPr(unittest.TestCase):
    def test_un_ci_verde_del_mismo_arbol_no_espera_los_checks_del_pr(self):
        with tempfile.TemporaryDirectory() as tmp:
            _vuelta, r = _entrega_pr(
                self,
                tmp,
                "fast-path",
                _GhAprobado,
                {
                    "corridas": {
                        "ops-tests": "in_progress",
                        "bateria": "in_progress",
                    }
                },
            )
            (r.espacio / "saludo.txt").write_text("hola")
            r.incorporar()
            sha, arbol = capacidades._commit_temporal(r.espacio, r.d, "test")
            e = r.fold()
            version = e.fase_actual.producto.firma
            r.reg.anotar(
                r.enc,
                {
                    "kind": "ci_producto",
                    "data": {
                        "fase": e.fase_activa,
                        "version": version,
                        "arbol": arbol,
                        "run": "1",
                        "jobs": [
                            {"nombre": j, "conclusion": "success", "reusado": False}
                            for j in trabajos_ci.TRABAJOS
                        ],
                        "fallas": [],
                    },
                },
                "ci-fast-path",
            )
            r.juicio({"veredicto": "aprobado", "evidencia": "lei saludo.txt"})
            r.incorporar()
            e = ciclo._paso_entrega(r.reg, r.fold(), _vuelta.gh, ahora_iso())
            self.assertEqual(e.estado, "terminado", e.motivo)
            self.assertTrue(
                any(
                    ev["data"].get("paso") == "checks_reusados"
                    for ev in r.eventos("entrega_externa")
                ),
                "no se anotó el reuso del CI previo",
            )
            self.assertEqual(e.fase_actual.checks_esperando_sha, "")


class ReusoPorJob(unittest.TestCase):
    def _con_verde(self, tmp: str, clave: str):
        r = _con_repo(tmp, clave)
        e = r.incorporar()
        sha, arbol = capacidades._commit_temporal(r.espacio, r.d, "test")
        version = e.fase_actual.producto.firma
        e = reducir(
            e,
            {
                "kind": "ci_producto",
                "data": {
                    "fase": e.fase_activa,
                    "version": version,
                    "arbol": arbol,
                    "jobs": [
                        {"nombre": j, "conclusion": "success", "reusado": False}
                        for j in trabajos_ci.TRABAJOS
                    ],
                    "fallas": [],
                },
            },
        )
        return r, e, sha, arbol

    def test_un_cambio_solo_de_ops_reusa_los_jobs_del_motor(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, e, _sha, arbol = self._con_verde(tmp, "reuso-ops")
            self.addCleanup(r.reg.close)
            (r.espacio / "ops").mkdir(parents=True, exist_ok=True)
            (r.espacio / "ops" / "x.sh").write_text("echo x\n")
            sha2, arbol2 = capacidades._commit_temporal(r.espacio, r.d, "test2")
            fase = replace(
                e.fase_actual,
                producto=replace(e.fase_actual.producto, firma="v2"),
                ci_version="v2",
                ci_arbol=arbol,
                ci_base_arbol=arbol,
                ci_fallas=(),
            )
            e2 = replace(
                e,
                fases=tuple(fase if f.id == fase.id else f for f in e.fases),
            )
            veredicto = SimpleNamespace(
                faltan=("windows",),
                fallas=(),
                en_marcha=("windows",),
                corridas=(),
            )
            self.assertTrue(ciclo._pre_ci_cubre(e2, r.espacio, sha2, veredicto))

    def test_un_cambio_de_motor_obliga_a_esperar_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, e, _sha, arbol = self._con_verde(tmp, "reuso-motor")
            self.addCleanup(r.reg.close)
            (r.espacio / "motor").mkdir(parents=True, exist_ok=True)
            (r.espacio / "motor" / "nuevo.py").write_text("X = 1\n")
            sha2, _arbol2 = capacidades._commit_temporal(r.espacio, r.d, "test2")
            fase = replace(
                e.fase_actual,
                producto=replace(e.fase_actual.producto, firma="v2"),
                ci_version="v2",
                ci_arbol=arbol,
                ci_base_arbol=arbol,
                ci_fallas=(),
            )
            e2 = replace(
                e,
                fases=tuple(fase if f.id == fase.id else f for f in e.fases),
            )
            veredicto = SimpleNamespace(
                faltan=("windows",),
                fallas=(),
                en_marcha=("windows",),
                corridas=(),
            )
            self.assertFalse(ciclo._pre_ci_cubre(e2, r.espacio, sha2, veredicto))

    def test_un_job_cancelado_no_acredita_el_arbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, e, sha, arbol = self._con_verde(tmp, "reuso-cancelado")
            self.addCleanup(r.reg.close)
            fase = replace(
                e.fase_actual,
                ci_jobs=(
                    ("ops-tests", "success", False),
                    ("bateria", "cancelled", False),
                    ("macos", "success", False),
                    ("windows", "success", False),
                ),
                ci_arbol=arbol,
                ci_base_arbol=arbol,
                ci_fallas=(),
            )
            e2 = replace(
                e,
                fases=tuple(fase if f.id == fase.id else f for f in e.fases),
            )
            veredicto = SimpleNamespace(
                faltan=("bateria",),
                fallas=(),
                en_marcha=("bateria",),
                corridas=(),
            )
            self.assertFalse(ciclo._pre_ci_cubre(e2, r.espacio, sha, veredicto))


if __name__ == "__main__":
    unittest.main()
