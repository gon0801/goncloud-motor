"""Las guías del encargo (A.1) y el anexo con su contenido completo (A.2).

`guias()` es pura: la exigencia del (carril, papel), el CLI y `StartAttempt.correccion`
(verdadero en un bloque de bug o tras una revisión no aprobada) deciden la lista.
"""

from __future__ import annotations

import time
import unittest

from motor import machine as m
from motor import pstack as ps
from motor.guias import guias
from motor.plan import parse_plan
from motor.store import Event

PLAN = """# Demo
autopiloto: motor
plan: demo
repo: git@github.com:x/app.git

## Bloque A — uno
lane: gate
prueba-real: true
| Fila | Contenido | Acepta |
| --- | --- | --- |
| A.1 | algo | true |
"""

FEATURE = "pstack/skills/poteto-mode/playbooks/feature.md"
BUGFIX = "pstack/skills/poteto-mode/playbooks/bug-fix.md"
BASE = "pstack/skills/poteto-mode/SKILL.md"
INTERROGATE = "pstack/skills/interrogate/SKILL.md"
ADAPTADOR = "pstack/docs/harness/claude-code.md"

GATE_IMPL = ps.Exigencia(playbook=True)
GATE_REV = ps.Exigencia(
    skills=("poteto-mode", "interrogate"), playbook=True, min_subagentes=2
)

_i = [0]


def ev(kind: str, data: dict, block: str = "A") -> Event:
    _i[0] += 1
    return Event(_i[0], time.time(), "demo", block, kind, data)


def pol() -> m.Policy:
    return m.Policy(
        implementadores=("sonnet",),
        revisores=("opus",),
        cuenta={"sonnet": "anthropic", "opus": "anthropic"},
        max_parallel={"anthropic": 2},
    )


def arranque(eventos: list[Event], role: str = "implementar") -> m.StartAttempt:
    s = m.fold(parse_plan(PLAN), eventos)
    for a in m.decide(s, pol(), time.time()):
        if isinstance(a, m.StartAttempt) and a.role == role:
            return a
    raise AssertionError(f"decide no lanzó un intento de {role}")


def entregado() -> list[Event]:
    return [
        ev(
            "AttemptStarted",
            {"n": 1, "role": "implementar", "modelo": "sonnet", "proc": {}, "dir": "d"},
        ),
        ev(
            "AttemptEnded",
            {
                "n": 1,
                "role": "implementar",
                "outcome": {"kind": "entregado", "sha": "a" * 40},
            },
        ),
    ]


def revisable() -> list[Event]:
    return [
        *entregado(),
        ev("Verified", {"sha": "a" * 40, "ok": True}),
        ev("PrOpened", {"numero": 7}),
    ]


class TestGuias(unittest.TestCase):
    def test_implementar_en_gate_abre_el_playbook_feature(self):
        a = arranque([])
        self.assertFalse(a.correccion)
        self.assertEqual(guias(GATE_IMPL, "kimi", a.correccion)[0], FEATURE)

    def test_con_una_revision_no_aprobada_abre_bug_fix(self):
        a = arranque(
            [
                *revisable(),
                ev(
                    "Reviewed",
                    {
                        "sha": "a" * 40,
                        "veredicto": "CAMBIOS",
                        "bloqueantes": [{"texto": "x", "repro": "false"}],
                    },
                ),
            ]
        )
        self.assertTrue(a.correccion)
        self.assertEqual(guias(GATE_IMPL, "kimi", a.correccion)[0], BUGFIX)

    def test_feedback_de_un_cambio_de_plan_sin_revisiones_sigue_en_feature(self):
        a = arranque(
            [
                ev(
                    "AttemptStarted",
                    {
                        "n": 1,
                        "role": "implementar",
                        "modelo": "sonnet",
                        "proc": {},
                        "dir": "d",
                    },
                ),
                ev(
                    "AttemptEnded",
                    {
                        "n": 1,
                        "role": "implementar",
                        "outcome": {
                            "kind": "interrumpido",
                            "detalle": "el plan cambió: la fila A.1",
                        },
                    },
                ),
            ]
        )
        self.assertTrue(
            any("plan cambió" in x for x in a.feedback),
            "el feedback del cambio de plan está, y no debe marcar la corrección",
        )
        self.assertFalse(a.correccion)
        self.assertEqual(guias(GATE_IMPL, "kimi", a.correccion)[0], FEATURE)

    def test_revisar_en_gate_lleva_interrogate(self):
        a = arranque(revisable(), "revisar")
        g = guias(GATE_REV, "claude", a.correccion)
        self.assertIn(INTERROGATE, g)
        self.assertLess(g.index(INTERROGATE), g.index(BASE))
        self.assertEqual(
            guias(GATE_REV, "claude", True), (BUGFIX, INTERROGATE, BASE, ADAPTADOR)
        )

    def test_fast_no_lleva_playbook(self):
        self.assertEqual(guias(ps.Exigencia(), "claude", False), (BASE, ADAPTADOR))

    def test_claude_lleva_el_adaptador_y_kimi_no(self):
        self.assertEqual(guias(GATE_IMPL, "claude", False)[-1], ADAPTADOR)
        self.assertNotIn(ADAPTADOR, guias(GATE_IMPL, "kimi", False))


if __name__ == "__main__":
    unittest.main()
