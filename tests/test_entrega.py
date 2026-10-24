"""Una entrega válida cierra al agente. Una que no lo es, no. Nadie la pisa después."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from motor import entrega
from motor import machine as m
from motor import runwrap
from motor.daemon import Daemon
from motor.plan import parse_plan

FIXTURE = (
    Path(__file__).resolve().parent / "datos" / "entrega" / "result-2026-10-22.json"
)
RAMA = "bloque"
ENCARGO = "a" * 40
OTRO = "b" * 40


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _veredicto(sha: str) -> str:
    return json.dumps(
        {
            "sha": sha,
            "veredicto": "APROBADO",
            "bloqueantes": [],
            "notas": [],
        }
    )


class CierreTest(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="entrega-"))
        self.addCleanup(shutil.rmtree, self.t, True)

    def prepara(self, *, push: bool) -> Path:
        origin, work = self.t / "origin.git", self.t / "w"
        _git("init", "-q", "--bare", "-b", "main", str(origin))
        _git("init", "-q", "-b", RAMA, str(work))
        _git("-C", str(work), "config", "user.email", "t@l")
        _git("-C", str(work), "config", "user.name", "t")
        (work / "f.txt").write_text("a\n", encoding="utf-8")
        _git("-C", str(work), "add", "f.txt")
        _git("-C", str(work), "commit", "-q", "-m", "a")
        _git("-C", str(work), "remote", "add", "origin", str(origin))
        if push:
            _git("-C", str(work), "push", "-q", "origin", f"HEAD:refs/heads/{RAMA}")
        return work

    def corre(
        self,
        script: str,
        *extra: str,
        cwd: Path | None = None,
        sin_avance: float = 30,
        timeout: float = 45,
    ) -> tuple[dict, float]:
        d = self.t / "intento"
        d.mkdir(exist_ok=True)
        if cwd is None:
            cwd = self.t / "vacio"
            cwd.mkdir(exist_ok=True)
        inicio = time.time()
        runwrap.main(
            [
                "--dir", str(d), "--cwd", str(cwd), "--timeout", str(timeout),
                "--sin-avance", str(sin_avance), "--cierre", "1",
                *extra, "--", sys.executable, "-c", script,
            ]
        )  # fmt: skip
        return json.loads((d / "outcome.json").read_text()), time.time() - inicio

    def test_el_result_real_con_sha_null_no_cierra(self):
        real = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertIsNone(real["sha"])
        self.assertIn("sha va en null", real["resumen"])
        o, dura = self.corre(
            "import os, pathlib, shutil, time\n"
            f"shutil.copy({str(FIXTURE)!r}, "
            "pathlib.Path(os.environ['MOTOR_ATTEMPT_DIR'], 'result.json'))\n"
            "time.sleep(3)\n",
            "--entrega-rama",
            RAMA,
        )
        self.assertFalse(o["cerrado_tras_resultado"])
        self.assertLess(dura, 10)
        self.assertGreaterEqual(dura, 2.5)

    def test_bloqueado_con_motivo_cierra(self):
        o, dura = self.corre(
            "import os, pathlib, time\n"
            "pathlib.Path(os.environ['MOTOR_ATTEMPT_DIR'], 'result.json').write_text("
            '\'{"bloqueado": "falta el acceso"}\')\n'
            "time.sleep(30)\n",
            "--entrega-rama",
            RAMA,
        )
        self.assertTrue(o["cerrado_tras_resultado"])
        self.assertLess(dura, 15)
        self.assertEqual(o["exit"], 0)

    def test_la_punta_remota_cierra_como_hoy(self):
        work = self.prepara(push=True)
        o, dura = self.corre(
            "import json, os, pathlib, subprocess, time\n"
            "sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()\n"
            "pathlib.Path(os.environ['MOTOR_ATTEMPT_DIR'], 'result.json').write_text("
            "json.dumps({'sha': sha, 'resumen': 'listo'}))\n"
            "time.sleep(30)\n",
            "--entrega-rama",
            RAMA,
            cwd=work,
        )
        self.assertTrue(o["cerrado_tras_resultado"])
        self.assertLess(dura, 15)

    def test_el_head_local_no_cierra_y_despues_del_push_si(self):
        work = self.prepara(push=False)
        o, dura = self.corre(
            "import json, os, pathlib, subprocess, time\n"
            "sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()\n"
            "pathlib.Path(os.environ['MOTOR_ATTEMPT_DIR'], 'result.json').write_text("
            "json.dumps({'sha': sha, 'resumen': 'listo'}))\n"
            "time.sleep(4)\n"
            "subprocess.check_call(["
            f"'git', 'push', '-q', 'origin', 'HEAD:refs/heads/{RAMA}'])\n"
            "time.sleep(30)\n",
            "--entrega-rama",
            RAMA,
            cwd=work,
            sin_avance=60,
            timeout=60,
        )
        local = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
        remoto = _git(
            "ls-remote", "origin", f"refs/heads/{RAMA}", cwd=work
        ).stdout.split()[0]
        self.assertEqual(remoto, local)
        self.assertTrue(o["cerrado_tras_resultado"])
        self.assertGreaterEqual(dura, 4)
        self.assertLess(dura, 20)

    def test_el_revisor_cierra_solo_con_el_sha_del_encargo(self):
        bueno, dura_ok = self.corre(
            "import os, pathlib, time\n"
            "pathlib.Path(os.environ['MOTOR_ATTEMPT_DIR'], 'result.json').write_text("
            f"{_veredicto(ENCARGO)!r})\n"
            "time.sleep(30)\n",
            "--entrega-sha",
            ENCARGO,
        )
        self.assertTrue(bueno["cerrado_tras_resultado"])
        self.assertLess(dura_ok, 15)
        self.t.joinpath("intento").rename(self.t / "intento-ok")
        malo, dura_mal = self.corre(
            "import os, pathlib, time\n"
            "pathlib.Path(os.environ['MOTOR_ATTEMPT_DIR'], 'result.json').write_text("
            f"{_veredicto(OTRO)!r})\n"
            "time.sleep(3)\n",
            "--entrega-sha",
            ENCARGO,
        )
        self.assertFalse(malo["cerrado_tras_resultado"])
        self.assertLess(dura_mal, 10)
        self.assertGreaterEqual(dura_mal, 2.5)

    def recoge(self, carpeta: Path, sha: str) -> dict:
        proto = Path(__file__).resolve().parent.parent / "proto"
        if str(proto) not in sys.path:
            sys.path.insert(0, str(proto))
        import sandbox

        home, plan_md = sandbox.create(self.t / "banco")
        daemon = Daemon(home)
        self.addCleanup(daemon._candado.close)
        plan = parse_plan(plan_md.read_text(encoding="utf-8")).to_dict()
        daemon.store.append("demo", "", "PlanLoaded", {"plan": plan, "por": "t"})
        daemon.fx.cx.ws.punta = lambda repo, rama, sha=sha: sha
        att = m.Attempt(
            n=1,
            role="implementar",
            modelo="glm",
            started=0.0,
            proc={},
            dir=str(carpeta),
        )
        cargado = daemon.plans()[0]
        return daemon.fx.collect(cargado, daemon._fold(cargado), "A", att)

    def test_un_hijo_pisa_result_y_el_motor_toma_la_entrega(self):
        work = self.prepara(push=True)
        hijo = self.t / "hijo.py"
        hijo.write_text(
            "import json, os, pathlib, time\n"
            "d = pathlib.Path(os.environ['MOTOR_ATTEMPT_DIR'])\n"
            "for _ in range(100):\n"
            "    if (d / 'entrega.json').exists():\n"
            "        break\n"
            "    time.sleep(0.05)\n"
            "else:\n"
            "    raise SystemExit(2)\n"
            "(d / 'result.json').write_text(json.dumps({'sha': None}))\n",
            encoding="utf-8",
        )
        o, dura = self.corre(
            "import json, os, pathlib, subprocess, sys, time\n"
            "d = pathlib.Path(os.environ['MOTOR_ATTEMPT_DIR'])\n"
            "sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()\n"
            "(d / 'result.json').write_text(json.dumps({'sha': sha, 'resumen': 'listo'}))\n"
            f"subprocess.Popen([sys.executable, {str(hijo)!r}])\n"
            "time.sleep(30)\n",
            "--entrega-rama",
            RAMA,
            cwd=work,
        )
        intento = self.t / "intento"
        sha = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
        self.assertTrue(o["cerrado_tras_resultado"])
        self.assertLess(dura, 15)
        self.assertEqual(json.loads((intento / "entrega.json").read_text())["sha"], sha)
        self.assertIsNone(json.loads((intento / "result.json").read_text())["sha"])
        self.assertIsNone(
            json.loads((intento / "result.ajeno.json").read_text())["sha"]
        )
        out = self.recoge(intento, sha)
        self.assertEqual(out["kind"], "entregado")
        self.assertEqual(out["sha"], sha)

    def test_la_ultima_entrega_valida_reemplaza_a_la_congelada(self):
        d = self.t / "intento"
        d.mkdir()
        espera = entrega.Espera(sha=ENCARGO)
        cambios = json.dumps(
            {
                "sha": ENCARGO,
                "veredicto": "CAMBIOS",
                "bloqueantes": [{"texto": "falla", "repro": "false"}],
                "notas": [],
            }
        )
        (d / "result.json").write_text(_veredicto(ENCARGO))
        self.assertGreater(espera.vigila(d, self.t), 0)
        self.assertEqual(
            json.loads((d / "entrega.json").read_text())["veredicto"], "APROBADO"
        )
        (d / "result.json").write_text(cambios)
        espera.vigila(d, self.t)
        self.assertEqual((d / "entrega.json").read_text(), cambios)
        self.assertFalse((d / "result.ajeno.json").exists())
        (d / "result.json").write_text(json.dumps({"sha": None}))
        espera.vigila(d, self.t)
        self.assertEqual((d / "entrega.json").read_text(), cambios)
        self.assertIsNone(json.loads((d / "result.ajeno.json").read_text())["sha"])
        (d / "result.json").write_text(_veredicto("otro-sha"))
        espera.vigila(d, self.t)
        self.assertEqual((d / "entrega.json").read_text(), cambios)

    def test_sin_entrega_valida_termina_sin_resultado(self):
        o, _ = self.corre("import time\ntime.sleep(0.2)\n")
        intento = self.t / "intento"
        self.assertFalse(o["cerrado_tras_resultado"])
        self.assertFalse((intento / "entrega.json").exists())
        self.assertEqual(self.recoge(intento, "c" * 40)["kind"], "sin_resultado")


if __name__ == "__main__":
    unittest.main()
