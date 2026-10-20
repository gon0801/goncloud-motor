"""Corridas reales del motor sobre bancos desechables. Cada prueba es una falla o un requisito."""

from __future__ import annotations

import http.server
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "proto"))
import sandbox  # noqa: E402

from motor import machine as m  # noqa: E402
from motor.daemon import Daemon  # noqa: E402
from motor.notify import lint_plain  # noqa: E402
from motor.store import Store  # noqa: E402
from motor.workspace import foto_wip, git  # noqa: E402

ENV = {**os.environ, "PYTHONPATH": str(RAIZ)}


def motor(*a: str, env: dict | None = None, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "motor", *a], cwd=RAIZ, env={**ENV, **(env or {})},
                          capture_output=True, text=True, timeout=timeout)


def daemon(home: Path, env: dict | None = None) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "motor", "daemon", "--home", str(home), "--intervalo", "0.2",
                             "--hasta-terminar", "--tope", "150", "--checks-cada", "0"], cwd=RAIZ,
                            env={**ENV, **(env or {})}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)


def esperar(cond, tope=90.0) -> bool:
    fin = time.time() + tope
    while time.time() < fin:
        if cond():
            return True
        time.sleep(0.2)
    return False


def eventos(home: Path):
    return Store(home / "motor.db").events("demo")


class Banco(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="motor-e2e-"))

    def arranca(self, login=True, **kw) -> Path:
        self.home, self.plan = sandbox.create(self.tmp / "b", **kw)
        r = motor("entrega", str(self.plan), "--home", str(self.home), "--json", "--espera", "0")
        self.assertEqual(json.loads(r.stdout)["estado"], "encolado", r.stdout + r.stderr)
        if login:
            (self.home / "login-hecho").write_text("ok")
        return self.home

    def termina(self, p: subprocess.Popen | None = None, env: dict | None = None):
        p = p or daemon(self.home, env)
        self.assertEqual(p.wait(timeout=180), 0, "el motor no terminó el plan")

    def invariantes(self):
        evs = eventos(self.home)
        merged = [e for e in evs if e.kind == "Merged"]
        self.assertEqual(sorted(e.block for e in merged), ["A", "B", "C", "D"], "un merge por bloque, sin duplicados")
        for mg in merged:
            antes = [e for e in evs if e.id < mg.id and e.block == mg.block]
            sha = mg.data["sha"]
            for kind, cond in (("Verified", lambda d: d["ok"]), ("Reviewed", lambda d: True),
                               ("MarkedReady", lambda d: True), ("ChecksResult", lambda d: d["green"])):
                self.assertTrue(any(e.kind == kind and e.data["sha"] == sha and cond(e.data) for e in antes),
                                f"{mg.block}: merge sin {kind} sobre ese SHA")
            autores = {e.data["modelo"] for e in antes if e.kind == "AttemptStarted" and e.data["role"] == "implementar"}
            revisor = [e.data["modelo"] for e in antes if e.kind == "Reviewed" and e.data["sha"] == sha][-1]
            self.assertNotIn(revisor, autores, f"{mg.block}: lo revisó uno de sus autores")
        self.assertEqual(len([e for e in evs if e.kind == "Deployed" and e.data["ok"]]), 1)
        self.assertEqual((self.tmp / "b" / "prod" / "version.txt").read_text().strip(), "v2")
        gh = json.loads((self.tmp / "b" / "github" / "github.json").read_text())
        self.assertEqual(len(gh["prs"]), 4, "un PR por bloque de código")
        for e in evs:
            if e.kind == "NoticeQueued":
                lint_plain(e.data["texto"])
                self.assertTrue(e.data["texto"].startswith("En la Mac (prueba)"))
        snap = m.fold(Daemon(self.home).plans()[0], evs)
        self.assertTrue(all(m.estado(snap, b) is m.Estado.HECHO for b in snap.blocks))
        return evs


