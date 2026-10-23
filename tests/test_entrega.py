"""Una entrega válida cierra al agente. Una que no lo es, no."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from motor import runwrap

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


if __name__ == "__main__":
    unittest.main()
