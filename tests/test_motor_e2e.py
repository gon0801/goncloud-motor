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


def motor(
    *a: str, env: dict | None = None, timeout: float = 60
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "motor", *a],
        cwd=RAIZ,
        env={**ENV, **(env or {})},
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def daemon(home: Path, env: dict | None = None) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "motor",
            "daemon",
            "--home",
            str(home),
            "--intervalo",
            "0.2",
            "--hasta-terminar",
            "--tope",
            "150",
            "--checks-cada",
            "0",
        ],
        cwd=RAIZ,
        env={**ENV, **(env or {})},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


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
        r = motor(
            "entrega",
            str(self.plan),
            "--home",
            str(self.home),
            "--json",
            "--espera",
            "0",
        )
        self.assertEqual(
            json.loads(r.stdout)["estado"], "encolado", r.stdout + r.stderr
        )
        if login:
            (self.home / "login-hecho").write_text("ok")
        return self.home

    def termina(self, p: subprocess.Popen | None = None, env: dict | None = None):
        p = p or daemon(self.home, env)
        self.assertEqual(p.wait(timeout=180), 0, "el motor no terminó el plan")

    def invariantes(self):
        evs = eventos(self.home)
        merged = [e for e in evs if e.kind == "Merged"]
        self.assertEqual(
            sorted(e.block for e in merged),
            ["A", "B", "C", "D"],
            "un merge por bloque, sin duplicados",
        )
        for mg in merged:
            antes = [e for e in evs if e.id < mg.id and e.block == mg.block]
            sha = mg.data["sha"]
            for kind, cond in (
                ("Verified", lambda d: d["ok"]),
                ("Reviewed", lambda d: True),
                ("MarkedReady", lambda d: True),
                ("ChecksResult", lambda d: d["green"]),
            ):
                self.assertTrue(
                    any(
                        e.kind == kind and e.data["sha"] == sha and cond(e.data)
                        for e in antes
                    ),
                    f"{mg.block}: merge sin {kind} sobre ese SHA",
                )
            autores = {
                e.data["modelo"]
                for e in antes
                if e.kind == "AttemptStarted" and e.data["role"] == "implementar"
            }
            revisor = [
                e.data["modelo"]
                for e in antes
                if e.kind == "Reviewed" and e.data["sha"] == sha
            ][-1]
            self.assertNotIn(
                revisor, autores, f"{mg.block}: lo revisó uno de sus autores"
            )
        self.assertEqual(
            len([e for e in evs if e.kind == "Deployed" and e.data["ok"]]), 1
        )
        self.assertEqual(
            (self.tmp / "b" / "prod" / "version.txt").read_text().strip(), "v2"
        )
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
        sueno = self.tmp / "sueno.log"
        with open(
            self.home / "maquina.toml", "a"
        ) as f:  # la MacBook: no dormirse mientras haya plan
            f.write(
                f'\n[no_dormir]\nencender = ["sh", "-c", "echo sin-dormir >> {sueno}"]\n'
                f'apagar = ["sh", "-c", "echo normal >> {sueno}"]\n'
            )
        self.termina()
        self.assertEqual(
            sueno.read_text().split(),
            ["sin-dormir", "normal"],
            "se enciende una vez y se apaga al terminar",
        )
        evs = self.invariantes()
        self.assertTrue(
            any(e.kind == "Cooldown" and e.data["cuenta"] == "anthropic" for e in evs)
        )
        revisores = {e.data["modelo"] for e in evs if e.kind == "Reviewed"}
        self.assertNotIn(
            "opus", revisores, "opus comparte cuenta con sonnet, que quedó sin cuota"
        )
        docs = self.tmp / "b" / "docs.git"
        self.assertEqual(
            git("show", "main:GUIA.md", cwd=docs).stdout.strip(), "Cómo usar la app"
        )

    def test_cliente_encola_con_el_motor_apagado_y_luego_lo_toma(self):
        self.arranca()
        cmd = json.loads(
            motor(
                "entrega",
                str(self.plan),
                "--home",
                str(self.home),
                "--json",
                "--espera",
                "0",
            ).stdout
        )["cmd_id"]
        self.assertEqual(
            json.loads(motor("recibo", cmd, "--home", str(self.home), "--json").stdout)[
                "estado"
            ],
            "encolado",
        )
        Daemon(self.home).tick()
        self.assertEqual(
            json.loads(motor("recibo", cmd, "--home", str(self.home), "--json").stdout)[
                "estado"
            ],
            "tomado",
        )
        est = json.loads(motor("estado", "--home", str(self.home), "--json").stdout)
        self.assertEqual(est[0]["plan"], "demo")

    def test_plan_invalido_se_rechaza_en_el_momento(self):
        self.home, self.plan = sandbox.create(self.tmp / "b")
        malo = self.tmp / "malo.md"
        malo.write_text(
            "# x\nautopiloto: motor\nplan: x\nrepo: /nada\n## Bloque A — a\nlane: gate\n| Fila | Contenido | Acepta |\n| A.1 | a | - |\n"
        )
        r = motor("entrega", str(malo), "--home", str(self.home), "--json")
        d = json.loads(r.stdout)
        self.assertEqual(d["estado"], "rechazado")
        self.assertTrue(any("Acepta" in x for x in d["errores"]))


