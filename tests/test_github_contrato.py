"""Contrato de GitHub: las mismas pruebas para el falso y para el real.

El real solo corre con MOTOR_GH_SANDBOX=<dueño/repo> (un repo desechable con un check requerido
"ci" en su rama principal). Sin esa variable se salta y se dice por qué.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
import uuid
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
    git(
        "-c", "user.name=a", "-c", "user.email=a@l", "commit", "-q", "-m", "base", cwd=w
    )
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
        (self.t / "gh" / "repos.json").write_text(
            json.dumps(
                {
                    self.repo: {
                        "requeridos": ["ci", "completa"],
                        "solo_listo": ["completa"],
                        "checks": {
                            "ci": "./ci.sh",
                            "completa": "./ci.sh",
                            "seguridad": "./ci.sh",
                        },
                    }
                }
            )
        )
        self.gh = FakeGitHub(self.t / "gh")

    def test_flujo_completo(self):
        pr = self.gh.abrir_pr(self.repo, "feat", "main", "t", "c")
        self.assertTrue(pr.borrador)
        self.assertEqual(
            self.gh.abrir_pr(self.repo, "feat", "main", "t", "c").numero,
            pr.numero,
            "idempotente por rama",
        )
        v = self.gh.checks(self.repo, pr.numero, self.sha, ("seguridad",))
        self.assertEqual(
            (v.terminal, v.faltan),
            (False, ("completa",)),
            "completa no corre en borrador",
        )
        with self.assertRaises(MergeRechazado):
            self.gh.merge(self.repo, pr.numero, self.sha, "squash", "m")
        self.gh.marcar_listo(self.repo, pr.numero)
        self.assertTrue(
            self.gh.checks(self.repo, pr.numero, self.sha, ("seguridad",)).green
        )
        hecho = self.gh.merge(self.repo, pr.numero, self.sha, "squash", "m")
        self.assertEqual(hecho.estado, "mergeado")
        self.assertEqual(
            self.gh.merge(self.repo, pr.numero, self.sha, "squash", "m").estado,
            "mergeado",
            "idempotente",
        )

    def test_merge_rechaza_si_la_punta_se_movio(self):
        pr = self.gh.abrir_pr(self.repo, "feat", "main", "t", "c")
        self.gh.marcar_listo(self.repo, pr.numero)
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
        with self.assertRaises(MergeRechazado):
            self.gh.merge(self.repo, pr.numero, self.sha, "squash", "m")

    def test_checks_en_rojo(self):
        (self.w / "roto.txt").write_text("1")
        git("add", "-A", cwd=self.w)
        git(
            "-c",
            "user.name=a",
            "-c",
            "user.email=a@l",
            "commit",
            "-q",
            "-m",
            "r",
            cwd=self.w,
        )
        git("push", "-q", self.repo, "feat", cwd=self.w)
        sha = git("rev-parse", "HEAD", cwd=self.w).stdout.strip()
        pr = self.gh.abrir_pr(self.repo, "feat", "main", "t", "c")
        v = self.gh.checks(self.repo, pr.numero, sha, ())
        self.assertEqual((v.terminal, v.green, "ci" in v.fallas), (True, False, True))


class GhReintentaTest(unittest.TestCase):
    """Sin red: `gh` falla como falla cuando la rama recién empujada aún no se ve del lado de GitHub."""

    def gh(self, errores: list[str]):
        llamadas = []

        class Fake(GhGitHub):
            def _gh(self, *a):
                llamadas.append(a[:2])
                if a[:2] == ("pr", "list"):
                    return "[]"
                if a[:2] == ("pr", "create"):
                    if errores:
                        raise RuntimeError(errores.pop(0))
                    return "https://github.com/o/r/pull/7\n"
                return json.dumps(
                    {
                        "number": 7,
                        "headRefName": "f",
                        "baseRefName": "main",
                        "isDraft": True,
                        "headRefOid": "a" * 40,
                        "state": "OPEN",
                        "mergeCommit": None,
                    }
                )

        g = Fake()
        g.dormir = lambda s: llamadas.append(("dormir", s))
        return g, llamadas

    def test_reintenta_si_la_rama_aun_no_llega(self):
        g, llamadas = self.gh(
            ["gh pr create: GraphQL: No commits between main and f (createPullRequest)"]
            * 2
        )
        self.assertEqual(g.abrir_pr("o/r", "f", "main", "t", "c").numero, 7)
        self.assertEqual(llamadas.count(("pr", "create")), 3)
        self.assertEqual(llamadas.count(("dormir", 3.0)), 2)

    def test_no_reintenta_otros_errores(self):
        g, llamadas = self.gh(["gh pr create: HTTP 401: Bad credentials"])
        with self.assertRaises(RuntimeError):
            g.abrir_pr("o/r", "f", "main", "t", "c")
        self.assertEqual(llamadas.count(("pr", "create")), 1)

    def test_se_rinde_tras_los_reintentos(self):
        g, llamadas = self.gh(["gh pr create: No commits between main and f"] * 20)
        with self.assertRaises(RuntimeError):
            g.abrir_pr("o/r", "f", "main", "t", "c")
        self.assertEqual(llamadas.count(("pr", "create")), 6)


class GhChecksSaltadoTest(unittest.TestCase):
    """Sin red: lo que GitHub devolvió para Orbit PR #487 cuando el motor esperó horas (INGESTA 01)."""

    def checks(self, borrador: bool, listo: list[str]):
        runs = [
            ("gate", "success", "19:37:23"),
            ("reconcile", "success", "19:36:30"),
            ("completa", "skipped", "19:36:20"),
            ("completa", "skipped", "19:37:51"),
        ]

        class Fake(GhGitHub):
            def _gh(self, *a):
                if a[:2] == ("pr", "view"):
                    return json.dumps(
                        {
                            "number": 487,
                            "headRefName": "f",
                            "baseRefName": "master",
                            "isDraft": borrador,
                            "headRefOid": "a" * 40,
                            "state": "OPEN",
                            "mergeCommit": None,
                        }
                    )
                if a[1].endswith("/required_status_checks"):
                    return '["gate", "reconcile"]'
                if "/rules/branches/" in a[1]:
                    return "[]"
                if a[1].endswith("/status"):
                    return '{"state": "success", "statuses": []}'
                if "/issues/487/events" in a[1]:
                    return "".join(f"{h}\n" for h in listo)
                if "/check-runs" in a[1]:
                    assert "completed_at" in a[-1], (
                        "sin la hora no se sabe cuándo se saltó"
                    )
                    return json.dumps(
                        [
                            {
                                "name": n,
                                "status": "completed",
                                "conclusion": c,
                                "completed_at": f"2026-10-21T{h}Z",
                            }
                            for n, c, h in runs
                        ]
                    )
                raise AssertionError(a)

        return Fake().checks("https://github.com/o/r", 487, "a" * 40, ("completa",))

    def test_salto_tras_listo_es_verde(self):
        self.assertTrue(self.checks(False, ["2026-10-21T19:37:40Z"]).green)

    def test_salto_antes_de_volver_a_listo_no_cuenta(self):
        v = self.checks(False, ["2026-10-21T19:00:00Z", "2026-10-21T19:40:00Z"])
        self.assertEqual(v.faltan, ("completa",))
        self.assertTrue(
            v.github_termino,
            "nada corre ni espera: el motor puede notar la incoherencia",
        )

    def test_en_borrador_no_cuenta(self):
        self.assertEqual(
            self.checks(True, ["2026-10-21T19:37:40Z"]).faltan, ("completa",)
        )


