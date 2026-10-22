from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from motor import workspace
from motor.github import GhGitHub


class TopesTest(unittest.TestCase):
    def test_git_con_tope_segun_operacion_y_sin_prompt(self):
        vistos = []

        def fake_run(argv, **kw):
            vistos.append((argv, kw))
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(workspace.subprocess, "run", fake_run):
            workspace.git("clone", "x", "y")
            workspace.git("fetch", "origin")
            workspace.git("push", "origin", "main")
            workspace.git("status")
            workspace.git("-c", "user.name=x", "push", "origin", "main")
        self.assertEqual([k["timeout"] for _, k in vistos], [180, 180, 180, 60, 180])
        for _, kw in vistos:
            self.assertEqual(kw["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_gh_con_tope_y_sin_prompt(self):
        vistos = []

        def fake_run(argv, **kw):
            vistos.append((argv, kw))
            return subprocess.CompletedProcess(argv, 0, "ok", "")

        with mock.patch("motor.github.subprocess.run", fake_run):
            GhGitHub(env={})._gh("pr", "view", "1")
        self.assertEqual(vistos[0][1]["timeout"], 60)
        self.assertEqual(vistos[0][1]["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_git_pasarse_del_tope_lanza(self):
        import socket
        import tempfile
        import threading
        import time
        from pathlib import Path

        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(5)
        puerto = srv.getsockname()[1]
        aceptados: list[socket.socket] = []

        def acepta():
            while True:
                try:
                    c, _ = srv.accept()
                except OSError:
                    return
                aceptados.append(c)

        hilo = threading.Thread(target=acepta, daemon=True)
        hilo.start()
        try:
            inicio = time.time()
            with self.assertRaises(subprocess.TimeoutExpired):
                workspace.git(
                    "ls-remote",
                    f"http://127.0.0.1:{puerto}/repo",
                    timeout=2,
                )
            self.assertLess(time.time() - inicio, 10)
            with tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp) / "repo"
                subprocess.run(
                    ["git", "init", "-q", str(repo)],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(repo),
                        "remote",
                        "add",
                        "origin",
                        f"http://127.0.0.1:{puerto}/repo",
                    ],
                    check=True,
                    capture_output=True,
                )
                with self.assertRaises(subprocess.TimeoutExpired):
                    workspace.git("fetch", "origin", cwd=repo, timeout=2)
        finally:
            srv.close()
            for c in aceptados:
                c.close()

    def test_run_no_espera_al_hijo_en_segundo_plano(self):
        import os
        import time

        from motor.effects import _run

        inicio = time.time()
        r = _run("echo ok; (sleep 30) &", None, dict(os.environ), timeout=10)
        self.assertLess(time.time() - inicio, 2)
        self.assertEqual(r.returncode, 0)
        self.assertIn("ok", r.stdout)

    def test_run_colgado_devuelve_124_con_salida_parcial(self):
        import os

        from motor.effects import _run

        r = _run("echo parcial; sleep 30", None, dict(os.environ), timeout=1)
        self.assertEqual(r.returncode, 124)
        self.assertIn("parcial", r.stdout)


class ApartTest(unittest.TestCase):
    def test_verify_corre_aparte_y_se_recoge_en_otro_tick(self):
        import subprocess as sp
        import time

        from motor import machine as m
        from motor.plan import Plan
        from tests.test_no_caerse import Banco

        banco = Banco("setUp")
        banco.setUp()
        d = banco.daemon()
        plan_dict = banco.plan
        plan_dict["blocks"][0]["rows"] = [
            {"id": "A.1", "contenido": "x", "acepta": "sleep 3; true"}
        ]
        plan = Plan.from_dict(plan_dict)
        repo = plan.block("A").repo
        sha = sp.run(
            ["git", "rev-parse", "main"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        inicio = time.time()
        d.fx.verify(plan, m.fold(plan, []), m.Verify("A", sha))
        self.assertLess(time.time() - inicio, 2)
        self.assertEqual(len(banco.eventos("VerifyStarted")), 1)
        self.assertEqual(banco.eventos("Verified"), [])
        fin = time.time() + 20
        while time.time() < fin and not banco.eventos("Verified"):
            d.fx.verify(plan, m.fold(plan, []), m.Verify("A", sha))
            time.sleep(0.2)
        self.assertEqual(len(banco.eventos("Verified")), 1)
        self.assertTrue(banco.eventos("Verified")[0].data["ok"])
        self.assertEqual(len(banco.eventos("VerifyStarted")), 1)

    def test_si_el_aparte_muere_se_relanza_y_si_termino_se_recoge(self):
        import os
        import signal
        import subprocess as sp
        import time

        from motor import machine as m
        from motor.plan import Plan
        from tests.test_no_caerse import Banco

        banco = Banco("setUp")
        banco.setUp()
        d = banco.daemon()
        plan_dict = banco.plan
        plan_dict["blocks"][0]["rows"] = [
            {"id": "A.1", "contenido": "x", "acepta": "sleep 2; true"}
        ]
        plan = Plan.from_dict(plan_dict)
        repo = plan.block("A").repo
        sha = sp.run(
            ["git", "rev-parse", "main"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        d.fx.verify(plan, m.fold(plan, []), m.Verify("A", sha))
        proc = banco.eventos("VerifyStarted")[0].data["proc"]
        os.killpg(int(proc["pid"]), signal.SIGKILL)
        time.sleep(0.3)
        d2 = banco.daemon()
        fin = time.time() + 20
        while time.time() < fin and not banco.eventos("Verified"):
            d2.fx.verify(plan, m.fold(plan, []), m.Verify("A", sha))
            time.sleep(0.2)
        self.assertEqual(len(banco.eventos("Verified")), 1)
        self.assertGreaterEqual(len(banco.eventos("VerifyStarted")), 2)


class CicloTest(unittest.TestCase):
    def test_tick_sin_trabajo_con_muchos_limpiados_es_rapido(self):
        import json as js
        import time

        from tests.test_no_caerse import Banco

        banco = Banco("setUp")
        banco.setUp()
        d = banco.daemon()
        filas = []
        for i in range(100):
            pid = f"viejo-{i}"
            plan = {**banco.plan, "id": pid}
            filas.append((1.0, pid, "", "PlanLoaded", js.dumps({"plan": plan})))
            filas.append((1.0, pid, "", "Limpiado", js.dumps({"ramas": []})))
            for j in range(298):
                filas.append((1.0, pid, "A", "Note", js.dumps({"texto": str(j)})))
        d.store.db.executemany(
            "INSERT INTO events (ts, plan, block, kind, data) VALUES (?,?,?,?,?)",
            filas,
        )
        indices = {
            r[0]
            for r in d.store.db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='events'"
            )
        }
        self.assertIn("idx_events_plan", indices)
        self.assertNotIn("viejo-0", [p.id for p in d.plans()])
        d.tick()
        inicio = time.time()
        d.tick()
        self.assertLess(time.time() - inicio, 0.5)


if __name__ == "__main__":
    unittest.main()