class TestPedidoDeModelos(Banco):
    def test_el_plan_elige_quien_implementa_y_quien_revisa(self):
        """La tabla dice sonnet→glm para implementar y opus→k3 para revisar; el plan pide glm y k3."""
        self.home, self.plan = sandbox.create(self.tmp / "b")
        texto = self.plan.read_text().replace(
            "autopiloto: motor\n", "autopiloto: motor\nimplementa: GLM\nrevisa: k3\n", 1
        )
        self.plan.write_text(texto)
        conf = self.tmp / "b" / "conf"
        rutas = (
            (conf / "rutas.toml")
            .read_text()
            .replace(
                "[rutas.glm]\n",
                '[rutas.glm]\nenv = { FAKE_LLAVE = "${MOTOR_TELEGRAM_TOKEN}" }\n',
                1,
            )
        )
        (conf / "rutas.toml").write_text(rutas)
        git(
            "-c",
            "user.name=c",
            "-c",
            "user.email=c@l",
            "commit",
            "-qam",
            "env por ruta",
            cwd=conf,
        )
        malo = self.tmp / "malo.md"
        malo.write_text(texto.replace("revisa: k3", "revisa: deepseek"))
        r = motor(
            "entrega", str(malo), "--home", str(self.home), "--json", "--espera", "0"
        )
        self.assertIn("no conozco el modelo deepseek", r.stdout + r.stderr)
        r = motor(
            "entrega",
            str(self.plan),
            "--home",
            str(self.home),
            "--json",
            "--espera",
            "0",
        )
        self.assertEqual(
            json.loads(r.stdout)["estado"], "encolado", r.stdout + r.stderr
        )
        (self.home / "login-hecho").write_text("ok")
        self.termina()
        evs = eventos(self.home)
        usados = {
            (e.data["role"], e.data["modelo"])
            for e in evs
            if e.kind == "AttemptStarted"
        }
        self.assertEqual({mo for rol, mo in usados if rol == "implementar"}, {"glm"})
        self.assertEqual({mo for rol, mo in usados if rol == "revisar"}, {"k3"})
        self.assertEqual(
            (self.tmp / "b" / "fake-state" / "llave-glm").read_text(),
            "prueba",
            "la ruta recibe su llave desde secretos.env",
        )