class TestPlan(Banco):
    def test_plan_completo_cuota_por_cuenta_y_dos_repos(self):
        self.arranca()
        self.termina()
        evs = self.invariantes()
        self.assertTrue(any(e.kind == "Cooldown" and e.data["cuenta"] == "anthropic" for e in evs))
        revisores = {e.data["modelo"] for e in evs if e.kind == "Reviewed"}
        self.assertNotIn("opus", revisores, "opus comparte cuenta con sonnet, que quedó sin cuota")
        docs = self.tmp / "b" / "docs.git"
        self.assertEqual(git("show", "main:GUIA.md", cwd=docs).stdout.strip(), "Cómo usar la app")

    def test_cliente_encola_con_el_motor_apagado_y_luego_lo_toma(self):
        self.arranca()
        cmd = json.loads(motor("entrega", str(self.plan), "--home", str(self.home), "--json", "--espera", "0").stdout)["cmd_id"]
        self.assertEqual(json.loads(motor("recibo", cmd, "--home", str(self.home), "--json").stdout)["estado"], "encolado")
        Daemon(self.home).tick()
        self.assertEqual(json.loads(motor("recibo", cmd, "--home", str(self.home), "--json").stdout)["estado"], "tomado")
        est = json.loads(motor("estado", "--home", str(self.home), "--json").stdout)
        self.assertEqual(est[0]["plan"], "demo")

    def test_plan_invalido_se_rechaza_en_el_momento(self):
        self.home, self.plan = sandbox.create(self.tmp / "b")
        malo = self.tmp / "malo.md"
        malo.write_text("# x\nautopiloto: motor\nplan: x\nrepo: /nada\n## Bloque A — a\nlane: gate\n| Fila | Contenido | Acepta |\n| A.1 | a | - |\n")
        r = motor("entrega", str(malo), "--home", str(self.home), "--json")
        d = json.loads(r.stdout)
        self.assertEqual(d["estado"], "rechazado")
        self.assertTrue(any("Acepta" in x for x in d["errores"]))


class TestFallas(Banco):
    def test_stdin_no_cuelga(self):
        self.arranca(modos={"sonnet": "stdin", "glm": "stdin"})
        self.termina()
        for e in self.invariantes():
            if e.kind == "AttemptStarted" and e.data["role"] == "implementar":
                o = json.loads((Path(e.data["dir"]) / "outcome.json").read_text())
                self.assertLess(o["segundos"], 10)

    def test_agente_que_miente_nunca_pasa(self):
        self.arranca(modos={"sonnet": "mentiroso", "glm": "ok"})
        self.termina()
        evs = self.invariantes()
        self.assertTrue([e for e in evs if e.kind == "AttemptEnded" and e.data["outcome"]["kind"] == "mintio"])
        falso = "0123456789abcdef0123456789abcdef01234567"
        self.assertFalse([e for e in evs if e.data.get("sha") == falso and e.kind != "AttemptEnded"])

    def test_agente_colgado_se_detiene_y_releva(self):
        self.arranca(modos={"sonnet": "cuelga", "glm": "ok"}, sin_avance=2)
        self.termina()
        evs = self.invariantes()
        self.assertTrue([e for e in evs if e.kind == "AttemptEnded" and e.data["outcome"]["kind"] == "sin_avance"])

    def test_pruebas_en_rojo_se_arreglan_solas(self):
        self.arranca(modos={"sonnet": "rompe_primero", "glm": "ok"})
        self.termina()
        evs = self.invariantes()
        self.assertTrue([e for e in evs if e.kind == "ChecksResult" and not e.data["green"]])

    def test_sin_salida_avisa_una_vez_y_no_espera(self):
        self.arranca(modos={"sonnet": "mentiroso", "glm": "mentiroso"})
        p = daemon(self.home)
        ok = esperar(lambda: any(e.kind == "NoticeQueued" and e.data["key"] == "atorado:A" for e in eventos(self.home)), 60)
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()
        self.assertTrue(ok)


class TestApagon(Banco):
    def test_corte_de_luz_a_la_mitad_retoma_sin_perder_trabajo(self):
        self.arranca(codigo=("glm", "grok"), modos={"glm": "parcial_lento", "grok": "ok"}, wip_cada=1)
        rama_wip = "refs/heads/motor-wip/mac-prueba/demo/A"
        app = self.tmp / "b" / "app.git"
        p = daemon(self.home, {"MOTOR_BOOT_ID": "arranque-1"})
        self.assertTrue(esperar(lambda: git("ls-remote", str(app), rama_wip).stdout.strip() != ""), "no llegó la foto")
        # "se va la luz": muere el motor, el supervisor del agente y el agente
        inicio = [e for e in eventos(self.home) if e.kind == "AttemptStarted" and e.block == "A"][0]
        d = Path(inicio.data["dir"])
        os.killpg(p.pid, signal.SIGKILL)
        for pid in (inicio.data["proc"]["pid"], int((d / "agent.pid").read_text())):
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        p.wait()
        self.termina(env={"MOTOR_BOOT_ID": "arranque-2"})   # "vuelve la luz"
        evs = self.invariantes()
        cortes = [e for e in evs if e.kind == "AttemptEnded" and e.data["outcome"]["kind"] == "corte"]
        self.assertEqual([e.block for e in cortes], ["A"])
        segundo = [e for e in evs if e.kind == "AttemptStarted" and e.block == "A" and e.data["n"] == 2][0]
        self.assertEqual(segundo.data["modelo"], "glm", "un corte no es culpa del agente: no se le releva")
        self.assertEqual(git("show", "main:parcial.txt", cwd=app).stdout.strip(),
                         "trabajo a medias que no debe perderse")
        self.assertTrue(any(e.kind == "NoticeQueued" and e.data["key"].startswith("retome:") for e in evs))


