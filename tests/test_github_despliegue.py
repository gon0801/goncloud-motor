"""MOTOR 02, bloque H: GitHub y el despliegue dicen la verdad."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from motor import effects as fxmod
from motor import machine as m
from motor.conf import Maquina
from motor.diagnostico import Causa, diagnostico
from motor.effects import RECETA, Effects
from motor.github import (
    Borrador,
    Conflicto,
    FakeGitHub,
    GhGitHub,
    PoliticaBloquea,
    PuntaMovida,
    veredicto,
)
from motor.plan import PlanError, parse_plan
from motor.store import Store
from motor.workspace import git
from tests.test_github_contrato import _bare_con_rama
from tests.test_puro import PLAN, pol

SHA = "a" * 40

PLAN_RELEASE = """# Demo
autopiloto: motor
plan: demo
repo: /r

## Bloque A — uno
lane: release
despliegue: desplegar=ops/d.sh checklist=ops/c.sh rollback=ops/r.sh
prueba-real: true
| Fila | Contenido | Acepta |
| --- | --- | --- |
| A.1 | algo | true |
"""


def _pr_json(borrador=False):
    return json.dumps(
        {
            "number": 7,
            "headRefName": "f",
            "baseRefName": "main",
            "isDraft": borrador,
            "headRefOid": SHA,
            "state": "OPEN",
            "mergeCommit": None,
        }
    )


class GhGrabado(GhGitHub):
    """`gh` falso con respuestas grabadas de GitHub, sin red."""

    def __init__(self, proteccion='["ci"]', reglas="[]", runs="[]", estados=None):
        super().__init__({})
        self.proteccion = proteccion
        self.reglas = reglas
        self.runs = runs
        self.estados = estados or {"state": "success", "statuses": []}
        self.llamadas = []
        self.relanzadas = []
        self.relanzadas_fallidas = []

    def _gh(self, *a):
        self.llamadas.append(a)
        if a[:2] == ("pr", "view"):
            return _pr_json()
        if a[0] == "run" and a[1] == "rerun":
            (self.relanzadas_fallidas if "--failed" in a else self.relanzadas).append(
                a[2]
            )
            return ""
        if a[0] == "run" and a[1] == "view":
            return "\n".join(f"linea-{i:03d}" for i in range(1, 101))
        if a[0] != "api":
            raise AssertionError(a)
        if a[1].endswith("/required_status_checks"):
            if isinstance(self.proteccion, Exception):
                raise self.proteccion
            return self.proteccion
        if "/rules/branches/" in a[1]:
            return self.reglas
        if "/check-runs" in a[1]:
            return self.runs
        if a[1].endswith("/status"):
            return json.dumps(self.estados)
        if "/events" in a[1]:
            return ""
        raise AssertionError(a)


def _run(nombre, conclusion="success", rid=1, status="completed"):
    job = rid + 100000
    return {
        "name": nombre,
        "status": status,
        "conclusion": conclusion,
        "completed_at": "2026-10-21T20:00:00Z",
        "id": job,
        "html_url": f"https://github.com/o/r/actions/runs/{rid}/job/{job}",
        "details_url": f"https://github.com/o/r/actions/runs/{rid}/job/{job}",
    }


class ChecksTest(unittest.TestCase):
    def test_proteccion_404_sin_requeridos_y_todo_verde(self):
        gh = GhGrabado(
            proteccion=RuntimeError("gh api: HTTP 404: Branch not protected"),
            runs=json.dumps([_run("ci")]),
            estados={
                "state": "success",
                "statuses": [{"context": "viejo", "state": "success"}],
            },
        )
        v = gh.checks("o/r", 7, SHA, ())
        self.assertTrue(v.green)
        self.assertTrue(v.terminal)

    def test_sin_requeridos_un_estado_pendiente_no_es_terminal(self):
        gh = GhGrabado(
            proteccion=RuntimeError("HTTP 404: Branch not protected"),
            runs=json.dumps([_run("ci")]),
            estados={
                "state": "pending",
                "statuses": [{"context": "viejo", "state": "pending"}],
            },
        )
        v = gh.checks("o/r", 7, SHA, ())
        self.assertFalse(v.terminal)
        self.assertFalse(v.green)

    def test_sin_requeridos_un_estado_en_rojo_es_falla(self):
        gh = GhGrabado(
            proteccion=RuntimeError("HTTP 404: Branch not protected"),
            runs=json.dumps([_run("ci")]),
            estados={
                "state": "failure",
                "statuses": [{"context": "viejo", "state": "failure"}],
            },
        )
        v = gh.checks("o/r", 7, SHA, ())
        self.assertTrue(v.terminal)
        self.assertEqual(v.fallas, ("viejo",))

    def test_proteccion_con_otro_error_es_falla_del_efecto(self):
        gh = GhGrabado(proteccion=RuntimeError("gh api: HTTP 500: boom"))
        with self.assertRaises(RuntimeError):
            gh.checks("o/r", 7, SHA, ())

    def test_proteccion_404_generico_es_falla_del_efecto(self):
        gh = GhGrabado(proteccion=RuntimeError("gh: Not Found (HTTP 404)"))
        with self.assertRaises(RuntimeError):
            gh.checks("o/r", 7, SHA, ())

    def test_sin_requeridos_salto_en_borrador_no_es_verde(self):
        salto = {
            "name": "ci",
            "status": "completed",
            "conclusion": "skipped",
            "completed_at": "2026-10-21T20:00:00Z",
            "id": 1,
            "html_url": "https://github.com/o/r/actions/runs/1",
            "details_url": "https://github.com/o/r/actions/runs/1",
        }
        v = veredicto(SHA, (), [salto], "2026-10-21T21:00:00Z")
        self.assertFalse(v.green)
        self.assertFalse(v.terminal)

    def test_las_reglas_de_la_rama_aportan_requeridos(self):
        reglas = json.dumps(
            [
                {
                    "type": "required_status_checks",
                    "parameters": {"required_status_checks": [{"context": "ci"}]},
                }
            ]
        )
        gh = GhGrabado(
            proteccion=RuntimeError("HTTP 404: Branch not protected"),
            reglas=reglas,
            runs=json.dumps([_run("ci")]),
        )
        self.assertTrue(gh.checks("o/r", 7, SHA, ()).green)
        gh2 = GhGrabado(
            proteccion=RuntimeError("HTTP 404: Branch not protected"),
            reglas=reglas,
            runs="[]",
        )
        self.assertEqual(gh2.checks("o/r", 7, SHA, ()).faltan, ("ci",))

    def test_check_runs_se_paginan(self):
        gh = GhGrabado(
            runs=json.dumps([_run("ci", rid=1)]) + json.dumps([_run("otra", rid=2)])
        )
        v = gh.checks("o/r", 7, SHA, ())
        self.assertEqual([c["nombre"] for c in v.corridas], ["ci", "otra"])
        self.assertTrue(
            any("--paginate" in str(a) and "/check-runs" in str(a) for a in gh.llamadas)
        )

    def test_run_id_sale_de_la_url_no_del_id_del_job(self):
        from motor.github import _run_id

        u = "https://github.com/cli/cli/actions/runs/39848362394/job/120067601052"
        self.assertEqual(
            _run_id({"id": 120067601052, "html_url": u, "details_url": u}),
            "39848362394",
        )
        gh = GhGrabado(runs=json.dumps([_run("ci", conclusion="failure", rid=7)]))
        v = gh.checks("o/r", 7, SHA, ())
        self.assertEqual(v.corridas_fallidas[0]["run_id"], "7")

    def test_veredicto_sin_requeridos_exige_que_todo_termine_y_pase(self):
        v = veredicto(SHA, (), [_run("ci", status="in_progress", conclusion=None)])
        self.assertFalse(v.terminal)
        v = veredicto(SHA, (), [_run("ci", conclusion="failure")])
        self.assertTrue(v.terminal)
        self.assertFalse(v.green)
        self.assertEqual(v.corridas_fallidas[0]["run_id"], "1")

    def _fx(self, gh):
        t = Path(tempfile.mkdtemp(prefix="h-checks-"))
        store = Store(t / "motor.db")
        plan = parse_plan(PLAN)
        fx = Effects(
            SimpleNamespace(store=store, gh=gh, checks_cada_s=0.0, policy=pol())
        )
        return plan, store, fx

    def _snap(self, plan, store, sha=SHA):
        snap = m.fold(plan, store.events(plan.id))
        snap.blocks["A"].pr = {"numero": 7}
        return snap

    def test_saltada_en_borrador_se_relanza_una_sola_vez_tras_30_min(self):
        from motor.github import ChecksVerdict, PrView

        gh = SimpleNamespace(
            ver=lambda repo, n: PrView(n, "f", "main", False, SHA, "abierto", ""),
            checks=lambda *a: ChecksVerdict(
                SHA,
                False,
                False,
                ("completa",),
                (),
                github_termino=True,
                corridas=(
                    {
                        "nombre": "completa",
                        "run_id": "42",
                        "url": "u",
                        "status": "completed",
                        "conclusion": "skipped",
                    },
                ),
            ),
            relanzadas=[],
        )
        gh.relanzar_corrida = lambda repo, rid: gh.relanzadas.append(rid)
        plan, store, fx = self._fx(gh)
        store.append(
            "demo",
            "A",
            "ChecksEsperando",
            {"sha": SHA, "faltan": ["completa"], "github_termino": True},
            ts=time.time() - 3600,
        )
        snap = self._snap(plan, store)
        fx.poll_checks(plan, snap, m.PollChecks("A", SHA))
        fx.poll_checks(plan, self._snap(plan, store), m.PollChecks("A", SHA))
        self.assertEqual(gh.relanzadas, ["42"])
        self.assertTrue(
            [e for e in store.events("demo") if e.kind == "ChecksRelanzados"]
        )

    def test_tras_relanzar_y_seguir_sin_correr_es_atasco(self):
        plan = parse_plan(PLAN)
        bv = m.BlockView(block=plan.block("A"))
        ahora = time.time()
        bv.attempts.append(
            m.Attempt(
                n=1,
                role="implementar",
                modelo="glm",
                started=ahora - 7200,
                proc={},
                dir="d",
                ended={"kind": "entregado", "sha": SHA},
                ended_at=ahora - 7000,
            )
        )
        bv.verified[SHA] = {"ok": True}
        bv.pr = {"numero": 7}
        bv.reviews.append({"sha": SHA, "veredicto": "APROBADO", "bloqueantes": []})
        bv.ready.add(SHA)
        bv.esperando[SHA] = {
            "faltan": ["completa"],
            "github_termino": True,
            "desde": ahora - 3600,
        }
        bv.relanzadas_saltadas.add(SHA)
        snap = m.Snapshot(
            plan=plan,
            blocks={"A": bv},
            cooldowns={},
            plan_notices=set(),
            repos={},
        )
        d = diagnostico(snap, "A", pol(), ahora)
        self.assertTrue(d.atorado)
        self.assertIn("relancé", d.motivo)

    def test_relanzada_que_vuelve_a_saltarse_no_es_verde(self):
        from motor import claves

        class Gh(GhGrabado):
            def _gh(self, *a):
                if a[0] == "api" and "/events" in a[1]:
                    return "2026-10-21T21:00:00Z"
                return super()._gh(*a)

        salto = _run("completa", conclusion="skipped", rid=42)
        salto["completed_at"] = "2026-10-21T20:00:00Z"
        gh = Gh(proteccion=json.dumps(["completa"]), runs=json.dumps([salto]))
        plan, store, fx = self._fx(gh)
        store.append(
            "demo",
            "A",
            "ChecksEsperando",
            {"sha": SHA, "faltan": ["completa"], "github_termino": True},
            ukey=claves.checks_esperando("demo", "A", SHA, ("completa",), True),
            ts=time.time() - 3600,
        )
        fx.poll_checks(plan, self._snap(plan, store), m.PollChecks("A", SHA))
        self.assertEqual(gh.relanzadas, ["42"])
        salto["completed_at"] = "2026-10-22T12:00:00Z"
        gh.runs = json.dumps([salto])
        self.assertEqual(gh.checks("o/r", 7, SHA, (), ("42",)).faltan, ("completa",))
        fx.poll_checks(plan, self._snap(plan, store), m.PollChecks("A", SHA))
        self.assertEqual(gh.relanzadas, ["42"])
        self.assertFalse(
            [
                e
                for e in store.events("demo")
                if e.kind == "ChecksResult" and e.data.get("green")
            ]
        )
        self.assertEqual(
            self._snap(plan, store).blocks["A"].esperando[SHA]["faltan"],
            ["completa"],
        )

    def test_rojo_trae_pr_url_y_las_ultimas_80_lineas(self):
        gh = GhGrabado(runs=json.dumps([_run("ci", conclusion="failure", rid=7)]))
        plan, store, fx = self._fx(gh)
        fx.poll_checks(plan, self._snap(plan, store), m.PollChecks("A", SHA))
        (rojo,) = [
            e
            for e in store.events("demo")
            if e.kind == "ChecksResult" and not e.data["green"]
        ]
        self.assertIn("PR #7", rojo.data["detalle"])
        self.assertIn("https://github.com/o/r/actions/runs/7", rojo.data["detalle"])
        self.assertIn("linea-100", rojo.data["detalle"])
        self.assertIn("linea-021", rojo.data["detalle"])
        self.assertNotIn("linea-001", rojo.data["detalle"])

    def test_reentrega_del_mismo_sha_relanza_las_fallidas_una_vez(self):
        gh = GhGrabado(runs=json.dumps([_run("ci", conclusion="failure", rid=7)]))
        plan, store, fx = self._fx(gh)
        t0 = time.time() - 1000
        store.append(
            "demo",
            "A",
            "AttemptStarted",
            {"n": 1, "role": "implementar", "modelo": "glm", "proc": {}, "dir": "d"},
            ts=t0,
        )
        store.append(
            "demo",
            "A",
            "AttemptEnded",
            {
                "n": 1,
                "role": "implementar",
                "outcome": {"kind": "entregado", "sha": SHA},
            },
            ts=t0 + 1,
        )
        fx.poll_checks(plan, self._snap(plan, store), m.PollChecks("A", SHA))
        self.assertEqual(gh.relanzadas_fallidas, [])
        for kind, datos in [
            ("Verified", {"sha": SHA, "ok": True}),
            ("PrOpened", {"numero": 7, "rama": "f"}),
            ("Reviewed", {"sha": SHA, "veredicto": "APROBADO", "bloqueantes": []}),
            ("MarkedReady", {"sha": SHA}),
        ]:
            store.append("demo", "A", kind, datos, ts=t0 + 50)
        store.append(
            "demo",
            "A",
            "AttemptStarted",
            {"n": 2, "role": "implementar", "modelo": "glm", "proc": {}, "dir": "d"},
        )
        store.append(
            "demo",
            "A",
            "AttemptEnded",
            {
                "n": 2,
                "role": "implementar",
                "outcome": {"kind": "entregado", "sha": SHA},
            },
        )
        snap = self._snap(plan, store)
        self.assertIs(m.estado(snap, "A"), m.Estado.CHECKS)
        fx.poll_checks(plan, snap, m.PollChecks("A", SHA))
        self.assertEqual(gh.relanzadas_fallidas, ["7"])
        self.assertEqual(
            len([e for e in store.events("demo") if e.kind == "ChecksResult"]), 1
        )
        fx.poll_checks(plan, self._snap(plan, store), m.PollChecks("A", SHA))
        self.assertEqual(gh.relanzadas_fallidas, ["7"])
        self.assertEqual(
            len([e for e in store.events("demo") if e.kind == "ChecksResult"]), 2
        )

    def test_tercera_entrega_del_mismo_sha_relanza_y_cuenta_el_rojo(self):
        gh = GhGrabado(runs=json.dumps([_run("ci", conclusion="failure", rid=7)]))
        plan, store, fx = self._fx(gh)

        def entrega(n):
            store.append(
                "demo",
                "A",
                "AttemptStarted",
                {
                    "n": n,
                    "role": "implementar",
                    "modelo": "glm",
                    "proc": {},
                    "dir": "d",
                },
            )
            store.append(
                "demo",
                "A",
                "AttemptEnded",
                {
                    "n": n,
                    "role": "implementar",
                    "outcome": {"kind": "entregado", "sha": SHA},
                },
            )

        def poll():
            fx.poll_checks(plan, self._snap(plan, store), m.PollChecks("A", SHA))

        entrega(1)
        poll()
        for kind, datos in [
            ("Verified", {"sha": SHA, "ok": True}),
            ("PrOpened", {"numero": 7, "rama": "f"}),
            ("Reviewed", {"sha": SHA, "veredicto": "APROBADO", "bloqueantes": []}),
            ("MarkedReady", {"sha": SHA}),
        ]:
            store.append("demo", "A", kind, datos)
        entrega(2)
        poll()
        poll()
        entrega(3)
        for _ in range(4):
            poll()
        self.assertEqual(gh.relanzadas_fallidas, ["7", "7"])
        rojos = [e for e in store.events("demo") if e.kind == "ChecksResult"]
        self.assertGreaterEqual(len(rojos), 3)


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="h-merge-"))
        self.repo, self.sha, self.w = _bare_con_rama(self.t)
        (self.t / "gh").mkdir()

    def _fake(self, cfg):
        (self.t / "gh" / "repos.json").write_text(json.dumps({self.repo: cfg}))
        return FakeGitHub(self.t / "gh")

    def test_fake_tipos(self):
        gh = self._fake(
            {
                "requeridos": ["ci"],
                "checks": {"ci": "./ci.sh"},
            }
        )
        pr = gh.abrir_pr(self.repo, "feat", "main", "t", "c")
        with self.assertRaises(Borrador):
            gh.merge(self.repo, pr.numero, self.sha, "squash", "m")
        gh.marcar_listo(self.repo, pr.numero)
        (self.w / "y.txt").write_text("y\n")
        git("add", "-A", cwd=self.w)
        git(
            "-c",
            "user.name=a",
            "-c",
            "user.email=a@l",
            "commit",
            "-q",
            "-m",
            "y",
            cwd=self.w,
        )
        git("push", "-q", self.repo, "feat", cwd=self.w)
        with self.assertRaises(PuntaMovida):
            gh.merge(self.repo, pr.numero, self.sha, "squash", "m")

    def test_fake_politica_y_conflicto(self):
        gh = self._fake({"requeridos": ["fantasma"], "checks": {}})
        pr = gh.abrir_pr(self.repo, "feat", "main", "t", "c")
        gh.marcar_listo(self.repo, pr.numero)
        with self.assertRaises(PoliticaBloquea):
            gh.merge(self.repo, pr.numero, self.sha, "squash", "m")
        gh2 = self._fake({"requeridos": ["ci"], "checks": {"ci": "./ci.sh"}})
        pr2 = gh2.abrir_pr(self.repo, "feat", "main", "t", "c")
        gh2.marcar_listo(self.repo, pr2.numero)
        git("checkout", "-q", "main", cwd=self.w)
        (self.w / "x.txt").write_text("otro\n")
        git("add", "-A", cwd=self.w)
        git(
            "-c",
            "user.name=a",
            "-c",
            "user.email=a@l",
            "commit",
            "-q",
            "-m",
            "b",
            cwd=self.w,
        )
        git("push", "-q", self.repo, "main", cwd=self.w)
        with self.assertRaises(Conflicto):
            gh2.merge(self.repo, pr2.numero, self.sha, "squash", "m")

    def test_gh_clasifica_por_tipo(self):
        casos = [
            ("Pull Request is still a draft", Borrador),
            ("Head branch was modified", PuntaMovida),
            ("the merge commit cannot be cleanly created", Conflicto),
            ("required status checks have not passed", PoliticaBloquea),
        ]
        for texto, tipo in casos:
            with self.subTest(texto=texto):

                class F(GhGitHub):
                    def _gh(self, *a):
                        if a[:2] == ("pr", "merge"):
                            raise RuntimeError(texto)
                        return _pr_json()

                with self.assertRaises(tipo):
                    F({}).merge("o/r", 7, SHA, "squash", "m")

    def _fx_merge(self, error):
        from motor.github import PrView

        t = Path(tempfile.mkdtemp(prefix="h-fxm-"))
        store = Store(t / "motor.db")
        plan = parse_plan(PLAN)
        gh = SimpleNamespace(
            error=error,
            listos=[],
            ver=lambda repo, n: PrView(n, "f", "main", False, "b" * 40, "abierto", ""),
            merge=lambda *a: (_ for _ in ()).throw(error),
        )
        gh.marcar_listo = lambda repo, n: gh.listos.append(n)
        fx = Effects(SimpleNamespace(store=store, gh=gh, checks_cada_s=0.0))
        snap = m.fold(plan, [])
        snap.blocks["A"].pr = {"numero": 7}
        return plan, store, fx, snap, gh

    def test_punta_movida_le_dice_eso_al_agente(self):
        plan, store, fx, snap, gh = self._fx_merge(PuntaMovida("se movió"))
        fx.merge(plan, snap, m.Merge(m.MergeTicket("A", SHA, 7)))
        (rojo,) = [e for e in store.events("demo") if e.kind == "ChecksResult"]
        self.assertEqual(
            rojo.data["detalle"],
            "alguien subió un commit a la rama del PR después de la revisión; "
            "revísalo y vuelve a entregar",
        )

    def test_politica_invalida_los_checks_y_vuelve_a_esperar(self):
        plan, store, fx, snap, gh = self._fx_merge(
            PoliticaBloquea("branch protection: required review")
        )
        store.append(
            "demo", "A", "ChecksResult", {"sha": SHA, "green": True}, ts=time.time()
        )
        snap = m.fold(plan, store.events("demo"))
        snap.blocks["A"].pr = {"numero": 7}
        fx.merge(plan, snap, m.Merge(m.MergeTicket("A", SHA, 7)))
        self.assertTrue(
            [e for e in store.events("demo") if e.kind == "ChecksInvalidados"]
        )
        snap2 = m.fold(plan, store.events("demo"))
        self.assertNotIn(SHA, snap2.blocks["A"].checks)
        self.assertIn(SHA, snap2.blocks["A"].politica_bloquea)

    def test_politica_de_una_hora_es_atasco_con_el_texto_de_github(self):
        plan = parse_plan(PLAN)
        bv = m.BlockView(block=plan.block("A"))
        ahora = time.time()
        bv.attempts.append(
            m.Attempt(
                n=1,
                role="implementar",
                modelo="glm",
                started=ahora - 7200,
                proc={},
                dir="d",
                ended={"kind": "entregado", "sha": SHA},
                ended_at=ahora - 7000,
            )
        )
        bv.verified[SHA] = {"ok": True}
        bv.pr = {"numero": 7}
        bv.reviews.append({"sha": SHA, "veredicto": "APROBADO", "bloqueantes": []})
        bv.ready.add(SHA)
        bv.politica_bloquea[SHA] = {
            "desde": ahora - 3601,
            "detalle": "branch protection: required review",
        }
        snap = m.Snapshot(
            plan=plan, blocks={"A": bv}, cooldowns={}, plan_notices=set(), repos={}
        )
        d = diagnostico(snap, "A", pol(), ahora)
        self.assertEqual(d.causa, Causa.POLITICA_BLOQUEA)
        self.assertTrue(d.atorado)
        self.assertIn("required review", d.motivo)

    def test_politica_conserva_desde_plegando_eventos(self):
        plan = parse_plan(PLAN)
        t = Path(tempfile.mkdtemp(prefix="h-pol-"))
        store = Store(t / "motor.db")
        t0 = time.time() - 2 * 3600

        def ev(k, d, ts):
            store.append("demo", "A", k, d, ts=ts)

        ev(
            "AttemptStarted",
            {"n": 1, "role": "implementar", "modelo": "glm", "proc": {}, "dir": "d"},
            t0,
        )
        ev(
            "AttemptEnded",
            {
                "n": 1,
                "role": "implementar",
                "outcome": {"kind": "entregado", "sha": SHA},
            },
            t0 + 1,
        )
        for k, d in [
            ("Verified", {"sha": SHA, "ok": True}),
            ("PrOpened", {"numero": 7, "rama": "f"}),
            ("Reviewed", {"sha": SHA, "veredicto": "APROBADO", "bloqueantes": []}),
            ("MarkedReady", {"sha": SHA}),
        ]:
            ev(k, d, t0 + 2)
        for i in range(16):
            ts = t0 + 10 + i * 300
            ev("ChecksResult", {"sha": SHA, "green": True, "detalle": ""}, ts)
            ev(
                "ChecksInvalidados",
                {"sha": SHA, "detalle": "Required approving review"},
                ts + 5,
            )
        snap = m.fold(plan, store.events("demo"))
        self.assertEqual(snap.blocks["A"].politica_bloquea[SHA]["desde"], t0 + 15)
        d = diagnostico(snap, "A", pol(), t0 + 10 + 16 * 300)
        self.assertTrue(d.atorado)
        self.assertEqual(d.causa, Causa.POLITICA_BLOQUEA)
        self.assertIn("Required approving review", d.motivo)

    def test_borrador_marca_el_pr_como_listo(self):
        plan, store, fx, snap, gh = self._fx_merge(Borrador("draft"))
        fx.merge(plan, snap, m.Merge(m.MergeTicket("A", SHA, 7)))
        self.assertEqual(gh.listos, [7])
        self.assertTrue([e for e in store.events("demo") if e.kind == "MarkedReady"])

    def test_es_del_agente_ya_no_existe(self):
        self.assertFalse(hasattr(fxmod, "es_del_agente"))


class _WsFalso:
    def __init__(self, base: Path):
        self.base = base
        self.n = 0

    def copia(self, repo, sha, destino):
        self.n += 1
        d = self.base / f"copia-{self.n}"
        (d / "ops").mkdir(parents=True, exist_ok=True)
        return d


class DespliegueTest(unittest.TestCase):
    def _fx(self, scripts: dict[str, str]):
        t = Path(tempfile.mkdtemp(prefix="h-deploy-"))
        plan = parse_plan(PLAN_RELEASE)
        ws = _WsFalso(t)
        cx = SimpleNamespace(
            home=t,
            maquina=Maquina(nombre="x", conf=Path("c")),
            conf=SimpleNamespace(tiempos={}),
            store=Store(t / "motor.db"),
            ws=ws,
            gh=None,
        )
        fx = Effects(cx)
        snap = m.Snapshot(
            plan=plan,
            blocks={"A": m.BlockView(block=plan.block("A"))},
            cooldowns={},
            plan_notices=set(),
            repos={},
        )
        snap.blocks["A"].merged = [{"sha": SHA, "commit": SHA}]
        original = fx._deploy_data

        def con_scripts(plan_, snap_, a_):
            w = ws.copia("", "", None)
            for nombre, cuerpo in scripts.items():
                (w / "ops" / nombre).write_text(cuerpo)
            ws_copia = ws.copia
            ws.copia = lambda repo, sha, destino: w
            try:
                return original(plan_, snap_, a_)
            finally:
                ws.copia = ws_copia

        return fx, plan, snap, con_scripts, t

    def test_receta_trae_el_contrato_de_salida(self):
        self.assertIn("MOTOR_SHA", RECETA)
        self.assertIn("75", RECETA)
        self.assertIn("no es el código; reintenta luego", RECETA)
        self.assertIn("solo sale 0 si producción ya corre", RECETA)

    def test_receta_con_un_script_vacio_no_vale_al_entregar_el_plan(self):
        texto = PLAN_RELEASE.replace("checklist=ops/c.sh", "checklist=")
        with self.assertRaises(PlanError):
            parse_plan(texto)

    def test_75_no_hace_rollback_ni_culpa(self):
        fx, plan, snap, corre, t = self._fx(
            {
                "d.sh": "exit 75\n",
                "c.sh": "touch checklist-corrio\n",
                "r.sh": "touch rollback-corrio\n",
            }
        )
        r = corre(plan, snap, m.Deploy("A", SHA))
        self.assertFalse(r["ok"])
        self.assertEqual(r["codigo"], 75)
        self.assertIn("no es el código; reintenta luego", r["detalle"])
        w = fx.cx.ws.base / "copia-1"
        self.assertFalse((w / "rollback-corrio").exists())
        self.assertFalse((w / "checklist-corrio").exists())

    def test_checklist_solo_pasa_si_produccion_corre_motor_sha(self):
        fx, plan, snap, corre, t = self._fx(
            {
                "d.sh": 'echo "$MOTOR_SHA" > prod.txt\n',
                "c.sh": 'test "$(cat prod.txt)" = "$MOTOR_SHA"\n',
                "r.sh": "true\n",
            }
        )
        r = corre(plan, snap, m.Deploy("A", SHA))
        self.assertTrue(r["ok"], r["detalle"])

    def test_ya_no_hay_atajo_por_checklist_sobre_lo_anterior(self):
        fx, plan, snap, corre, t = self._fx(
            {
                "d.sh": "touch desplegado.txt\n",
                "c.sh": "true\n",
                "r.sh": "true\n",
            }
        )
        snap.blocks["A"].deploy_marcados = {SHA}
        r = corre(plan, snap, m.Deploy("A", SHA))
        self.assertTrue(r["ok"], r["detalle"])
        w = fx.cx.ws.base / "copia-1"
        self.assertTrue((w / "desplegado.txt").exists())

    def test_dos_despliegues_del_mismo_repo_van_de_uno_en_uno(self):
        log = Path(tempfile.mkdtemp(prefix="h-lock-")) / "log.txt"
        scripts = {
            "d.sh": f"echo inicio >> {log}\nsleep 0.4\necho fin >> {log}\n",
            "c.sh": "true\n",
            "r.sh": "true\n",
        }
        fx, plan, snap, corre, t = self._fx(scripts)
        hilos = [
            threading.Thread(target=lambda: corre(plan, snap, m.Deploy("A", SHA)))
            for _ in range(2)
        ]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        self.assertEqual(
            log.read_text(encoding="utf-8").splitlines(),
            ["inicio", "fin", "inicio", "fin"],
        )

    def test_prueba_real_con_75_tampoco_culpa(self):
        fx, plan, snap, corre, t = self._fx({})
        plan = parse_plan(
            PLAN_RELEASE.replace("prueba-real: true", "prueba-real: exit 75")
        )
        snap.blocks["A"] = m.BlockView(block=plan.block("A"))
        snap.blocks["A"].merged = [{"sha": SHA, "commit": SHA}]
        snap.plan = plan
        r = fx._prove_data(plan, snap, m.Prove("A", SHA))
        self.assertFalse(r["ok"])
        self.assertEqual(r["codigo"], 75)
        self.assertIn("no es el código; reintenta luego", r["detalle"])

    def test_segundo_deploy_tras_75_ya_anotado_lanza_un_intento_nuevo(self):
        fx, plan, snap, corre, t = self._fx({})
        h_ins, h_maq = fx._huellas("deployed", plan.block("A"), snap)
        d = t / "aparte-1"
        d.mkdir()
        datos = {
            "sha": SHA,
            "ok": False,
            "codigo": 75,
            "detalle": "no es el código; reintenta luego",
            "huella": h_ins,
            "huella_maquina": h_maq,
            "falta_comando": [],
        }
        (d / "outcome.json").write_text(json.dumps({"data": datos}))
        fx.cx.store.append(
            "demo",
            "A",
            "DeployStarted",
            {"sha": SHA, "dir": str(d), "proc": {}, "n": 1},
            ts=time.time() - 1200,
        )
        fx.deploy(plan, snap, m.Deploy("A", SHA))
        self.assertEqual(
            [
                e.data.get("codigo")
                for e in fx.cx.store.events("demo")
                if e.kind == "Deployed"
            ],
            [75],
        )
        lanzados = []
        original = fxmod.plataforma.lanzar_separado
        fxmod.plataforma.lanzar_separado = lambda *a, **k: lanzados.append(a) or {}
        try:
            fx.env_base = lambda extra=None: {}
            fx.deploy(plan, snap, m.Deploy("A", SHA))
        finally:
            fxmod.plataforma.lanzar_separado = original
        self.assertEqual(len(lanzados), 1)
        iniciados = [e for e in fx.cx.store.events("demo") if e.kind == "DeployStarted"]
        self.assertEqual(iniciados[-1].data.get("n"), 2)

    def test_75_reintenta_a_los_15_min_y_a_las_3_h_es_atasco(self):
        from tests.test_puro import ev

        plan = parse_plan(PLAN_RELEASE)
        ahora = time.time()
        evs = [
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
                    "outcome": {"kind": "entregado", "sha": SHA},
                },
            ),
            ev("Merged", "A", {"sha": SHA, "commit": SHA}),
            ev(
                "Deployed",
                "A",
                {"sha": SHA, "ok": False, "codigo": 75, "detalle": "75"},
            ),
        ]
        snap = m.fold(plan, evs)
        self.assertIs(m.estado(snap, "A"), m.Estado.DESPLEGAR)
        sin = [a for a in m.decide(snap, pol(), ahora + 60) if isinstance(a, m.Deploy)]
        con = [a for a in m.decide(snap, pol(), ahora + 901) if isinstance(a, m.Deploy)]
        self.assertEqual(sin, [])
        self.assertEqual(len(con), 1)
        d = diagnostico(snap, "A", pol(), ahora + 3 * 3600 + 1)
        self.assertEqual(d.causa, Causa.SALIDA_75)
        self.assertTrue(d.atorado)


if __name__ == "__main__":
    unittest.main()
