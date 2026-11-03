"""Los dos atascos reales de MOTOR 07 C, sin modificar el log de produccion."""

import unittest
from unittest.mock import patch

from motor import machine as m
from motor.github import PrView
from motor.plan import Plan, parse_plan
from motor.workspace import git
from tests.test_no_caerse import Banco
from tests.test_puro import PLAN, entregado, ev


class ReentregaTest(unittest.TestCase):
    def test_sin_cambios_con_plan_corregido_revisa_sin_gastar_intento(self):
        sha = "a" * 40
        anterior = parse_plan(PLAN)
        corregido = parse_plan(
            PLAN.replace("| A.1 | algo |", "| A.1 | ya satisfecho |")
        )
        eventos = [ev("PlanLoaded", "", {"plan": anterior.to_dict(), "version": 1})]
        eventos += entregado(sha=sha)
        eventos += [
            ev("Verified", "A", {"sha": sha, "ok": True}),
            ev("PrOpened", "A", {"numero": 19}),
            ev(
                "Reviewed",
                "A",
                {
                    "sha": sha,
                    "veredicto": "CAMBIOS",
                    "bloqueantes": [{"texto": "exigencia anterior", "repro": "false"}],
                },
            ),
        ]
        for n in (2, 3):
            if n == 2:
                eventos.append(
                    ev("PlanLoaded", "", {"plan": corregido.to_dict(), "version": 2})
                )
            eventos += [
                ev(
                    "AttemptStarted",
                    "A",
                    {
                        "n": n,
                        "role": "implementar",
                        "modelo": "glm",
                        "dir": f"d{n}",
                        "sha": sha,
                    },
                ),
                ev(
                    "AttemptEnded",
                    "A",
                    {
                        "n": n,
                        "role": "implementar",
                        "outcome": {"kind": "sin_cambios"},
                    },
                ),
            ]
        snap = m.fold(corregido, eventos)
        self.assertIs(m.estado(snap, "A"), m.Estado.LISTO)
        self.assertFalse(
            any(m._cuenta_falla(a, snap.blocks["A"]) for a in snap.blocks["A"].attempts)
        )
        eventos.append(
            ev(
                "Reviewed",
                "A",
                {"sha": sha, "veredicto": "APROBADO", "bloqueantes": []},
            )
        )
        self.assertIs(m.estado(m.fold(corregido, eventos), "A"), m.Estado.LISTO)

        # Sin una correccion del plan, repetir la entrega rechazada sigue siendo una falla.
        sin_correccion = [
            e
            for e in eventos[:-1]
            if not (e.kind == "PlanLoaded" and e.data["version"] == 2)
        ]
        snap = m.fold(anterior, sin_correccion)
        self.assertIs(m.estado(snap, "A"), m.Estado.LISTO)
        self.assertTrue(
            m._cuenta_falla(snap.blocks["A"].attempts[-1], snap.blocks["A"])
        )


class UnionExternaTest(Banco):
    def test_union_externa_con_commit_vacio_sale_de_arreglar_y_termina(self):
        d = self.daemon()
        datos = {**self.plan, "blocks": [self.plan["blocks"][0]]}
        plan = Plan.from_dict(datos)
        seed = self.tmp / "b" / "seed-app"

        def g(*args):
            r = git("-c", "user.name=t", "-c", "user.email=t@l", *args, cwd=seed)
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout.strip()

        base = g("rev-parse", "HEAD")
        (seed / "arreglo.txt").write_text("trabajo verificado\n")
        g("add", "arreglo.txt")
        g("commit", "-qm", "entrega")
        sha = g("rev-parse", "HEAD")
        rama = d.cx.ws.rama(plan.id, "A")
        g("push", "-q", plan.block("A").repo, f"{sha}:refs/heads/{rama}")
        d.cx.ws.espejo(plan.block("A").repo)
        g("commit", "--allow-empty", "-qm", "reentrega sin cambiar contenido")
        punta = g("rev-parse", "HEAD")
        arbol = g("rev-parse", "HEAD^{tree}")
        unido = g("commit-tree", arbol, "-p", base, "-m", "union externa")
        g(
            "push",
            "-q",
            plan.block("A").repo,
            f"{punta}:refs/heads/{rama}",
            f"{unido}:refs/heads/main",
        )
        g("push", "-q", plan.block("A").repo, "--delete", rama)
        eventos = [ev("PlanLoaded", "", {"plan": datos, "version": 1})]
        eventos += entregado(sha=sha)
        eventos += [
            ev("Verified", "A", {"sha": sha, "ok": True}),
            ev(
                "Reviewed",
                "A",
                {
                    "sha": sha,
                    "veredicto": "CAMBIOS",
                    "bloqueantes": [{"texto": "rechazo anterior", "repro": "false"}],
                },
            ),
            ev("PrOpened", "A", {"numero": 19, "rama": rama}),
            ev(
                "AttemptStarted",
                "A",
                {
                    "n": 2,
                    "role": "implementar",
                    "modelo": "glm",
                    "dir": "d2",
                    "sha": sha,
                },
            ),
            ev(
                "AttemptEnded",
                "A",
                {"n": 2, "role": "implementar", "outcome": {"kind": "sin_cambios"}},
            ),
        ]
        for e in eventos:
            d.store.append(plan.id, e.block, e.kind, e.data)
        self.assertIs(m.estado(d._fold(plan), "A"), m.Estado.LISTO)
        pruebas = []
        d.fx.prove = lambda p, s, a: pruebas.append((s, a))
        vista = PrView(19, rama, "main", False, punta, "mergeado", unido)
        with patch.object(d.cx.gh, "ver", return_value=vista):
            d._tick_plan(plan, [plan], 1e10)
        snap = m.fold(plan, d.store.events(plan.id))
        self.assertIs(m.estado(snap, "A"), m.Estado.PROBAR)
        self.assertEqual(len(pruebas), 1)
        (merged,) = [e for e in d.store.events(plan.id) if e.kind == "Merged"]
        self.assertEqual(
            (merged.data["sha"], merged.data["commit"], merged.data["externo"]),
            (sha, unido, True),
        )
        resultado = d.fx._prove_data(plan, snap, m.Prove("A", sha))
        d.store.append(plan.id, "A", "Proved", resultado)
        self.assertIs(
            m.estado(m.fold(plan, d.store.events(plan.id)), "A"), m.Estado.HECHO
        )