class TestEntregaSegura(Banco):
    def test_repo_que_no_existe_rechaza_el_plan_sin_cargarlo(self):
        self.home, self.plan = sandbox.create(self.tmp / "b")
        malo = self.tmp / "malo.md"
        malo.write_text(
            self.plan.read_text().replace("repo: ", "repo: /no/existe/x.git\n#", 1)
        )
        r = motor(
            "entrega", str(malo), "--home", str(self.home), "--json", "--espera", "0"
        )
        cmd = json.loads(r.stdout)["cmd_id"]
        d = Daemon(self.home)
        d.ingest_inbox()
        self.assertEqual(
            d.plans(), [], "un plan con un repo inexistente no queda cargado"
        )
        recibo = [
            e
            for e in Store(self.home / "motor.db").events()
            if e.kind == "CommandReceived"
        ]
        self.assertEqual(recibo[0].data["cmd_id"], cmd)
        self.assertTrue(recibo[0].data["resultado"].startswith("rechazado"))

    def test_hecho_antes_de_tiempo_se_rechaza_y_un_efecto_que_revienta_no_tumba_al_motor(
        self,
    ):
        self.home, self.plan = sandbox.create(self.tmp / "b")
        self.plan.write_text(
            self.plan.read_text().replace(
                "lane: fast\nhumano:", "lane: fast\ndepende: A\nhumano:", 1
            )
        )
        motor(
            "entrega",
            str(self.plan),
            "--home",
            str(self.home),
            "--json",
            "--espera",
            "0",
        )
        motor("hecho", "demo", "E", "--home", str(self.home))
        d = Daemon(self.home)

        def revienta(*_):
            raise RuntimeError("se cayó la red")

        d.fx.start_attempt = revienta
        d.tick()
        evs = eventos(self.home)
        hecho = [
            e
            for e in Store(self.home / "motor.db").events()
            if e.kind == "CommandReceived" and e.data["tipo"] == "hecho"
        ]
        self.assertTrue(
            hecho[0]
            .data["resultado"]
            .startswith("rechazado: ese paso todavía no te toca")
        )
        self.assertFalse(any(e.kind == "HumanDone" for e in evs))
        self.assertTrue(
            any(
                e.kind == "EffectFailed" and "se cayó la red" in e.data["error"]
                for e in evs
            )
        )


class TestSalidaDeWindows(Banco):
    def test_salida_no_utf8_y_efecto_que_falla_no_se_repite_en_cada_tick(self):
        """MOTOR 01, 2026-10-20: la prueba por ssh a la PC devolvió una ñ en cp1252 (0xf1) y la
        verificación reventaba con UnicodeDecodeError en cada tick, corriendo la prueba otra vez."""
        from motor.effects import _run

        r = _run("printf 'a\\361o\\n'", None, dict(os.environ))
        self.assertEqual((r.returncode, r.stdout), (0, "a\ufffdo\n"))
        self.home, self.plan = sandbox.create(self.tmp / "b")
        motor(
            "entrega",
            str(self.plan),
            "--home",
            str(self.home),
            "--json",
            "--espera",
            "0",
        )
        d = Daemon(self.home)
        llamadas = []

        def revienta(*_):
            llamadas.append(1)
            raise RuntimeError("se cayó la red")

        d.fx.start_attempt = revienta
        ahora = time.time()
        d.tick(ahora)
        primero = len(llamadas)
        d.tick(ahora + 10)
        self.assertEqual(
            len(llamadas), primero, "a los 10 s no se repite lo que acaba de fallar"
        )
        d.tick(ahora + 301)
        self.assertGreater(len(llamadas), primero, "a los 5 min sí se reintenta")


