"""`ci.correr job=todos` devuelve todos los jobs y deja el log de los rojos.

El commit temporal se cuelga del árbol verde anterior (o de un árbol vacío en
la primera corrida); el workflow decide por el diff y los jobs saltados quedan
reusados. Se usa el mismo `gh` falso que las pruebas de CI.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from motor.encargos import capacidades as cap
from tests.test_ci_remoto import _Ci

GH_MULTI = r'''#!{python}
import json, os, sys
d = os.environ["FAKE_GH_DIR"]
args = sys.argv[1:]
def val(flag):
    return args[args.index(flag) + 1]
def val_run(a):
    return a[2] if len(a) > 2 else ""
if args[:2] == ["pr", "create"]:
    print("https://github.com/o/r/pull/7")
elif args[:2] == ["pr", "view"]:
    print(json.dumps({{"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"}}))
elif args[:2] == ["run", "list"]:
    flujo = val("--workflow") if "--workflow" in args else ""
    if flujo == "quality.yml":
        print(json.dumps([{{"databaseId": 4321, "status": "completed",
            "conclusion": "success", "headSha": val("--commit"),
            "event": "pull_request",
            "url": "https://github.com/o/r/actions/runs/4321",
            "workflowName": "Quality"}}]))
    else:
        print(json.dumps([{{"databaseId": 1234, "status": "completed",
            "conclusion": "failure", "headSha": val("--commit"),
            "event": "pull_request",
            "url": "https://github.com/o/r/actions/runs/1234",
            "workflowName": "Pruebas"}}]))
elif args[:2] == ["run", "view"] and "--log" not in args and val_run(args) == "4321":
    print(json.dumps({{"status": "completed", "conclusion": "success",
        "url": "https://github.com/o/r/actions/runs/4321",
        "jobs": [{{"name": "gate", "databaseId": 200, "conclusion": "success"}}]}}))
elif args[:2] == ["run", "view"] and "--log" not in args:
    print(json.dumps({{"status": "completed", "conclusion": "failure",
        "url": "https://github.com/o/r/actions/runs/1234",
        "jobs": [
            {{"name": "ops-tests", "databaseId": 99, "conclusion": "success"}},
            {{"name": "bateria", "databaseId": 100, "conclusion": "failure"}},
            {{"name": "macos", "databaseId": 101, "conclusion": "skipped"}},
            {{"name": "windows", "databaseId": 102, "conclusion": "skipped"}},
        ]}}))
elif args[:2] == ["run", "view"]:
    sys.stdout.write(open(os.path.join(d, "log-" + val("--job") + ".txt")).read())
elif args[:2] == ["pr", "close"]:
    pass
else:
    sys.exit(2)
'''


class CorreTodosLosJobs(_Ci):
    def ramas_base(self) -> list[str]:
        salida = subprocess.run(
            ["git", "--git-dir", str(self.bare), "for-each-ref",
             "--format=%(refname)", "refs/heads/ci-base"],
            capture_output=True, text=True,
        ).stdout
        return salida.split()

    def setUp(self):
        super().setUp()
        (self.espacio / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
        (self.espacio / ".github" / "workflows" / "quality.yml").write_text(
            "name: Quality\n", encoding="utf-8"
        )
        (self.fake / "log-100.txt").write_text("FALLA de bateria\n", encoding="utf-8")
        (self.fake / "log-101.txt").write_text("", encoding="utf-8")
        (self.bin / "gh").write_text(
            GH_MULTI.replace("{{", "{").replace("}}", "}").replace(
                "#!{python}", "#!" + sys.executable
            )
        )
        (self.bin / "gh").chmod(0o755)

    def test_devuelve_todos_los_jobs_y_el_log_del_rojo(self):
        r = cap.ejecutar(
            self.recursos,
            cap.CI,
            "ci-linux",
            {"job": "todos"},
            self.destino,
            espacio=self.espacio,
        )
        self.assertEqual(r["estado"], "ok", r)
        datos = r["datos"]
        self.assertEqual(datos["fallas"], ["bateria"])
        jobs = {j["nombre"]: j for j in datos["jobs"]}
        self.assertEqual(jobs["ops-tests"]["conclusion"], "success")
        self.assertFalse(jobs["ops-tests"]["reusado"])
        self.assertEqual(jobs["bateria"]["conclusion"], "failure")
        self.assertIn("FALLA de bateria", Path(jobs["bateria"]["log"]).read_text())
        self.assertTrue(jobs["macos"]["reusado"])
        self.assertEqual(jobs["gate"]["conclusion"], "success")
        self.assertTrue(datos["arbol"])

        padre = subprocess.run(
            ["git", "--git-dir", str(self.bare), "rev-parse", datos["sha"] + "^"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.assertEqual(
            subprocess.run(
                ["git", "--git-dir", str(self.bare), "ls-tree", padre],
                capture_output=True, text=True, check=True,
            ).stdout.strip(),
            "",
            "la primera corrida se cuelga del árbol vacío y corre todos los jobs",
        )
        mensaje = subprocess.run(
            ["git", "--git-dir", str(self.bare), "log", "-1", "--format=%B", datos["sha"]],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertNotIn("jobs=", mensaje)

        evidencia = r["evidencia"]
        self.assertEqual(evidencia["fallas"], ["bateria"])
        self.assertEqual(
            {j["nombre"]: j["reusado"] for j in evidencia["jobs"]}["macos"], True
        )
        self.assertTrue(datos["base_rama"].startswith("ci-base/"))
        self.assertEqual(datos["base"], "main")
        self.assertEqual(self.ramas_remotas(), [])
        self.assertEqual(self.ramas_base(), [])

    def test_un_job_cancelado_es_rojo_y_no_cuenta_como_verde(self):
        guion = (self.bin / "gh").read_text(encoding="utf-8")
        (self.bin / "gh").write_text(
            guion.replace('"conclusion": "failure"', '"conclusion": "cancelled"'),
            encoding="utf-8",
        )
        (self.bin / "gh").chmod(0o755)
        r = cap.ejecutar(
            self.recursos,
            cap.CI,
            "ci-linux",
            {"job": "todos"},
            self.destino,
            espacio=self.espacio,
        )
        self.assertEqual(r["estado"], "ok", r)
        datos = r["datos"]
        self.assertEqual(datos["conclusion"], "failure")
        self.assertEqual(datos["fallas"], ["bateria"])
        jobs = {j["nombre"]: j for j in datos["jobs"]}
        self.assertEqual(jobs["bateria"]["conclusion"], "cancelled")
        self.assertIn("FALLA de bateria", Path(jobs["bateria"]["log"]).read_text())


    def test_una_correccion_se_cuelga_del_arbol_verde_y_su_diff(self):
        self.destino.mkdir(parents=True, exist_ok=True)
        verde = cap.arbol_producto(self.espacio, self.destino)
        (self.espacio / "base.txt").write_text("corregido\n")
        r = cap.ejecutar(
            self.recursos,
            cap.CI,
            "ci-linux",
            {"job": "todos", "base_arbol": verde},
            self.destino,
            espacio=self.espacio,
        )
        self.assertEqual(r["estado"], "ok", r)
        sha = r["datos"]["sha"]
        padre = subprocess.run(
            ["git", "--git-dir", str(self.bare), "rev-parse", sha + "^"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        arbol_padre = subprocess.run(
            ["git", "--git-dir", str(self.bare), "rev-parse", padre + "^{tree}"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.assertEqual(arbol_padre, verde)
        self.assertRegex(
            r["datos"]["rama"], r"^ci-prueba/.+-[0-9a-f]{7,}-[0-9]+$"
        )
        self.assertEqual(
            subprocess.run(
                ["git", "--git-dir", str(self.bare), "log", "-1",
                 "--format=%ae", sha],
                capture_output=True, text=True, check=True,
            ).stdout.strip(),
            "motor-ci@localhost",
        )
        cambios = subprocess.run(
            ["git", "--git-dir", str(self.bare), "diff", "--name-only", padre, sha],
            capture_output=True, text=True, check=True,
        ).stdout.split()
        self.assertIn("base.txt", cambios)
        self.assertNotIn("nuevo.txt", cambios)
        self.assertTrue(r["datos"]["base_rama"].startswith("ci-base/"))
        self.assertEqual(r["datos"]["base"], "main")
        self.assertEqual(self.ramas_base(), [])

    def test_si_no_se_puede_comprobar_el_choque_no_corre_el_ci(self):
        # La rama base que no existe hace fallar el fetch real de
        # `_archivos_en_conflicto`: no se sabe si choca y no se corre el CI.
        recursos = {
            "ci-linux": {
                **self.recursos["ci-linux"],
                "base": "rama-que-no-existe",
            }
        }
        r = cap.ejecutar(
            recursos,
            cap.CI,
            "ci-linux",
            {"job": "todos"},
            self.destino,
            espacio=self.espacio,
        )
        self.assertEqual(r["estado"], "error", r)
        self.assertIn("no se pudo comprobar", r["motivo"])

    def test_el_choque_distingue_no_choca_de_no_poder_comprobar(self):
        self.destino.mkdir(parents=True, exist_ok=True)
        sha, _ = cap._commit_temporal(self.espacio, self.destino, "test")
        self.assertEqual(
            cap._archivos_en_conflicto(
                self.espacio, str(self.bare), "main", sha
            ),
            [],
        )
        self.assertIsNone(
            cap._archivos_en_conflicto(
                self.espacio, str(self.bare), "rama-que-no-existe", sha
            )
        )

    def test_goncloud_motor_sin_workflow_en_el_recurso_sigue_usando_pruebas_yml(self):
        recursos = {
            "ci-linux": {
                "tipo": "ci",
                "autorizacion": "",
                "repo": "gon0801/goncloud-motor",
                "base": "main",
            }
        }

        r = cap.ejecutar(
            recursos,
            cap.CI,
            "ci-linux",
            {"job": "todos"},
            self.destino,
            espacio=self.espacio,
        )

        self.assertEqual(r["estado"], "ok", r)
        jobs = {j["nombre"]: j for j in r["datos"]["jobs"]}
        self.assertIn("gate", jobs)
        self.assertIn("ops-tests", jobs)
        self.assertTrue(jobs["macos"]["reusado"])

    def test_un_job_exacto_sin_base_compara_con_head(self):
        r = cap.ejecutar(
            self.recursos,
            cap.CI,
            "ci-linux",
            {"job": "bateria"},
            self.destino,
            espacio=self.espacio,
        )
        self.assertEqual(r["estado"], "ok", r)
        sha = r["datos"]["sha"]
        padre = subprocess.run(
            ["git", "--git-dir", str(self.bare), "rev-parse", sha + "^"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        arbol_padre = subprocess.run(
            ["git", "--git-dir", str(self.bare), "rev-parse", padre + "^{tree}"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        head_tree = subprocess.run(
            ["git", "-C", str(self.espacio), "rev-parse", "HEAD^{tree}"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.assertEqual(arbol_padre, head_tree)
        mensaje = subprocess.run(
            ["git", "--git-dir", str(self.bare), "log", "-1", "--format=%B", sha],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("jobs_extra=bateria", mensaje)

    def test_un_base_arbol_roto_corre_todo_y_no_compara_con_head(self):
        r = cap.ejecutar(
            self.recursos,
            cap.CI,
            "ci-linux",
            {"job": "todos", "base_arbol": "no-es-un-arbol"},
            self.destino,
            espacio=self.espacio,
        )
        self.assertEqual(r["estado"], "ok", r)
        sha = r["datos"]["sha"]
        padre = subprocess.run(
            ["git", "--git-dir", str(self.bare), "rev-parse", sha + "^"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.assertEqual(
            subprocess.run(
                ["git", "--git-dir", str(self.bare), "ls-tree", padre],
                capture_output=True, text=True, check=True,
            ).stdout.strip(),
            "",
            "un base inválido usa el árbol vacío, no HEAD",
        )


GH_SIN_PRUEBAS = r'''#!{python}
import json, os, sys
d = os.environ["FAKE_GH_DIR"]
args = sys.argv[1:]
def val(flag):
    return args[args.index(flag) + 1]
ruta = os.path.join(d, "estado.json")
estado = json.load(open(ruta)) if os.path.exists(ruta) else {"llamadas": []}
estado["llamadas"].append(args)
def guardar():
    json.dump(estado, open(ruta, "w"))
sha = val("--commit") if "--commit" in args else ""
runs = [
    {{"databaseId": 1234, "status": "completed", "conclusion": "success",
      "headSha": sha, "event": "pull_request", "workflowName": "quality",
      "url": "https://github.com/gon0801/goncloud-Orbit/actions/runs/1234"}},
    {{"databaseId": 1235, "status": "completed", "conclusion": "failure",
      "headSha": sha, "event": "pull_request", "workflowName": "quality-extra",
      "url": "https://github.com/gon0801/goncloud-Orbit/actions/runs/1235"}},
    {{"databaseId": 1236, "status": "completed", "conclusion": "failure",
      "headSha": sha, "event": "pull_request", "workflowName": "ai-review-publish",
      "url": "https://github.com/gon0801/goncloud-Orbit/actions/runs/1236"}},
]
if args[:2] == ["pr", "create"]:
    guardar()
    print("https://github.com/gon0801/goncloud-Orbit/pull/7")
elif args[:2] == ["pr", "view"]:
    guardar()
    print(json.dumps({{"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"}}))
elif args[:2] == ["run", "list"]:
    guardar()
    print(json.dumps(runs))
elif args[:2] == ["run", "view"] and "--log" not in args:
    guardar()
    rid = args[2]
    jobs = (
        [{{"name": "quality", "databaseId": 201, "conclusion": "success"}}]
        if rid == "1234"
        else [{{"name": "lint", "databaseId": 202, "conclusion": "failure"}}]
    )
    print(json.dumps({{"status": "completed",
        "conclusion": "success" if rid == "1234" else "failure",
        "url": "https://github.com/gon0801/goncloud-Orbit/actions/runs/" + rid,
        "jobs": jobs}}))
elif args[:2] == ["run", "view"]:
    guardar()
    sys.stdout.write(open(os.path.join(d, "log-" + val("--job") + ".txt")).read())
elif args[:2] == ["pr", "close"]:
    guardar()
else:
    guardar()
    sys.exit(2)
'''


class EsperaTodosLosWorkflowsDelRepo(_Ci):
    """Un repo que no tiene `pruebas.yml` no puede esperar ese workflow: el
    veredicto cubre todos los workflows de pull_request del SHA, salvo los de
    ai-review, que ya cubre la revisión de DeepSeek."""

    def setUp(self):
        super().setUp()
        (self.fake / "log-202.txt").write_text("FALLA de lint\n", encoding="utf-8")
        (self.fake / "estado.json").write_text(json.dumps({"llamadas": []}))
        (self.bin / "gh").write_text(
            GH_SIN_PRUEBAS.replace("{{", "{").replace("}}", "}").replace(
                "#!{python}", "#!" + sys.executable
            )
        )
        (self.bin / "gh").chmod(0o755)
        self.recursos = {
            "ci-linux": {"tipo": "ci", "autorizacion": "", "repo": "o/r", "base": "main"}
        }

    def llamadas_run_list(self) -> list[list[str]]:
        return [
            llamada
            for llamada in json.loads((self.fake / "estado.json").read_text())["llamadas"]
            if llamada[:2] == ["run", "list"]
        ]

    def test_espera_los_workflows_de_pull_request_y_no_pruebas_yml(self):
        r = self.pedir("todos")

        self.assertEqual(r["estado"], "ok", r)
        for llamada in self.llamadas_run_list():
            self.assertNotIn("--workflow", llamada)
        jobs = {j["nombre"]: j for j in r["datos"]["jobs"]}
        self.assertEqual(jobs["quality"]["conclusion"], "success")
        self.assertEqual(jobs["lint"]["conclusion"], "failure")
        self.assertEqual(r["datos"]["fallas"], ["lint"])
        self.assertEqual(r["datos"]["conclusion"], "failure")
        self.assertNotIn("ai-review-publish", jobs)

    def test_un_job_exacto_sale_del_workflow_que_lo_tiene(self):
        r = self.pedir("lint")

        self.assertEqual(r["estado"], "ok", r)
        self.assertEqual(r["datos"]["job"], "lint")
        self.assertEqual(r["datos"]["run"], "1235")
        self.assertEqual(r["datos"]["conclusion"], "failure")


GH_LENTO = r'''#!{python}
import json, os, sys
d = os.environ["FAKE_GH_DIR"]
args = sys.argv[1:]
def val(flag):
    return args[args.index(flag) + 1]
ruta = os.path.join(d, "estado.json")
estado = json.load(open(ruta)) if os.path.exists(ruta) else {"llamadas": [], "sondeos": 0}
estado["llamadas"].append(args)
def guardar():
    json.dump(estado, open(ruta, "w"))
sha = val("--commit") if "--commit" in args else ""
def runs(estado_run):
    return [
        {{"databaseId": 1234, "status": estado_run, "conclusion": "success",
          "headSha": sha, "event": "pull_request", "workflowName": "quality",
          "url": "https://github.com/gon0801/goncloud-Orbit/actions/runs/1234"}},
        {{"databaseId": 1235, "status": estado_run,
          "conclusion": "success" if estado_run != "completed" else "failure",
          "headSha": sha, "event": "pull_request", "workflowName": "quality-extra",
          "url": "https://github.com/gon0801/goncloud-Orbit/actions/runs/1235"}},
    ]
if args[:2] == ["pr", "create"]:
    guardar()
    print("https://github.com/gon0801/goncloud-Orbit/pull/7")
elif args[:2] == ["pr", "view"]:
    guardar()
    print(json.dumps({{"mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"}}))
elif args[:2] == ["run", "list"]:
    estado["sondeos"] = int(estado.get("sondeos") or 0) + 1
    if estado["sondeos"] == 1:
        print("[]")
    elif estado["sondeos"] == 2:
        print(json.dumps(runs("in_progress")))
    else:
        print(json.dumps(runs("completed")))
    guardar()
elif args[:2] == ["run", "view"] and "--log" not in args:
    guardar()
    rid = args[2]
    jobs = (
        [{{"name": "quality", "databaseId": 201, "conclusion": "success"}}]
        if rid == "1234"
        else [{{"name": "lint", "databaseId": 202, "conclusion": "failure"}}]
    )
    print(json.dumps({{"status": "completed",
        "conclusion": "success" if rid == "1234" else "failure",
        "url": "https://github.com/gon0801/goncloud-Orbit/actions/runs/" + rid,
        "jobs": jobs}}))
elif args[:2] == ["run", "view"]:
    guardar()
    sys.stdout.write(open(os.path.join(d, "log-" + val("--job") + ".txt")).read())
elif args[:2] == ["pr", "close"]:
    guardar()
else:
    guardar()
    sys.exit(2)
'''


class EsperaARunsQueAparecenTarde(_Ci):
    """Recién creado el PR el repo puede no tener runs, o tenerlos en curso.
    El plazo llega como duración: no se puede comparar con un epoch."""

    def setUp(self):
        super().setUp()
        (self.fake / "log-202.txt").write_text("FALLA de lint\n", encoding="utf-8")
        (self.fake / "estado.json").write_text(
            json.dumps({"llamadas": [], "sondeos": 0})
        )
        (self.bin / "gh").write_text(
            GH_LENTO.replace("{{", "{").replace("}}", "}").replace(
                "#!{python}", "#!" + sys.executable
            )
        )
        (self.bin / "gh").chmod(0o755)
        self.recursos = {
            "ci-linux": {"tipo": "ci", "autorizacion": "", "repo": "o/r", "base": "main"}
        }

    def test_espera_a_que_aparezcan_y_terminen_los_runs(self):
        r = self.pedir("todos")

        self.assertEqual(r["estado"], "ok", r)
        estado = json.loads((self.fake / "estado.json").read_text())
        self.assertGreaterEqual(estado["sondeos"], 3)
        jobs = {j["nombre"]: j for j in r["datos"]["jobs"]}
        self.assertEqual(jobs["lint"]["conclusion"], "failure")
        self.assertEqual(r["datos"]["fallas"], ["lint"])


if __name__ == "__main__":
    unittest.main()