class GhChecksRepoPrivadoTest(unittest.TestCase):
    """Sin red: un repo privado en el plan gratis responde 403 a protección y a reglas. Así
    se trabó PSTACK 01 C en goncloud-motor el 2026-10-22 (PollChecks fallando cada 5 min)."""

    def checks(self, conclusion: str):
        plan_gratis = (
            "gh: Upgrade to GitHub Pro or make this repository public to enable this "
            "feature. (HTTP 403)"
        )

        class Fake(GhGitHub):
            def _gh(self, *a):
                if a[:2] == ("pr", "view"):
                    return json.dumps(
                        {
                            "number": 56,
                            "headRefName": "f",
                            "baseRefName": "main",
                            "isDraft": False,
                            "headRefOid": "a" * 40,
                            "state": "OPEN",
                            "mergeCommit": None,
                        }
                    )
                if a[1].endswith("/required_status_checks") or "/rules/branches/" in a[1]:
                    raise RuntimeError(f"gh {' '.join(a)}: {plan_gratis}")
                if a[1].endswith("/status"):
                    return '{"state": "success", "statuses": []}'
                if "/issues/56/events" in a[1]:
                    return "2026-10-22T21:40:00Z\n"
                if "/check-runs" in a[1]:
                    return json.dumps(
                        [
                            {
                                "name": "quality",
                                "status": "completed",
                                "conclusion": conclusion,
                                "completed_at": "2026-10-22T21:45:00Z",
                            }
                        ]
                    )
                raise AssertionError(a)

        return Fake().checks("https://github.com/o/r", 56, "a" * 40, ("quality",))

    def test_sin_proteccion_por_plan_espera_las_pruebas_del_repo(self):
        self.assertTrue(self.checks("success").green)

    def test_sin_proteccion_por_plan_una_prueba_roja_sigue_roja(self):
        self.assertFalse(self.checks("failure").green)


