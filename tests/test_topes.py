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

    def test_deploy_copia_que_falla_una_vez_luego_despliega_de_verdad(self):
        import subprocess as sp
        import time

        from motor import machine as m
        from motor.plan import Plan
        from tests.test_no_caerse import Banco
        from tests.test_puro import ev

        banco = Banco("setUp")
        banco.setUp()
        prod = banco.tmp / "prod"
        prod.write_text("viejo\n", encoding="utf-8")
        b = banco.plan["blocks"][0]
        b["lane"] = "release"
        b["release"] = {
            "desplegar": f"-c 'echo nuevo > {prod}'",
            "checklist": f"-c 'test -f {prod}'",
            "rollback": "-c true",
        }
        d = banco.daemon()
        plan = Plan.from_dict(banco.plan)
        sha = sp.run(
            ["git", "rev-parse", "main"],
            cwd=plan.block("A").repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        base = [
            ev(
                "AttemptStarted",
                "A",
                {
                    "n": 1,
                    "role": "implementar",
                    "modelo": "glm",
                    "proc": {},
                    "dir": "d",
                },
            ),
            ev(
                "AttemptEnded",
                "A",
                {
                    "n": 1,
                    "role": "implementar",
                    "outcome": {"kind": "entregado", "sha": sha},
                },
            ),
            ev("Merged", "A", {"sha": sha, "commit": sha}),
        ]
        snap = lambda: m.fold(  # noqa: E731
            plan,
            base + [e for e in d.store.events("demo") if e.kind == "DeployStarted"],
        )
        estorbo = d.cx.home / "tmp" / "despliega-demo-A"
        estorbo.parent.mkdir(parents=True, exist_ok=True)
        estorbo.write_text("la copia falla una vez", encoding="utf-8")
        fallo = None
        fin = time.time() + 30
        while time.time() < fin and fallo is None:
            try:
                d.fx.deploy(plan, snap(), m.Deploy("A", sha))
            except RuntimeError as e:
                fallo = str(e)
            time.sleep(0.2)
        self.assertIsNotNone(fallo)
        estorbo.unlink()
        fin = time.time() + 30
        while time.time() < fin and not banco.eventos("Deployed"):
            d.fx.deploy(plan, snap(), m.Deploy("A", sha))
            time.sleep(0.2)
        (r,) = banco.eventos("Deployed")
        self.assertTrue(r.data["ok"], r.data)
        self.assertNotIn("ya estaba desplegado", r.data["detalle"])
        self.assertEqual(prod.read_text(encoding="utf-8").strip(), "nuevo")

    def test_verify_con_maquina_cambiada_se_recoge_a_la_primera(self):
        import subprocess as sp
        import time

        from motor import machine as m
        from motor.plan import Plan
        from tests.test_no_caerse import Banco

        banco = Banco("setUp")
        banco.setUp()
        d = banco.daemon()
        banco.plan["blocks"][0]["rows"] = [
            {"id": "A.1", "contenido": "x", "acepta": "true"}
        ]
        plan = Plan.from_dict(banco.plan)
        sha = sp.run(
            ["git", "rev-parse", "main"],
            cwd=plan.block("A").repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        mq = banco.home / "maquina.toml"
        texto = mq.read_text(encoding="utf-8")
        self.assertNotIn("tiene", texto)
        mq.write_text('tiene = ["ssh-goncloud"]\n' + texto, encoding="utf-8")
        fin = time.time() + 15
        while time.time() < fin and not banco.eventos("Verified"):
            d.fx.verify(plan, m.fold(plan, []), m.Verify("A", sha))
            time.sleep(0.3)
        self.assertEqual(len(banco.eventos("Verified")), 1)
        self.assertEqual(len(banco.eventos("VerifyStarted")), 1)

    def test_segundo_sha_no_recoge_el_outcome_del_primero(self):
        import subprocess as sp
        import time
        from pathlib import Path

        from motor import machine as m
        from motor.plan import Plan
        from tests.test_no_caerse import Banco

        banco = Banco("setUp")
        banco.setUp()
        d = banco.daemon()
        banco.plan["blocks"][0]["rows"] = [
            {
                "id": "A.1",
                "contenido": "x",
                "acepta": "if test -f roto; then sleep 3; exit 1; fi",
            }
        ]
        plan = Plan.from_dict(banco.plan)
        bare = Path(plan.block("A").repo)
        seed = bare.parent / ("seed-" + bare.name[:-4])

        def git(*a):
            return sp.run(
                ["git", "-c", "user.name=x", "-c", "user.email=x@l", *a],
                cwd=seed,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()

        def verifica(sha):
            fin = time.time() + 20
            hallados = []
            while time.time() < fin:
                hallados = [
                    e.data for e in banco.eventos("Verified") if e.data["sha"] == sha
                ]
                if hallados:
                    break
                d.fx.verify(plan, m.fold(plan, []), m.Verify("A", sha))
                time.sleep(0.2)
            return hallados

        sha1 = git("rev-parse", "HEAD")
        v1 = verifica(sha1)
        self.assertTrue(v1 and v1[0]["ok"], v1)
        (seed / "roto").write_text("x", encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "rompe la Acepta")
        git("push", "-q", str(bare), "main")
        sha2 = git("rev-parse", "HEAD")
        v2 = verifica(sha2)
        self.assertTrue(v2, "el SHA nuevo nunca anota su propio Verified")
        self.assertFalse(v2[0]["ok"])
        dirs = {
            e.data["sha"]: Path(e.data["dir"]).name
            for e in banco.eventos("VerifyStarted")
        }
        self.assertNotEqual(dirs[sha1], dirs[sha2])


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

    def test_plan_limpiado_y_reentregado_vuelve_a_estar_en_plans(self):
        import json as js

        from tests.test_no_caerse import Banco

        banco = Banco("setUp")
        banco.setUp()
        d = banco.daemon()
        d.store.append("demo", "", "PlanLoaded", {"plan": banco.plan, "version": 1})
        d.store.append("demo", "", "Limpiado", {"ramas": []})
        self.assertNotIn("demo", [p.id for p in d.plans()])
        nuevo = js.loads(js.dumps(banco.plan))
        nuevo["blocks"][0]["rows"].append(
            {"id": "A.9", "contenido": "fila nueva tras limpiar", "acepta": "true"}
        )
        inbox = banco.home / "inbox"
        inbox.mkdir(exist_ok=True)
        (inbox / "v2.json").write_text(
            js.dumps({"cmd_id": "v2", "tipo": "plan", "plan": nuevo, "por": "David"}),
            encoding="utf-8",
        )
        d.tick()
        self.assertIn("demo", [p.id for p in d.plans()])

    def test_purga_borra_registros_de_un_limpiado_aunque_plans_no_lo_traiga(self):
        import time

        from tests.test_no_caerse import Banco

        banco = Banco("setUp")
        banco.setUp()
        d = banco.daemon()
        d.store.append("demo", "", "PlanLoaded", {"plan": banco.plan, "version": 1})
        d.store.append("demo", "", "Limpiado", {"ramas": []})
        carpeta = banco.home / "intentos" / "demo"
        carpeta.mkdir(parents=True)
        (carpeta / "registro.txt").write_text("x", encoding="utf-8")
        self.assertEqual(d.plans(), [])
        d.fx.purga_registros(d.plans(), time.time() + 15 * 86400)
        self.assertFalse(carpeta.exists())

    def test_el_cupo_por_cuenta_se_respeta_entre_planes_del_mismo_tick(self):
        import json as js

        from tests.test_no_caerse import Banco

        banco = Banco("setUp")
        banco.setUp()
        d = banco.daemon()

        def arranca(plan, snap, a):
            d.store.append(
                plan.id,
                a.block,
                "AttemptStarted",
                {
                    "n": a.n,
                    "role": a.role,
                    "modelo": a.modelo,
                    "proc": {},
                    "dir": "d",
                },
            )

        d.fx.start_attempt = arranca
        for pid in ("uno", "dos", "tres", "cuatro"):
            p = js.loads(js.dumps(banco.plan))
            p["id"] = pid
            p["blocks"] = [p["blocks"][0]]
            p["blocks"][0]["depende"] = []
            d.store.append(pid, "", "PlanLoaded", {"plan": p, "version": 1})
        d.tick()
        pol = d.cx.policy
        por_cuenta: dict[str, int] = {}
        for e in banco.eventos("AttemptStarted"):
            c = pol.cuenta.get(e.data["modelo"], e.data["modelo"])
            por_cuenta[c] = por_cuenta.get(c, 0) + 1
        self.assertTrue(por_cuenta)
        for c, k in por_cuenta.items():
            self.assertLessEqual(k, pol.max_parallel.get(c, 1), c)


if __name__ == "__main__":
    unittest.main()
