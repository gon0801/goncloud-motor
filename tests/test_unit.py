"""Pruebas de la parte pura: plan, reglas de decisión, puerta del merge, clasificación y avisos."""

from __future__ import annotations

import time
import unittest
from pathlib import Path

from taller import machine as m
from taller.notify import PLANTILLAS, AvisoInvalido, lint_plain, render
from taller.plan import PlanError, parse_plan
from taller.store import Event

PLAN = (Path(__file__).parent.parent / "proto" / "plan_demo.md").read_text()


def ev(kind: str, block: str, data: dict, i=[0]) -> Event:
    i[0] += 1
    return Event(i[0], time.time(), "demo", block, kind, data)


def policy(*impl: str) -> m.Policy:
    provs = {p: m.Provider(p, ("x",)) for p in (*impl, "claude")}
    return m.Policy(implementadores=impl, revisores=("claude",), providers=provs)


class PlanTest(unittest.TestCase):
    def test_plan_demo_valido(self):
        p = parse_plan(PLAN)
        self.assertEqual([b.id for b in p.blocks], ["A", "B", "D", "E"])
        self.assertEqual(p.block("D").release.checklist, "ejecucion/D/checklist.sh")
        self.assertEqual(p.block("E").human.kind, "login")

    def test_rechaza_todo_lo_que_paralizaria_despues(self):
        malo = """# x
plan: x
## Bloque A — sin pruebas
lane: gate
| Fila | Contenido | Acepta |
| A.1 | algo | - |
## Bloque B — humano raro
lane: fast
depende: Z
humano: aprobar el merge
## Bloque C — release sin scripts
lane: release
| C.1 | algo | true |
"""
        with self.assertRaises(PlanError) as cm:
            parse_plan(malo, min_implementadores=2, implementadores=1)
        texto = "\n".join(cm.exception.errores)
        for esperado in ("autopiloto: taller", "ninguna fila trae 'Acepta'", "prueba-real",
                         "depende de Z", "'humano: aprobar' no vale", "lane release sin 'despliegue:'",
                         "se exigen 2"):
            self.assertIn(esperado, texto)


