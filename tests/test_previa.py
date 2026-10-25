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


class MaquinaArregladaTest(unittest.TestCase):
    def test_maquina_arreglada_se_reverifica_sin_agotar(self):
        from motor import huella
        from motor import machine as m
        from motor.store import Event
        from tests.test_puro import PLAN, entregado, ev, pol

        sha = "a" * 40
        plan = __import__("motor.plan", fromlist=["parse_plan"]).parse_plan(PLAN)
        base = 1_700_000_000.0

        def evento(i, kind, block, data):
            return Event(i, base + i, "demo", block, kind, data)

        evs = entregado()
        evs = [
            evento(
                i + 1,
                e.kind,
                e.block,
                e.data,
            )
            for i, e in enumerate(evs)
        ]
        evs.append(
            evento(
                10,
                "Verified",
                "A",
                {
                    "sha": sha,
                    "ok": False,
                    "detalle": "cannot import name 'tomllib'",
                    "falta_comando": ["tomllib"],
                    "huella": huella.insumo_de("verified", plan.block("A"), {}),
                    "huella_maquina": "m" * 16,
                },
            )
        )
        for n in (2, 3):
            evs.append(
                evento(
                    10 + n * 2,
                    "AttemptStarted",
                    "A",
                    {
                        "n": n,
                        "role": "implementar",
                        "modelo": "glm",
                        "proc": {},
                        "dir": "d",
                        "sha": sha,
                    },
                )
            )
            evs.append(
                evento(
                    11 + n * 2,
                    "AttemptEnded",
                    "A",
                    {
                        "n": n,
                        "role": "implementar",
                        "outcome": {"kind": "sin_cambios", "detalle": "lo mismo"},
                    },
                )
            )
        snap = m.fold(plan, evs, "m" * 16)
        bv = snap.blocks["A"]
        sin = [a for a in bv.attempts if a.ended and a.ended.get("kind") == "sin_cambios"]
        self.assertEqual(len(sin), 2)
        self.assertTrue(all(not m._cuenta_falla(a, bv) for a in sin))
        self.assertNotEqual(
            m.bloqueo(snap, pol(), base + 100, bv, "implementar", "glm", pol().implementadores)[0],
            "agotado",
        )
        acciones = [a for a in m.decide(snap, pol(), base + 700) if not isinstance(a, m.Notify) and getattr(a, "block", "") == "A"]
        self.assertIn(m.Verify("A", sha), acciones)
        self.assertNotIn("StartAttempt", [type(a).__name__ for a in acciones])

        con_nota = evs + [evento(30, "Note", "A", {"texto": "máquina arreglada"})]
        snap_nota = m.fold(plan, con_nota, "m" * 16)
        acciones_nota = [
            a
            for a in m.decide(snap_nota, pol(), base + 40)
            if not isinstance(a, m.Notify) and getattr(a, "block", "") == "A"
        ]
        self.assertIn(m.Verify("A", sha), acciones_nota)

        arreglada = con_nota + [
            evento(
                31,
                "Verified",
                "A",
                {
                    "sha": sha,
                    "ok": True,
                    "detalle": "",
                    "falta_comando": [],
                    "huella": huella.insumo_de("verified", plan.block("A"), {}),
                    "huella_maquina": "m" * 16,
                },
            )
        ]
        snap_ok = m.fold(plan, arreglada, "m" * 16)
        acciones_ok = [
            a
            for a in m.decide(snap_ok, pol(), base + 50)
            if not isinstance(a, m.Notify) and getattr(a, "block", "") == "A"
        ]
        self.assertEqual(acciones_ok, [m.OpenPr("A", sha)])


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