class TestSoloElMotorMergea(Banco):
    """MOTOR 01, 2026-10-20: GitHub rechazó el merge del PR #484 (pruebas pendientes), el motor se lo
    devolvió al agente como tarea y el agente mergeó él mismo con `gh pr merge`."""

    def test_el_gh_de_los_agentes_no_mergea(self):
        falso = self.tmp / "bin"
        falso.mkdir()
        (falso / "gh").write_text('#!/bin/sh\necho real "$@"\n')
        (falso / "gh").chmod(0o755)
        shim = RAIZ / "motor" / "shims" / "gh"
        env = {
            **os.environ,
            "PATH": f"{shim.parent}{os.pathsep}{falso}{os.pathsep}/usr/bin:/bin",
        }
        for prohibido in (
            ["pr", "merge", "484", "--squash"],
            ["pr", "close", "3"],
            ["pr", "ready", "3"],
            ["api", "-X", "PUT", "repos/x/y/pulls/3/merge"],
        ):
            r = subprocess.run(
                [str(shim), *prohibido], env=env, capture_output=True, text=True
            )
            self.assertEqual(r.returncode, 3, prohibido)
            self.assertIn("solo el motor mergea", r.stderr)
        r = subprocess.run(
            [str(shim), "pr", "view", "3"], env=env, capture_output=True, text=True
        )
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "real pr view 3"))

    def _daemon_con_pr(self):
        from motor.github import PrView

        self.home, self.plan = sandbox.create(self.tmp / "b")
        motor(
            "entrega",
            str(self.plan),
            "--home",
            str(self.home),
            "--json",
            "--espera",
            "0",
        )
        d = Daemon(self.home)
        d.ingest_inbox()
        plan = d.plans()[0]
        d.store.append("demo", "A", "PrOpened", {"numero": 7, "rama": "x"})
        snap = m.fold(plan, d.store.events("demo"))

        class GhFalso:
            estado, motivo = "abierto", ""

            def ver(self, repo, numero):
                return PrView(
                    numero, "x", "main", False, "a" * 40, self.estado, "c" * 40
                )

            def merge(self, *a):
                from motor.github import MergeRechazado

                raise MergeRechazado(self.motivo)

        d.fx.cx.gh = GhFalso()
        return d, plan, snap

    def test_rechazo_de_github_no_es_tarea_del_agente(self):
        d, plan, snap = self._daemon_con_pr()
        d.fx.cx.gh.motivo = "Pull request #7 is not mergeable: the base branch policy prohibits the merge."
        with self.assertRaises(RuntimeError):
            d.fx.merge(plan, snap, m.Merge(m.MergeTicket("A", "a" * 40, 7)))
        self.assertFalse(
            [e for e in d.store.events("demo") if e.kind == "ChecksResult"],
            "no se marca rojo ni se manda al agente",
        )
        d.fx.cx.gh.motivo = "the merge commit cannot be cleanly created"
        d.fx.merge(plan, snap, m.Merge(m.MergeTicket("A", "a" * 40, 7)))
        rojo = [e for e in d.store.events("demo") if e.kind == "ChecksResult"]
        self.assertIn("no mergees tú", rojo[0].data["detalle"])

    def test_head_modificado_con_la_misma_punta_se_reintenta(self):
        """MOTOR 01, PR #9: GitHub dijo "Head branch was modified" justo después de abrir el PR, con la
        misma punta; no es tarea del agente."""
        d, plan, snap = self._daemon_con_pr()
        d.fx.cx.gh.motivo = (
            "GraphQL: Head branch was modified. Review and try the merge again."
        )
        with self.assertRaises(RuntimeError):
            d.fx.merge(plan, snap, m.Merge(m.MergeTicket("A", "a" * 40, 7)))
        self.assertFalse(
            [e for e in d.store.events("demo") if e.kind == "ChecksResult"]
        )
        d.fx.merge(
            plan, snap, m.Merge(m.MergeTicket("A", "b" * 40, 7))
        )  # la punta sí se movió
        self.assertTrue(
            [e for e in d.store.events("demo") if e.kind == "ChecksResult"],
            "eso sí vuelve al agente",
        )

    def test_merge_hecho_por_fuera_se_adopta(self):
        d, plan, snap = self._daemon_con_pr()
        d.fx.cx.gh.estado = "mergeado"
        d.fx.poll_checks(plan, snap, m.PollChecks("A", "a" * 40))
        mg = [e for e in d.store.events("demo") if e.kind == "Merged"]
        self.assertEqual((mg[0].data["sha"], mg[0].data["externo"]), ("a" * 40, True))

    def test_pr_mergeado_con_otro_contenido_no_se_adopta(self):
        d, plan, snap = self._daemon_con_pr()
        d.fx.cx.gh.estado = "mergeado"
        d.fx.poll_checks(plan, snap, m.PollChecks("A", "b" * 40))
        evs = d.store.events("demo")
        self.assertFalse(
            [e for e in evs if e.kind == "Merged"],
            "no se despliega lo que se mergeó antes",
        )
        self.assertEqual([e.data["numero"] for e in evs if e.kind == "PrCerrado"], [7])
        self.assertIsNone(
            m.fold(plan, evs).blocks["A"].pr, "el siguiente paso abre un PR nuevo"
        )

    def test_el_encargo_prohibe_mergear(self):
        self.assertIn("PROHIBIDO mergear", (RAIZ / "motor" / "effects.py").read_text())