class MachineTest(unittest.TestCase):
    def snap(self, eventos):
        return m.fold(parse_plan(PLAN), eventos)

    def test_no_hay_merge_sin_veredicto_y_ci_sobre_el_mismo_sha(self):
        base = [
            ev("AttemptStarted", "A", {"n": 1, "role": "implementar", "provider": "glm", "pid": 1, "dir": "d"}),
            ev("AttemptEnded", "A", {"n": 1, "role": "implementar", "outcome": {"kind": "entregado", "sha": "a" * 40}}),
            ev("Verified", "A", {"sha": "a" * 40, "ok": True}),
            ev("Reviewed", "A", {"sha": "a" * 40, "veredicto": "APROBADO", "bloqueantes": []}),
        ]
        s = self.snap(base)
        self.assertEqual(m.merge_gate(s.blocks["A"]), "CI no está en verde sobre ese SHA")
        # CI verde, pero sobre OTRO sha: sigue sin ticket
        s = self.snap(base + [ev("CiResult", "A", {"sha": "b" * 40, "green": True})])
        self.assertIsInstance(m.merge_gate(s.blocks["A"]), str)
        s = self.snap(base + [ev("CiResult", "A", {"sha": "a" * 40, "green": True})])
        self.assertEqual(m.merge_gate(s.blocks["A"]), m.MergeTicket("A", "a" * 40))

    def test_cambios_sin_repro_cuenta_como_aprobado_regla_4(self):
        s = self.snap([
            ev("AttemptStarted", "A", {"n": 1, "role": "implementar", "provider": "glm", "pid": 1, "dir": "d"}),
            ev("AttemptEnded", "A", {"n": 1, "role": "implementar", "outcome": {"kind": "entregado", "sha": "a" * 40}}),
            ev("Verified", "A", {"sha": "a" * 40, "ok": True}),
            ev("Reviewed", "A", {"sha": "a" * 40, "veredicto": "CAMBIOS", "bloqueantes": [{"texto": "estilo", "repro": ""}]}),
        ])
        self.assertIs(m.estado(s, "A"), m.Estado.CI)

    def test_cuota_releva_y_el_reloj_devuelve_al_proveedor(self):
        now = time.time()
        eventos = [
            ev("AttemptStarted", "A", {"n": 1, "role": "implementar", "provider": "muse", "pid": 1, "dir": "d"}),
            ev("AttemptEnded", "A", {"n": 1, "role": "implementar", "outcome": {"kind": "cuota"}}),
            ev("Cooldown", "", {"provider": "muse", "hasta": now + 3600}),
        ]
        acc = m.decide(self.snap(eventos), policy("muse", "glm"), now)
        inicio = [a for a in acc if isinstance(a, m.StartAttempt)]
        self.assertEqual(inicio[0].provider, "glm")
        # una hora después, muse vuelve a ser elegible sin que nadie "quite la pausa"
        acc = m.decide(self.snap(eventos), policy("muse", "glm"), now + 3601)
        self.assertEqual([a for a in acc if isinstance(a, m.StartAttempt)][0].provider, "muse")

    def test_todos_en_cuota_no_pausa_y_avisa_solo_si_tarda(self):
        now = time.time()
        eventos = [ev("Cooldown", "", {"provider": "glm", "hasta": now + 7 * 3600})]
        acc = m.decide(self.snap(eventos), policy("glm"), now)
        self.assertFalse([a for a in acc if isinstance(a, m.StartAttempt)])
        self.assertEqual([a.template for a in acc if isinstance(a, m.Notify)].count("lento"), 1)

    def test_revisor_sin_cuota_lo_releva_quien_no_implemento(self):
        now = time.time()
        eventos = [
            ev("AttemptStarted", "A", {"n": 1, "role": "implementar", "provider": "muse", "pid": 1, "dir": "d"}),
            ev("AttemptEnded", "A", {"n": 1, "role": "implementar", "outcome": {"kind": "cuota"}}),
            ev("AttemptStarted", "A", {"n": 2, "role": "implementar", "provider": "glm", "pid": 1, "dir": "d"}),
            ev("AttemptEnded", "A", {"n": 2, "role": "implementar", "outcome": {"kind": "entregado", "sha": "a" * 40}}),
            ev("Verified", "A", {"sha": "a" * 40, "ok": True}),
            ev("Cooldown", "", {"provider": "claude", "hasta": now + 3600}),
        ]
        acc = m.decide(self.snap(eventos), policy("muse", "glm", "kimi"), now)
        rev = [a for a in acc if isinstance(a, m.StartAttempt) and a.role == "revisar"]
        # claude sin cuota; muse y glm trabajaron en el bloque: revisa kimi
        self.assertEqual(rev[0].provider, "kimi")

    def test_no_existe_estado_fallado_ni_pausado(self):
        nombres = {e.name for e in m.Estado}
        self.assertFalse(nombres & {"FALLADO", "FAILED", "PAUSADO", "PAUSED"})


class ClassifyTest(unittest.TestCase):
    def test_reconoce_cuota_con_hora(self):
        c = m.classify(1, False, False, "429 Too Many Requests: weekly limit reached, resets at 2026-10-23T09:00:00Z")
        self.assertEqual((c["kind"], c["reset_at"]), ("cuota", "2026-10-23T09:00:00Z"))

    def test_auth_transitorio_falla(self):
        self.assertEqual(m.classify(1, False, False, "Error: not logged in")["kind"], "auth")
        self.assertEqual(m.classify(1, False, False, "upstream 503")["kind"], "transitorio")
        self.assertEqual(m.classify(2, False, False, "boom")["kind"], "falla")
        self.assertEqual(m.classify(0, True, False, "")["kind"], "tiempo")


class NotifyTest(unittest.TestCase):
    def test_todas_las_plantillas_pasan_el_filtro(self):
        ejemplos = {"n": "1", "total": "3", "aviso": "ya puedes bloquear búsquedas", "prueba": "pasó",
                    "que_hacer": "entra a Seller Central", "hasta": str(time.time() + 3600),
                    "bloque": "el despliegue"}
        for t in PLANTILLAS:
            render(t, ejemplos)

    def test_rechaza_sha_rutas_y_jerga(self):
        for malo in ("Listo 3eda9854", "lee /Users/dn/x.txt", "el commit quedó", "\n".join("x" * 9)):
            with self.assertRaises(AvisoInvalido):
                lint_plain(malo)


if __name__ == "__main__":
    unittest.main()
