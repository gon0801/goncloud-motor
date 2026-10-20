"""Contrato de GitHub: las mismas pruebas para el falso y para el real.

El real solo corre con MOTOR_GH_SANDBOX=<dueño/repo> (un repo desechable con un check requerido
"ci" en su rama principal). Sin esa variable se salta y se dice por qué.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from motor.github import FakeGitHub, GhGitHub, MergeRechazado
from motor.workspace import git


def _bare_con_rama(t: Path) -> tuple[str, str, Path]:
    bare, w = t / "r.git", t / "w"
    git("init", "-q", "--bare", "-b", "main", str(bare))
    git("init", "-q", "-b", "main", str(w))
    (w / "ci.sh").write_text("#!/bin/bash\n! test -f roto.txt\n")
    (w / "ci.sh").chmod(0o755)
    git("add", "-A", cwd=w)
    git("-c", "user.name=a", "-c", "user.email=a@l", "commit", "-q", "-m", "base", cwd=w)
    git("push", "-q", str(bare), "main", cwd=w)
    git("checkout", "-q", "-b", "feat", cwd=w)
    (w / "x.txt").write_text("x\n")
    git("add", "-A", cwd=w)
    git("-c", "user.name=a", "-c", "user.email=a@l", "commit", "-q", "-m", "x", cwd=w)
    git("push", "-q", str(bare), "feat", cwd=w)
    return str(bare), git("rev-parse", "HEAD", cwd=w).stdout.strip(), w


class ContratoFalso(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="gh-"))
        self.repo, self.sha, self.w = _bare_con_rama(self.t)
        (self.t / "gh").mkdir()
        (self.t / "gh" / "repos.json").write_text(json.dumps({self.repo: {
            "requeridos": ["ci", "completa"], "solo_listo": ["completa"],
            "checks": {"ci": "./ci.sh", "completa": "./ci.sh", "seguridad": "./ci.sh"}}}))
        self.gh = FakeGitHub(self.t / "gh")

    def test_flujo_completo(self):
        pr = self.gh.abrir_pr(self.repo, "feat", "main", "t", "c")
        self.assertTrue(pr.borrador)
        self.assertEqual(self.gh.abrir_pr(self.repo, "feat", "main", "t", "c").numero, pr.numero, "idempotente por rama")
        v = self.gh.checks(self.repo, pr.numero, self.sha, ("seguridad",))
        self.assertEqual((v.terminal, v.faltan), (False, ("completa",)), "completa no corre en borrador")
        with self.assertRaises(MergeRechazado):
            self.gh.merge(self.repo, pr.numero, self.sha, "squash", "m")
        self.gh.marcar_listo(self.repo, pr.numero)
        self.assertTrue(self.gh.checks(self.repo, pr.numero, self.sha, ("seguridad",)).green)
        hecho = self.gh.merge(self.repo, pr.numero, self.sha, "squash", "m")
        self.assertEqual(hecho.estado, "mergeado")
        self.assertEqual(self.gh.merge(self.repo, pr.numero, self.sha, "squash", "m").estado, "mergeado", "idempotente")

    def test_merge_rechaza_si_la_punta_se_movio(self):
        pr = self.gh.abrir_pr(self.repo, "feat", "main", "t", "c")
        self.gh.marcar_listo(self.repo, pr.numero)
        (self.w / "y.txt").write_text("y\n")
        git("add", "-A", cwd=self.w)
        git("-c", "user.name=a", "-c", "user.email=a@l", "commit", "-q", "-m", "y", cwd=self.w)
        git("push", "-q", self.repo, "feat", cwd=self.w)
        with self.assertRaises(MergeRechazado):
            self.gh.merge(self.repo, pr.numero, self.sha, "squash", "m")

    def test_checks_en_rojo(self):
        (self.w / "roto.txt").write_text("1")
        git("add", "-A", cwd=self.w)
        git("-c", "user.name=a", "-c", "user.email=a@l", "commit", "-q", "-m", "r", cwd=self.w)
        git("push", "-q", self.repo, "feat", cwd=self.w)
        sha = git("rev-parse", "HEAD", cwd=self.w).stdout.strip()
        pr = self.gh.abrir_pr(self.repo, "feat", "main", "t", "c")
        v = self.gh.checks(self.repo, pr.numero, sha, ())
        self.assertEqual((v.terminal, v.green, "ci" in v.fallas), (True, False, True))


@unittest.skipUnless(os.environ.get("MOTOR_GH_SANDBOX"), "sin MOTOR_GH_SANDBOX: el contrato contra GitHub real no corre")
class ContratoReal(unittest.TestCase):  # pragma: no cover - requiere red y un repo desechable
    def test_flujo_real(self):
        nombre = os.environ["MOTOR_GH_SANDBOX"]
        gh = GhGitHub()
        with tempfile.TemporaryDirectory() as t:
            w = Path(t) / "w"
            subprocess.run(["gh", "repo", "clone", nombre, str(w)], check=True, capture_output=True)
            git("checkout", "-q", "-b", "motor-contrato", cwd=w)
            (w / "contrato.txt").write_text("x\n")
            git("add", "-A", cwd=w)
            git("commit", "-q", "-m", "contrato", cwd=w)
            git("push", "-q", "-f", "origin", "motor-contrato", cwd=w)
            sha = git("rev-parse", "HEAD", cwd=w).stdout.strip()
            repo = f"https://github.com/{nombre}"
            pr = gh.abrir_pr(repo, "motor-contrato", "main", "contrato motor", "prueba de contrato")
            self.assertTrue(pr.borrador)
            gh.marcar_listo(repo, pr.numero)
            with self.assertRaises(MergeRechazado):
                gh.merge(repo, pr.numero, "0" * 40, "squash", "m")


if __name__ == "__main__":
    unittest.main()