class TestConfSeActualizaSola(Banco):
    """David (2026-10-20): agregar, quitar o reemplazar modelos tiene que ser fácil. Es un cambio
    en el repo de la conf; cada máquina lo trae sola y solo aplica una conf válida."""

    def test_modelo_nuevo_llega_y_una_conf_rota_no(self):
        self.home, _ = sandbox.create(self.tmp / "b")
        conf, bare, w = (
            self.tmp / "b" / "conf",
            self.tmp / "conf.git",
            self.tmp / "conf-w",
        )
        git("clone", "-q", "--bare", str(conf), str(bare))
        git("remote", "add", "origin", str(bare), cwd=conf)
        git("fetch", "-q", "origin", cwd=conf)
        git("clone", "-q", str(bare), str(w))
        d = Daemon(self.home)
        self.assertNotIn("qwen", d.cx.conf.escalera("implementar"))

        modelos = (
            (w / "modelos.toml")
            .read_text()
            .replace('codigo = ["', 'codigo = ["qwen", "', 1)
        )
        (w / "modelos.toml").write_text(modelos)
        with open(w / "rutas.toml", "a") as f:
            f.write(
                '\n[rutas.qwen]\ncuenta = "qwen"\ncli = "qwen"\nargv = ["qwen", "{prompt}"]\n'
            )
        git(
            "-c", "user.name=a", "-c", "user.email=a@l", "commit", "-qam", "qwen", cwd=w
        )
        git("push", "-q", "origin", "HEAD", cwd=w)
        ahora = time.time()
        d.tick(ahora)
        self.assertEqual(
            d.cx.conf.escalera("implementar")[0], "qwen", "el modelo nuevo llegó solo"
        )

        with open(w / "rutas.toml", "a") as f:
            f.write('\n[rutas.roto]\ncuenta = "x"\ncli = "x"\nargv = ["x"]\n')
        git(
            "-c", "user.name=a", "-c", "user.email=a@l", "commit", "-qam", "roto", cwd=w
        )
        git("push", "-q", "origin", "HEAD", cwd=w)
        sha_bueno = d.cx.conf.sha
        d.tick(ahora + 10)
        d.tick(ahora + 901)
        self.assertEqual(
            d.cx.conf.sha, sha_bueno, "una conf que no pasa la validación no se aplica"
        )
        self.assertNotIn("roto", d.cx.conf.rutas)
        evs = Store(self.home / "motor.db").events()
        self.assertEqual(len([e for e in evs if e.kind == "ConfRechazada"]), 1)
        self.assertTrue(
            list((self.home / "avisos" / "pendientes").glob("*confmala*")),
            "se avisa una vez",
        )
        d.tick(ahora + 1900)
        self.assertEqual(
            len(
                [
                    e
                    for e in Store(self.home / "motor.db").events()
                    if e.kind == "ConfRechazada"
                ]
            ),
            1,
        )