class TestReglasFijas(Banco):
    def test_un_agente_no_cambia_las_reglas_de_merge_de_su_rama(self):
        self.home, self.plan = sandbox.create(self.tmp / "b")
        # el repo exige el check "seguridad" (fijado de la rama base); el agente lo quita en su rama
        repos = self.tmp / "b" / "github" / "repos.json"
        cfg = json.loads(repos.read_text())
        app = str(self.tmp / "b" / "app.git")
        cfg[app]["checks"]["seguridad"] = "! test -f inseguro.txt"
        repos.write_text(json.dumps(cfg))
        texto = self.plan.read_text().replace(
            "| A.2 | escribe: despedida.txt=adios | test -f despedida.txt |",
            "| A.2 | escribe: despedida.txt=adios | test -f despedida.txt |\n"
            "| A.3 | escribe: inseguro.txt=1 | test -f inseguro.txt |\n"
            "| A.4 | escribe: .motor.toml=[repo]\\nprincipal = \"main\"\\n | true |")
        self.plan.write_text(texto)
        motor("entrega", str(self.plan), "--home", str(self.home), "--json", "--espera", "0")
        (self.home / "login-hecho").write_text("ok")
        p = daemon(self.home)
        ok = esperar(lambda: any(e.kind == "ChecksResult" and e.block == "A" for e in eventos(self.home)), 90)
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()
        self.assertTrue(ok)
        primero = [e for e in eventos(self.home) if e.kind == "ChecksResult" and e.block == "A"][0]
        self.assertFalse(primero.data["green"], "el check 'seguridad' de la rama base debe seguir exigiéndose")
        self.assertIn("seguridad", primero.data["detalle"])


class TestAvisos(Banco):
    def test_los_avisos_llegan_por_un_solo_canal_con_la_maquina(self):
        recibidos: list[str] = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers["Content-Length"])
                recibidos.append(urllib.parse.parse_qs(self.rfile.read(n).decode())["text"][0])
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            self.arranca(chat_url=f"http://127.0.0.1:{srv.server_port}")
            self.termina()
            self.invariantes()
        finally:
            srv.shutdown()
        self.assertTrue(recibidos)
        self.assertTrue(all(t.startswith("En la Mac (prueba) · Demo de saludo:") for t in recibidos))
        self.assertTrue(any("Terminé el plan" in t for t in recibidos))
        self.assertFalse(any("Necesito tu mano" in t for t in recibidos), "la sonda vio el paso hecho: no se molesta")


class TestFotoWip(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="wip-"))
        self.bare, self.w = self.t / "o.git", self.t / "w"
        git("init", "-q", "--bare", "-b", "main", str(self.bare))
        git("clone", "-q", str(self.bare), str(self.w))
        (self.w / "a.txt").write_text("a\n")
        git("add", "-A", cwd=self.w)
        git("-c", "user.name=a", "-c", "user.email=a@l", "commit", "-q", "-m", "a", cwd=self.w)
        git("push", "-q", "origin", "HEAD:main", cwd=self.w)
        hook = self.w / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)

    def test_foto_sin_tocar_indice_ni_rama_y_sin_hooks(self):
        head = git("rev-parse", "HEAD", cwd=self.w).stdout.strip()
        (self.w / "b.txt").write_text("a medias\n")
        git("add", "a.txt", cwd=self.w)
        self.assertEqual(foto_wip(self.w, "motor-wip/x"), "subida", "el hook que falla no la detiene")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.w).stdout.strip(), head)
        self.assertIn("?? b.txt", git("status", "--short", cwd=self.w).stdout)
        foto = git("ls-remote", str(self.bare), "refs/heads/motor-wip/x").stdout.split()[0]
        self.assertEqual(git("show", f"{foto}:b.txt", cwd=self.bare).stdout, "a medias\n")
        self.assertEqual(foto_wip(self.w, "motor-wip/x"), "igual")

    def test_no_sube_secretos(self):
        (self.w / "conf.env").write_text("api_key = sk-abcdefghijklmnopqrstuvwxyz123456\n")
        self.assertEqual(foto_wip(self.w, "motor-wip/x"), "secreto")
        self.assertEqual(git("ls-remote", str(self.bare), "refs/heads/motor-wip/x").stdout, "")


if __name__ == "__main__":
    unittest.main()
