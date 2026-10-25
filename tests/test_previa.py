"""Fallas del 2026-10-24: la máquina se distingue del código."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "proto"))
import sandbox  # noqa: E402

DATOS = Path(__file__).resolve().parent / "datos" / "previa"
ENV = {**os.environ, "PYTHONPATH": str(RAIZ), "PYTHONUTF8": "1"}


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _motor(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "motor", *args],
        cwd=RAIZ,
        env=ENV,
        capture_output=True,
        text=True,
        timeout=180,
    )


class PreviaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="previa-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home, _plan = sandbox.create(self.tmp / "b")
        origin, seed = self.tmp / "repo.git", self.tmp / "seed"
        _git("init", "-q", "--bare", "-b", "main", str(origin), cwd=self.tmp)
        seed.mkdir()
        _git("init", "-q", "-b", "main", cwd=seed)
        _git("config", "user.email", "t@l", cwd=seed)
        _git("config", "user.name", "t", cwd=seed)
        (seed / "README.md").write_text("hola\n")
        (seed / ".gitignore").write_text("ignorado.log\n")
        (seed / ".motor.toml").write_text('[repo]\nprincipal = "main"\n')
        _git("add", "-A", cwd=seed)
        _git("commit", "-q", "-m", "inicio", cwd=seed)
        _git("remote", "add", "origin", str(origin), cwd=seed)
        _git("push", "-q", "origin", "main", cwd=seed)
        self.repo = origin

    def _plan(self, nombre: str) -> Path:
        texto = (DATOS / nombre).read_text(encoding="utf-8")
        texto = re.sub(r"^repo:.*$", f"repo: {self.repo.as_posix()}", texto, flags=re.M)
        p = self.tmp / nombre
        p.write_text(texto, encoding="utf-8")
        return p

    def _valida_previa(self, nombre: str) -> subprocess.CompletedProcess:
        return _motor(
            "valida", "--previa", str(self._plan(nombre)), "--home", str(self.home)
        )

    def test_casos_del_24_de_octubre(self):
        malo = self._valida_previa("malo.md")
        self.assertNotEqual(malo.returncode, 0, malo.stdout + malo.stderr)
        self.assertIn("A.1:", malo.stdout)
        self.assertIn("falta el módulo modulo_que_no_existe_24oct", malo.stdout)

        comillas = self._valida_previa("previa-comillas.md")
        self.assertNotEqual(comillas.returncode, 0, comillas.stdout + comillas.stderr)
        self.assertIn("A.1:", comillas.stdout)
        self.assertIn("comillas invertidas", comillas.stdout)

        ignorado = self._valida_previa("previa-ignorado.md")
        self.assertNotEqual(ignorado.returncode, 0, ignorado.stdout + ignorado.stderr)
        self.assertIn("A.1:", ignorado.stdout)
        self.assertIn("ignorado.log", ignorado.stdout)

        bueno = self._valida_previa("bueno.md")
        self.assertEqual(bueno.returncode, 0, bueno.stdout + bueno.stderr)

        entrega = _motor(
            "entrega",
            str(self._plan("malo.md")),
            "--home",
            str(self.home),
            "--json",
            "--espera",
            "0",
        )
        self.assertNotEqual(entrega.returncode, 0, entrega.stdout + entrega.stderr)
        self.assertEqual(json.loads(entrega.stdout)["estado"], "rechazado")
        self.assertFalse(list((self.home / "inbox").glob("*.json")))


if __name__ == "__main__":
    unittest.main()
