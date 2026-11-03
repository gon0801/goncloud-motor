"""`ci.correr job=todos` devuelve todos los jobs y deja el log de los rojos.

El commit temporal lleva `jobs=...` para que el workflow corra solo esos jobs;
los demás quedan reusados. Se usa el mismo `gh` falso que las pruebas de CI.
"""

from __future__ import annotations

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
        ]}}))
elif args[:2] == ["run", "view"]:
    sys.stdout.write(open(os.path.join(d, "log-" + val("--job") + ".txt")).read())
elif args[:2] == ["pr", "close"]:
    pass
else:
    sys.exit(2)
'''


class CorreTodosLosJobs(_Ci):
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
            {"job": "todos", "jobs": "ops-tests,bateria"},
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

        mensaje = subprocess.run(
            ["git", "--git-dir", str(self.bare), "log", "-1", "--format=%B", datos["sha"]],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertIn("jobs=ops-tests,bateria", mensaje)

        evidencia = r["evidencia"]
        self.assertEqual(evidencia["fallas"], ["bateria"])
        self.assertEqual(
            {j["nombre"]: j["reusado"] for j in evidencia["jobs"]}["macos"], True
        )
        self.assertEqual(self.ramas_remotas(), [])

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
            {"job": "todos", "jobs": "ops-tests,bateria"},
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


if __name__ == "__main__":
    unittest.main()