class PollChecksAnotaLaEsperaTest(unittest.TestCase):
    """El motor anota qué prueba espera y si GitHub ya terminó, una vez por cambio: de ahí salen el
    motivo del aviso y la alarma de incoherencia (INGESTA 01)."""

    def test_anota_una_vez_por_cambio(self):
        from types import SimpleNamespace

        from motor import machine as m
        from motor.effects import Effects
        from motor.github import ChecksVerdict, PrView
        from motor.plan import parse_plan
        from motor.store import Store

        plan = parse_plan(
            "# x\nautopiloto: motor\nplan: x\nrepo: /r\n\n## Bloque A — a\nlane: gate\n"
            "prueba-real: true\n| Fila | Contenido | Acepta |\n| --- | --- | --- |\n"
            "| A.1 | a | test -f a |\n"
        )
        t = Path(tempfile.mkdtemp(prefix="poll-"))
        store = Store(t / "motor.db")
        veredictos = [
            ChecksVerdict("s", False, False, ("completa",), (), github_termino=False),
            ChecksVerdict("s", False, False, ("completa",), (), github_termino=False),
            ChecksVerdict("s", False, False, ("completa",), (), github_termino=True),
        ]
        gh = SimpleNamespace(
            ver=lambda repo, n: PrView(n, "f", "main", False, "s", "abierto", ""),
            checks=lambda *a: veredictos.pop(0),
        )
        fx = Effects(SimpleNamespace(store=store, gh=gh, checks_cada_s=0.0))
        snap = m.fold(plan, [])
        snap.blocks["A"].pr = {"numero": 7}
        for _ in range(3):
            fx.poll_checks(plan, snap, m.PollChecks("A", "s"))
        anotados = [e.data for e in store.events() if e.kind == "ChecksEsperando"]
        self.assertEqual(
            anotados,
            [
                {"sha": "s", "faltan": ["completa"], "github_termino": False},
                {"sha": "s", "faltan": ["completa"], "github_termino": True},
            ],
        )