class TestArregloTrasDespliegueFallido(Banco):
    def test_el_arreglo_va_en_un_pr_nuevo_y_se_despliega_lo_arreglado(self):
        """MOTOR 01, bloque F: el despliegue falló tras el merge, el arreglo quedó en la rama y el motor
        siguió usando el PR ya mergeado; desplegaba otra vez el código viejo, en ciclo, casi 3 horas."""
        self.arranca(
            codigo=("glm", "grok"),
            modos={"glm": "despliegue_roto_primero", "grok": "ok"},
        )
        self.termina()
        evs = eventos(self.home)
        prs = [e.data["numero"] for e in evs if e.kind == "PrOpened" and e.block == "D"]
        merges = [e for e in evs if e.kind == "Merged" and e.block == "D"]
        despliegues = [
            e.data["ok"] for e in evs if e.kind == "Deployed" and e.block == "D"
        ]
        self.assertEqual(len(set(prs)), 2, "el arreglo va en un PR nuevo")
        self.assertEqual(
            len({mg.data["commit"] for mg in merges}), 2, "y se mergea de verdad"
        )
        self.assertEqual(
            despliegues, [False, True], "el segundo despliegue ya lleva el arreglo"
        )
        self.assertFalse(
            [e for e in evs if e.kind == "Merged" and e.data.get("externo")]
        )


class TestSondaLenta(Banco):
    def test_una_sonda_de_varios_minutos_no_traba_el_motor_y_si_cuenta(self):
        """MOTOR 01, bloque E: la sonda revisa tres máquinas y tarda minutos; con tope de 60 s
        nunca pasaba. Ahora corre aparte: el tick no la espera y, al terminar bien, cuenta."""
        import motor.effects as ef

        self.home, self.plan = sandbox.create(self.tmp / "b")
        self.plan.write_text(
            self.plan.read_text().replace(
                'sonda-humano: test -f "$MOTOR_HOME/login-hecho"',
                'sonda-humano: sleep 8 && test -f "$MOTOR_HOME/login-hecho"',
            )
        )
        motor(
            "entrega",
            str(self.plan),
            "--home",
            str(self.home),
            "--json",
            "--espera",
            "0",
        )
        (self.home / "login-hecho").write_text("ok")
        d = Daemon(self.home)
        d.fx.start_attempt = lambda *_: None
        viejo, ef.SONDA_CADA_S = ef.SONDA_CADA_S, 0
        try:
            inicio = time.time()
            d.tick()
            self.assertLess(time.time() - inicio, 7, "el tick no espera la sonda (8 s)")
            self.assertTrue(
                esperar(
                    lambda: (
                        (d.tick() or True)
                        and any(e.kind == "HumanDone" for e in eventos(self.home))
                    ),
                    tope=40,
                ),
                "la sonda lenta sí cuenta",
            )
        finally:
            ef.SONDA_CADA_S = viejo


class TestEnvDeLaMaquina(Banco):
    def test_el_motor_clona_con_el_alias_de_la_maquina(self):
        """MOTOR 01: la Mini rechazó el plan canario porque su llave por omisión no entra a GitHub;
        con GIT_CONFIG_* en maquina.toml el motor reescribe la dirección al alias que sí entra."""
        self.home, self.plan = sandbox.create(self.tmp / "b")
        app = str(self.tmp / "b" / "app.git")
        with open(self.home / "maquina.toml", "a", encoding="utf-8") as f:
            f.write(
                f'GIT_CONFIG_COUNT = "1"\nGIT_CONFIG_KEY_0 = "url.{app}.insteadOf"\n'
                'GIT_CONFIG_VALUE_0 = "alias-falso:app"\n'
            )
        self.plan.write_text(
            self.plan.read_text(encoding="utf-8").replace(
                f"repo: {app}", "repo: alias-falso:app", 1
            ),
            encoding="utf-8",
        )
        motor(
            "entrega",
            str(self.plan),
            "--home",
            str(self.home),
            "--json",
            "--espera",
            "0",
        )
        viejo = {
            k: os.environ.get(k)
            for k in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0")
        }
        try:
            d = Daemon(self.home)
            d.ingest_inbox()
            self.assertEqual(
                [p.id for p in d.plans()],
                ["demo"],
                "el plan se tomó: el clon usó el alias",
            )
        finally:
            for k, v in viejo.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


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
        self.assertTrue(
            [
                e
                for e in evs
                if e.kind == "AttemptEnded" and e.data["outcome"]["kind"] == "mintio"
            ]
        )
        falso = "0123456789abcdef0123456789abcdef01234567"
        self.assertFalse(
            [e for e in evs if e.data.get("sha") == falso and e.kind != "AttemptEnded"]
        )

    def test_agente_colgado_se_detiene_y_releva(self):
        self.arranca(modos={"sonnet": "cuelga", "glm": "ok"}, sin_avance=2)
        self.termina()
        evs = self.invariantes()
        self.assertTrue(
            [
                e
                for e in evs
                if e.kind == "AttemptEnded"
                and e.data["outcome"]["kind"] == "sin_avance"
            ]
        )

    def test_pruebas_en_rojo_se_arreglan_solas(self):
        self.arranca(modos={"sonnet": "rompe_primero", "glm": "ok"})
        self.termina()
        evs = self.invariantes()
        self.assertTrue(
            [e for e in evs if e.kind == "ChecksResult" and not e.data["green"]]
        )

    def test_sin_salida_avisa_una_vez_y_no_espera(self):
        self.arranca(modos={"sonnet": "mentiroso", "glm": "mentiroso"})
        p = daemon(self.home)
        ok = esperar(
            lambda: any(
                e.kind == "NoticeQueued" and e.data["key"] == "atorado:A"
                for e in eventos(self.home)
            ),
            60,
        )
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()
        self.assertTrue(ok)


