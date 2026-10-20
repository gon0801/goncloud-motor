"""Corridas reales del daemon sobre bancos desechables. Cada prueba es una falla del catálogo."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "proto"))
import sandbox  # noqa: E402

from taller import machine as m  # noqa: E402
from taller.daemon import Daemon  # noqa: E402
from taller.notify import lint_plain  # noqa: E402
from taller.store import Store  # noqa: E402

PLAN = RAIZ / "proto" / "plan_demo.md"
ENV = {**os.environ, "PYTHONPATH": str(RAIZ)}


def taller(*a: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "taller", *a], cwd=RAIZ, env=ENV, capture_output=True,
                          text=True, timeout=timeout)


def daemon(home: Path) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "taller", "daemon", "--home", str(home), "--intervalo", "0.2",
                             "--hasta-terminar", "--tope", "120"], cwd=RAIZ, env=ENV,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def esperar(cond, tope=90.0):
    fin = time.time() + tope
    while time.time() < fin:
        if cond():
            return True
        time.sleep(0.2)
    return False


def eventos(home: Path):
    return Store(home / "taller.db").events("demo")


class Banco(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="taller-e2e-"))

    def arranca(self, **kw) -> Path:
        home = sandbox.create(self.tmp / "b", **kw)
        r = taller("arranca", str(PLAN), "--home", str(home))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (home / "login-hecho").write_text("ok")  # el paso humano ya está hecho: la sonda lo detecta
        return home

    def termina(self, home: Path, p: subprocess.Popen | None = None):
        p = p or daemon(home)
        self.assertEqual(p.wait(timeout=150), 0, "el daemon no terminó el plan")

    def comprueba_invariantes(self, home: Path):
        evs = eventos(home)
        merged = [e for e in evs if e.kind == "Merged"]
        self.assertEqual(sorted(e.block for e in merged), ["A", "B", "D"], "un merge por bloque, sin duplicados")
        for mg in merged:
            antes = [e for e in evs if e.id < mg.id and e.block == mg.block]
            sha = mg.data["sha"]
            self.assertTrue(any(e.kind == "Reviewed" and e.data["sha"] == sha for e in antes),
                            f"{mg.block}: merge sin revisión de ese SHA")
            self.assertTrue(any(e.kind == "CiResult" and e.data["sha"] == sha and e.data["green"] for e in antes),
                            f"{mg.block}: merge sin CI verde de ese SHA")
        self.assertEqual(len([e for e in evs if e.kind == "Deployed" and e.data["ok"]]), 1)
        self.assertEqual((self.tmp / "b" / "prod" / "version.txt").read_text().strip(), "v2")
        for e in evs:
            if e.kind == "NoticeQueued":
                lint_plain(e.data["texto"])
        d = Daemon(home)
        snap = m.fold(d.plans()[0], evs)
        self.assertTrue(all(m.estado(snap, b) is m.Estado.HECHO for b in snap.blocks))
        return evs


class TestCatalogo(Banco):
    def test_cuota_a_media_noche_releva_y_termina(self):
        home = self.arranca()
        self.termina(home)
        evs = self.comprueba_invariantes(home)
        cuota = [e for e in evs if e.kind == "AttemptEnded" and e.data["outcome"]["kind"] == "cuota"]
        self.assertTrue(cuota, "muse debía chocar con su cuota")
        self.assertTrue(any(e.kind == "Cooldown" and e.data["provider"] == "muse" for e in evs))
        despues = [e for e in evs if e.kind == "AttemptStarted" and e.id > cuota[0].id and e.block == cuota[0].block]
        self.assertEqual(despues[0].data["provider"], "glm", "el relevo es inmediato, sin pausa")
        self.assertFalse([e for e in evs if e.kind == "NoticeQueued" and "lento" in e.data["key"]],
                         "con un relevo disponible no se molesta a David")

    def test_stdin_no_cuelga(self):
        home = self.arranca(modos={"muse": "stdin", "glm": "stdin"})
        self.termina(home)
        evs = self.comprueba_invariantes(home)
        for e in evs:
            if e.kind == "AttemptStarted" and e.data["role"] == "implementar":
                o = json.loads((Path(e.data["dir"]) / "outcome.json").read_text())
                self.assertLess(o["segundos"], 5, "leer de la terminal no puede colgar al agente")

    def test_agente_que_miente_nunca_pasa_la_verificacion(self):
        home = self.arranca(implementadores=("kimi", "glm"), modos={"kimi": "mentiroso", "glm": "ok"})
        self.termina(home)
        evs = self.comprueba_invariantes(home)
        mentiras = [e for e in evs if e.kind == "AttemptEnded" and e.data["outcome"]["kind"] == "mintio"]
        self.assertTrue(mentiras)
        falso = "0123456789abcdef0123456789abcdef01234567"
        self.assertFalse([e for e in evs if e.kind in ("Verified", "Reviewed", "Merged") and e.data["sha"] == falso])

    def test_agente_colgado_se_detiene_y_releva(self):
        home = self.arranca(implementadores=("muse", "glm"), modos={"muse": "cuelga", "glm": "ok"}, sin_avance=2)
        self.termina(home)
        evs = self.comprueba_invariantes(home)
        self.assertTrue([e for e in evs if e.kind == "AttemptEnded" and e.data["outcome"]["kind"] == "sin_avance"])

    def test_ci_en_rojo_se_arregla_sola(self):
        home = self.arranca(implementadores=("glm", "kimi"), modos={"glm": "rompe_primero", "kimi": "ok"})
        self.termina(home)
        evs = self.comprueba_invariantes(home)
        self.assertTrue([e for e in evs if e.kind == "CiResult" and not e.data["green"]])

    def test_matar_el_daemon_a_la_mitad_no_duplica_nada(self):
        home = self.arranca()
        p = daemon(home)
        self.assertTrue(esperar(lambda: any(e.kind == "AttemptStarted" and e.block == "B" for e in eventos(home))))
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()
        self.termina(home)
        self.comprueba_invariantes(home)
        claves = [(e.block, e.data["role"], e.data["n"]) for e in eventos(home) if e.kind == "AttemptStarted"]
        self.assertEqual(len(claves), len(set(claves)), "ningún intento se lanzó dos veces")

    def test_sin_salida_avisa_que_se_atoro_y_no_espera(self):
        home = sandbox.create(self.tmp / "b", implementadores=("glm", "kimi"),
                              modos={"glm": "mentiroso", "kimi": "mentiroso"})
        taller("arranca", str(PLAN), "--home", str(home))
        p = daemon(home)
        ok = esperar(lambda: any(e.kind == "NoticeQueued" and e.data["key"] == "atorado:A" for e in eventos(home)), 60)
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()
        self.assertTrue(ok, "debía avisar que el bloque A se atoró")
        texto = [e.data["texto"] for e in eventos(home) if e.kind == "NoticeQueued" and e.data["key"] == "atorado:A"][0]
        self.assertIn("Claude la va a revisar", texto)


if __name__ == "__main__":
    unittest.main()