@unittest.skipUnless(
    os.environ.get("MOTOR_GH_SANDBOX"),
    "sin MOTOR_GH_SANDBOX: el contrato contra GitHub real no corre",
)
class ContratoReal(
    unittest.TestCase
):  # pragma: no cover - requiere red y un repo desechable
    """El sandbox es privado y gratuito: GitHub no protege su rama, así que el motor exige "ci" como
    check extra (el workflow `ci` corre `true`). Cada corrida usa su propia rama y la borra al final."""

    def setUp(self):
        self.nombre = os.environ["MOTOR_GH_SANDBOX"]
        self.repo = f"https://github.com/{self.nombre}"
        self.gh = GhGitHub()
        self.rama = f"motor-contrato-{uuid.uuid4().hex[:8]}"
        self.tmp = tempfile.TemporaryDirectory()
        self.w = Path(self.tmp.name) / "w"
        subprocess.run(
            ["gh", "repo", "clone", self.nombre, str(self.w)],
            check=True,
            capture_output=True,
        )
        git("checkout", "-q", "-b", self.rama, cwd=self.w)
        self.addCleanup(self._limpia)

    def _limpia(self):
        subprocess.run(
            ["git", "push", "-q", "origin", "--delete", self.rama],
            cwd=self.w,
            capture_output=True,
        )
        self.tmp.cleanup()

    def _commit(self, nombre: str) -> str:
        # único por corrida: un merge anterior dejó su archivo en main y sin cambios no habría commit
        (self.w / f"{nombre}-{self.rama}.txt").write_text(f"{nombre}\n")
        git("add", "-A", cwd=self.w)
        git("commit", "-q", "-m", nombre, cwd=self.w).check_returncode()
        git("push", "-q", "origin", self.rama, cwd=self.w)
        return git("rev-parse", "HEAD", cwd=self.w).stdout.strip()

    def _espera_checks(self, numero: int, sha: str):
        for _ in range(60):
            v = self.gh.checks(self.repo, numero, sha, ("ci",))
            if v.terminal:
                return v
            time.sleep(5)
        self.fail("el check ci no terminó en 5 minutos")

    def test_flujo_real(self):
        sha = self._commit("contrato")
        pr = self.gh.abrir_pr(
            self.repo, self.rama, "main", "contrato motor", "prueba de contrato"
        )
        self.assertTrue(pr.borrador)
        self.assertEqual(pr.head, sha)
        self.assertEqual(
            self.gh.abrir_pr(self.repo, self.rama, "main", "t", "c").numero,
            pr.numero,
            "idempotente por rama",
        )
        self.gh.marcar_listo(self.repo, pr.numero)
        self.gh.marcar_listo(self.repo, pr.numero)  # idempotente
        self.assertFalse(self.gh.ver(self.repo, pr.numero).borrador)
        v = self._espera_checks(pr.numero, sha)
        self.assertTrue(v.green, f"ci debía salir verde: {v}")
        self.assertEqual(
            self.gh.checks(self.repo, pr.numero, sha, ("no-existe",)).faltan,
            ("no-existe",),
            "un check extra que nadie corre nunca termina",
        )
        with self.assertRaises(MergeRechazado):
            self.gh.merge(self.repo, pr.numero, "0" * 40, "squash", "m")
        hecho = self.gh.merge(
            self.repo, pr.numero, sha, "squash", "contrato motor\n\ncuerpo"
        )
        self.assertEqual(hecho.estado, "mergeado")
        self.assertTrue(hecho.merge_commit)

    def test_merge_rechaza_si_la_punta_se_movio(self):
        viejo = self._commit("uno")
        pr = self.gh.abrir_pr(
            self.repo, self.rama, "main", "contrato punta", "la punta se mueve"
        )
        self.gh.marcar_listo(self.repo, pr.numero)
        nuevo = self._commit("dos")
        self.assertNotEqual(viejo, nuevo)
        with self.assertRaises(MergeRechazado):
            self.gh.merge(self.repo, pr.numero, viejo, "squash", "m")
        self.assertEqual(
            self.gh.ver(self.repo, pr.numero).estado,
            "abierto",
            "el rechazo no mergea nada",
        )
        subprocess.run(
            ["gh", "pr", "close", str(pr.numero), "-R", self.nombre],
            capture_output=True,
        )


if __name__ == "__main__":
    unittest.main()