class TestApagon(Banco):
    def test_corte_de_luz_a_la_mitad_retoma_sin_perder_trabajo(self):
        self._apagon(privado=True)

    def test_repo_publico_la_foto_no_sale_de_la_maquina(self):
        app = self._apagon(privado=False)
        self.assertEqual(
            git("ls-remote", str(app), "refs/heads/motor-wip/*").stdout,
            "",
            "en un repo público lo de a medias nunca se publica",
        )

    def _apagon(self, privado: bool) -> Path:
        self.arranca(
            codigo=("glm", "grok"),
            modos={"glm": "parcial_lento", "grok": "ok"},
            wip_cada=1,
            app_privado=privado,
        )
        app = self.tmp / "b" / "app.git"

        def hay_foto() -> bool:
            if privado:
                return (
                    git(
                        "ls-remote", str(app), "refs/heads/motor-wip/mac-prueba/demo/A"
                    ).stdout.strip()
                    != ""
                )
            espejos = list((self.home / "repos").glob("app-*"))
            return (
                bool(espejos)
                and git(
                    "rev-parse",
                    "-q",
                    "--verify",
                    "refs/motor-wip/mac-prueba/demo/A",
                    cwd=espejos[0],
                ).returncode
                == 0
            )

        p = daemon(self.home, {"MOTOR_BOOT_ID": "arranque-1"})
        self.assertTrue(esperar(hay_foto), "no llegó la foto")
        # "se va la luz": muere el motor, el supervisor del agente y el agente
        inicio = [
            e
            for e in eventos(self.home)
            if e.kind == "AttemptStarted" and e.block == "A"
        ][0]
        d = Path(inicio.data["dir"])
        os.killpg(p.pid, signal.SIGKILL)
        for pid in (inicio.data["proc"]["pid"], int((d / "agent.pid").read_text())):
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        p.wait()
        self.termina(env={"MOTOR_BOOT_ID": "arranque-2"})  # "vuelve la luz"
        evs = self.invariantes()
        cortes = [
            e
            for e in evs
            if e.kind == "AttemptEnded" and e.data["outcome"]["kind"] == "corte"
        ]
        self.assertIn(
            "A", [e.block for e in cortes]
        )  # C puede ir corriendo en paralelo y cortarse también
        segundo = [
            e
            for e in evs
            if e.kind == "AttemptStarted" and e.block == "A" and e.data["n"] == 2
        ][0]
        self.assertEqual(
            segundo.data["modelo"],
            "glm",
            "un corte no es culpa del agente: no se le releva",
        )
        self.assertEqual(
            git("show", "main:parcial.txt", cwd=app).stdout.strip(),
            "trabajo a medias que no debe perderse",
        )
        self.assertTrue(
            any(
                e.kind == "NoticeQueued" and e.data["key"].startswith("retome:")
                for e in evs
            )
        )
        return app


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
            '| A.4 | escribe: .motor.toml=[repo]\\nprincipal = "main"\\n | true |',
        )
        self.plan.write_text(texto)
        motor(
            "entrega",
            str(self.plan),
            "--home",
            str(self.home),
            "--json",
            "--espera",
            "0",
        )
        (self.home / "login-hecho").write_text("ok")
        p = daemon(self.home)
        ok = esperar(
            lambda: any(
                e.kind == "ChecksResult" and e.block == "A" for e in eventos(self.home)
            ),
            90,
        )
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()
        self.assertTrue(ok)
        primero = [
            e for e in eventos(self.home) if e.kind == "ChecksResult" and e.block == "A"
        ][0]
        self.assertFalse(
            primero.data["green"],
            "el check 'seguridad' de la rama base debe seguir exigiéndose",
        )
        self.assertIn("seguridad", primero.data["detalle"])


