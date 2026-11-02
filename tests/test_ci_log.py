"""`ci.log` lee el log de un job de una corrida del PR del encargo.

El `gh` es un guion falso: devuelve el PR del encargo, sus runs y el log. La
operación no crea ramas ni PRs y rechaza cualquier PR, rama o repo ajenos.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from motor.encargos import capacidades as cap
from motor.encargos import necesidades as nec
from motor.encargos.dominio import (
    AlcanceRevision,
    Encargo,
    EntregaAutorizada,
    Fase,
    Solicitud,
)

SHA = "835b2f1a0c3d4e5f60718293a4b5c6d7e8f90123"
LOG = (
    "jaula-macos\tPrueba\t2026-11-01T12:00:00Z \x1b[32mok\x1b[0m test_jaula_macos\r\n"
    "jaula-macos\tPrueba\t2026-11-01T12:00:01Z Ran 12 tests in 3.1s\n"
    "jaula-macos\tPrueba\t2026-11-01T12:00:01Z OK (señal, ñandú)\n"
)

GH_FALSO = r'''#!{python}
import json, os, sys
d = os.environ["FAKE_GH_DIR"]
args = sys.argv[1:]
ruta = os.path.join(d, "estado.json")
estado = json.load(open(ruta))
estado["llamadas"].append(args)
def val(flag):
    return args[args.index(flag) + 1]
def guardar():
    json.dump(estado, open(ruta, "w"))
if args[:2] == ["pr", "view"]:
    guardar()
    print(json.dumps(estado["pr"]))
elif args[:2] == ["run", "list"]:
    guardar()
    sha = val("--commit")
    print(json.dumps([r for r in estado["runs"] if r["headSha"] == sha]))
elif args[:2] == ["run", "view"] and "--log" not in args:
    guardar()
    print(json.dumps(estado["vista"]))
elif args[:2] == ["run", "view"]:
    guardar()
    sys.stdout.write(open(os.path.join(d, "log-" + val("--job") + ".txt"), newline="").read())
else:
    guardar()
    sys.exit(2)
'''


def _encargo_con_repo(repo: str) -> Encargo:
    fase = Fase(
        id="f", resultado="log", estado="entrega",
        pr_numero=189, pr_sha=SHA, rama="encargo/enc_x-f",
    )
    return Encargo(
        id="enc_x",
        clave="clave-x",
        huella="huella",
        director="cli",
        texto="pedido",
        adjuntos=(),
        restricciones=(),
        canal="canal",
        entrega=EntregaAutorizada(
            nivel="pr", repo=repo, rama="encargo/enc_x", base="main"
        ),
        fases=(fase,),
        fase_activa="f",
        estado="ejecutando",
        generacion=1,
    )


class _CiLog(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.raiz = Path(tmp.name)
        self.fake = self.raiz / "fake"
        self.fake.mkdir()
        (self.fake / "log-99.txt").write_bytes(LOG.encode("utf-8"))
        (self.fake / "log-100.txt").write_text("otro job\n")
        self.estado = {
            "llamadas": [],
            "pr": {
                "number": 189,
                "state": "OPEN",
                "headRefName": "encargo/enc_x-f",
                "headRefOid": SHA,
                "headRepository": {"nameWithOwner": "o/r"},
            },
            "runs": [
                {
                    "databaseId": 1234,
                    "status": "completed",
                    "conclusion": "success",
                    "headSha": SHA,
                    "headBranch": "encargo/enc_x-f",
                    "url": "https://github.com/o/r/actions/runs/1234",
                    "workflowName": "Pruebas",
                }
            ],
            "vista": {
                "status": "completed",
                "conclusion": "success",
                "url": "https://github.com/o/r/actions/runs/1234",
                "jobs": [
                    {"name": "jaula-macos", "databaseId": 99, "conclusion": "success"},
                    {"name": "otro", "databaseId": 100, "conclusion": "success"},
                ],
            },
        }
        (self.fake / "estado.json").write_text(json.dumps(self.estado))
        bin_ = self.raiz / "bin"
        bin_.mkdir()
        gh = bin_ / "gh"
        gh.write_text(
            GH_FALSO.replace("#!{python}", "#!" + sys.executable)
        )
        gh.chmod(0o755)
        self.entorno = mock.patch.dict(
            os.environ,
            {
                "PATH": str(bin_) + os.pathsep + os.environ.get("PATH", ""),
                "FAKE_GH_DIR": str(self.fake),
            },
        )
        self.entorno.start()
        self.addCleanup(self.entorno.stop)
        self.espacio = self.raiz / "espacio"
        self.espacio.mkdir()
        self.destino = self.raiz / "destino"
        self.recursos = {
            "ci-linux": {
                "tipo": "ci",
                "autorizacion": "",
                "repo": "o/r",
                "workflow": "pruebas.yml",
                "base": "main",
            }
        }
        self.contexto = {
            "pr": 189,
            "sha": SHA,
            "rama": "encargo/enc_x-f",
            "repo": "o/r",
        }

    def pedir(self, contexto=None, **parametros) -> dict:
        datos = {"pr": "189", "job": "jaula-macos", "sha": SHA[:7]}
        datos.update({k: str(v) for k, v in parametros.items()})
        return cap.ejecutar(
            self.recursos, cap.CI_LOG, "ci-linux", datos, self.destino,
            espacio=self.espacio, contexto=self.contexto if contexto is None else contexto,
        )

    def estado_gh(self) -> dict:
        return json.loads((self.fake / "estado.json").read_text())

    def cambiar_pr(self, **cambios) -> None:
        self.estado["pr"].update(cambios)
        (self.fake / "estado.json").write_text(json.dumps(self.estado))

    def test_devuelve_el_log_literal_del_job_y_sha_pedidos(self):
        r = self.pedir()

        self.assertEqual(r["estado"], "ok", r)
        self.assertEqual(r["datos"]["pr"], 189)
        self.assertEqual(r["datos"]["job"], "jaula-macos")
        self.assertEqual(r["datos"]["sha"], SHA)
        self.assertEqual(r["datos"]["run"], "1234")
        self.assertEqual(r["datos"]["conclusion"], "success")
        self.assertEqual(Path(r["datos"]["log"]).read_bytes(), LOG.encode("utf-8"))
        self.assertEqual(r["datos"]["log_nombre"], f"jaula-macos-{SHA[:7]}-run1234.log")
        self.assertEqual(r["evidencia"]["tipo"], "ci_log")
        self.assertEqual(r["evidencia"]["pr"], 189)
        self.assertEqual(r["evidencia"]["sha"], SHA)
        llamadas = [a[:2] for a in self.estado_gh()["llamadas"]]
        self.assertNotIn(["pr", "create"], llamadas)

    def test_el_contexto_del_encargo_con_repo_en_url_no_se_rechaza(self):
        for repo in ("git@github.com:o/r.git", "https://github.com/o/r.git"):
            with self.subTest(repo=repo):
                contexto = nec.contexto_de(_encargo_con_repo(repo))
                self.assertEqual(contexto["repo"], repo)

                r = self.pedir(contexto=contexto)

                self.assertEqual(r["estado"], "ok", r)
                self.assertEqual(
                    Path(r["datos"]["log"]).read_bytes(), LOG.encode("utf-8")
                )

    def test_si_la_corrida_no_es_del_sha_pedido_dice_cual_hay(self):
        r = self.pedir(sha="a" * 40)

        self.assertEqual(r["estado"], "error", r)
        self.assertIn("no hay corrida del SHA", r["motivo"])
        self.assertIn(SHA[:12], r["motivo"])
        self.assertFalse((self.destino / "remoto").exists())

    def test_no_lee_un_pr_ajeno_al_encargo(self):
        r = self.pedir(pr="190")

        self.assertEqual(r["estado"], "error", r)
        self.assertIn("no es el del encargo", r["motivo"])
        self.assertEqual(self.estado_gh()["llamadas"], [])

    def test_no_lee_una_rama_ajena(self):
        self.cambiar_pr(headRefName="otra-rama")

        r = self.pedir()

        self.assertEqual(r["estado"], "error", r)
        self.assertIn("no de encargo/enc_x-f", r["motivo"])

    def test_no_lee_un_repo_ajeno(self):
        self.cambiar_pr(headRepository={"nameWithOwner": "otro/r"})

        r = self.pedir()

        self.assertEqual(r["estado"], "error", r)
        self.assertIn("no cruza repos", r["motivo"])

    def test_sin_pr_del_encargo_no_lee_nada(self):
        r = self.pedir(contexto={**self.contexto, "pr": 0})

        self.assertEqual(r["estado"], "error", r)
        self.assertIn("todavía no tiene un PR", r["motivo"])
        self.assertEqual(self.estado_gh()["llamadas"], [])

    def test_un_job_que_no_existe_lista_los_que_hay(self):
        r = self.pedir(job="no-existe")

        self.assertEqual(r["estado"], "error", r)
        self.assertIn("jaula-macos", r["motivo"])
        self.assertIn("otro", r["motivo"])

    def test_una_peticion_ci_log_es_una_solicitud_valida(self):
        sol = nec._solicitud_de(
            SimpleNamespace(id="enc_x"),
            {
                "id": "log-1", "encargo": "enc_x", "operacion": "ci.log",
                "recurso": "ci-linux", "fase": "f", "intento": "i",
                "generacion": 1, "conductor": "",
                "parametros": {"pr": "189", "job": "jaula-macos", "sha": SHA},
            },
        )

        self.assertEqual(sol.operacion, cap.CI_LOG)
        self.assertEqual(dict(sol.parametros)["pr"], "189")

    def test_una_inspeccion_ci_log_del_revisor_es_valida(self):
        from motor.encargos import comprobacion

        inspeccion = comprobacion._inspeccion_de(
            {
                "operacion": "ci.log",
                "recurso": "ci-linux",
                "parametros": {"pr": "189", "job": "jaula-macos", "sha": SHA},
            }
        )

        self.assertEqual(inspeccion.operacion, cap.CI_LOG)
        self.assertEqual(dict(inspeccion.parametros)["pr"], "189")

    def test_el_prompt_del_revisor_ofrece_ci_log_como_inspeccion(self):
        from motor.encargos import comprobacion

        texto = comprobacion.instrucciones_revision(
            AlcanceRevision(clase="completa", version="v", base_version="b"),
            Path("resultado.json"),
        )

        self.assertIn("ci.correr|ci.log", texto)

    def test_el_prompt_del_revisor_explica_los_parametros_de_ci_log(self):
        from motor.encargos import comprobacion

        texto = comprobacion.instrucciones_revision(
            AlcanceRevision(clase="completa", version="v", base_version="b"),
            Path("resultado.json"),
        )

        self.assertIn("Si la inspeccion es ci.log", texto)
        self.assertIn("pr (el numero del PR del encargo)", texto)
        self.assertIn("job (el nombre exacto del job)", texto)
        self.assertIn("sha (el SHA esperado)", texto)

    def test_el_catalogo_la_ofrece_y_no_es_observable(self):
        self.assertEqual(
            cap.operaciones_de(self.recursos, "ci-linux"), (cap.CI, cap.CI_LOG)
        )
        self.assertEqual(cap.recursos_observables(self.recursos), ())
        self.assertIn("ci.log --recurso <r>", nec.bloque_capacidades(self.raiz, self.recursos))

    def test_el_supervisor_publica_el_log_dentro_del_espacio(self):
        r = self.pedir()
        trabajador = self.raiz / "trabajador"
        trabajador.mkdir()
        directorio = self.raiz / "ejecucion"
        sol = Solicitud(
            id="ci-log-1", operacion=cap.CI_LOG, recurso="ci-linux", fase="f",
            intento="i", generacion=1, conductor="",
        )

        publicado = nec._publicar_snapshot(trabajador, sol, r, directorio)

        copia = Path(publicado["datos"]["log"])
        self.assertTrue(copia.is_relative_to(trabajador))
        self.assertIn(".interno", copia.parts)
        self.assertIn("entregas", copia.parts)
        self.assertEqual(copia.read_bytes(), LOG.encode("utf-8"))
        self.assertEqual(publicado["datos"]["log_host"], r["datos"]["log_host"])