class TestAvisos(Banco):
    def test_los_avisos_llegan_por_un_solo_canal_con_la_maquina(self):
        recibidos: list[str] = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers["Content-Length"])
                recibidos.append(
                    urllib.parse.parse_qs(self.rfile.read(n).decode())["text"][0]
                )
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
        self.assertTrue(
            all(t.startswith("En la Mac (prueba) · Demo de saludo:") for t in recibidos)
        )
        self.assertTrue(any("Terminé el plan" in t for t in recibidos))
        self.assertFalse(
            any("Necesito tu mano" in t for t in recibidos),
            "la sonda vio el paso hecho: no se molesta",
        )


class TestFotoWip(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="wip-"))
        self.bare, self.w = self.t / "o.git", self.t / "w"
        git("init", "-q", "--bare", "-b", "main", str(self.bare))
        git("clone", "-q", str(self.bare), str(self.w))
        (self.w / "a.txt").write_text("a\n")
        git("add", "-A", cwd=self.w)
        git(
            "-c",
            "user.name=a",
            "-c",
            "user.email=a@l",
            "commit",
            "-q",
            "-m",
            "a",
            cwd=self.w,
        )
        git("push", "-q", "origin", "HEAD:main", cwd=self.w)
        hook = self.w / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)

    def test_foto_sin_tocar_indice_ni_rama_y_sin_hooks(self):
        head = git("rev-parse", "HEAD", cwd=self.w).stdout.strip()
        (self.w / "b.txt").write_text("a medias\n")
        git("add", "a.txt", cwd=self.w)
        self.assertEqual(
            foto_wip(self.w, "motor-wip/x"), "subida", "el hook que falla no la detiene"
        )
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.w).stdout.strip(), head)
        self.assertIn("?? b.txt", git("status", "--short", cwd=self.w).stdout)
        foto = git(
            "ls-remote", str(self.bare), "refs/heads/motor-wip/x"
        ).stdout.split()[0]
        self.assertEqual(
            git("show", f"{foto}:b.txt", cwd=self.bare).stdout, "a medias\n"
        )
        self.assertEqual(foto_wip(self.w, "motor-wip/x"), "igual")

    def test_foto_local_no_sale_de_la_maquina(self):
        (self.w / "b.txt").write_text("a medias\n")
        self.assertEqual(foto_wip(self.w, "motor-wip/x", local=True), "guardada")
        self.assertEqual(
            git("ls-remote", str(self.bare), "refs/heads/motor-wip/*").stdout, ""
        )
        self.assertEqual(
            git("show", "refs/motor-wip/x:b.txt", cwd=self.w).stdout, "a medias\n"
        )
        self.assertEqual(foto_wip(self.w, "motor-wip/x", local=True), "igual")

    def test_no_sube_secretos(self):
        (self.w / "conf.env").write_text(
            "api_key = sk-abcdefghijklmnopqrstuvwxyz123456\n"
        )
        self.assertEqual(foto_wip(self.w, "motor-wip/x"), "secreto")
        self.assertEqual(
            git("ls-remote", str(self.bare), "refs/heads/motor-wip/x").stdout, ""
        )


if __name__ == "__main__":
    unittest.main()
