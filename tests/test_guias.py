"""Las guías del encargo (A.1) y el anexo con su contenido completo (A.2).

`guias()` es pura: la exigencia del (carril, papel), el CLI y `StartAttempt.correccion`
(verdadero en un bloque de bug o tras una revisión no aprobada) deciden la lista.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from motor import effects as ef
from motor import machine as m
from motor import pstack as ps
from motor.conf import RepoConf, load_conf
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


def _conf_falsa(tmp: Path) -> Path:
    """Una conf mínima con copia de pstack: todas las guías que `guias()` puede pedir."""
    conf = tmp / "conf"
    (conf / "reglas").mkdir(parents=True)
    (conf / "reglas" / "encargo.md").write_text("Reglas del encargo.\n")
    (conf / "reglas" / "revisor.md").write_text("Reglas del revisor.\n")
    (conf / "papeles.toml").write_text(
        '[papeles]\nimplementar = "codigo"\nrevisar = "revisar"\n'
    )
    (conf / "modelos.toml").write_text(
        '[carriles]\ncodigo = ["sonnet"]\nrevisar = ["opus"]\n'
    )
    (conf / "rutas.toml").write_text(
        '[rutas.sonnet]\ncuenta = "anthropic"\ncli = "claude"\n'
        'argv = ["claude", "{prompt}"]\n'
        '[rutas.opus]\ncuenta = "anthropic"\ncli = "claude"\n'
        'argv = ["claude", "{prompt}"]\n'
    )
    sk = conf / "pstack" / "skills" / "poteto-mode"
    (sk / "playbooks").mkdir(parents=True)
    (sk / "SKILL.md").write_text("# poteto-mode\nmodo de trabajo\n")
    (sk / "playbooks" / "feature.md").write_text("# playbook feature\n")
    (sk / "playbooks" / "bug-fix.md").write_text("# playbook bug-fix\n")
    it = conf / "pstack" / "skills" / "interrogate"
    it.mkdir()
    (it / "SKILL.md").write_text("# interrogate\nrevisa en serio\n")
    arnes = conf / "pstack" / "docs" / "harness"
    arnes.mkdir(parents=True)
    (arnes / "claude-code.md").write_text("# adaptador de Claude Code\n")
    (conf / "pstack" / "ORIGEN").write_text("origen: prueba abc1234567\n")
    mp = conf / "pstack-motor"
    mp.mkdir()
    (mp / "motor.md").write_text("REGLA-DEL-MOTOR: no abras ni mergees PRs.\n")
    (mp / "claude.md").write_text("Mapa de claude.\n")
    celdas = "".join(
        f'[exigencia.{lane}.{papel}]\nskills = ["poteto-mode", "interrogate"]\n'
        "playbook = true\n"
        for lane in ("fast", "gate", "release")
        for papel in ("implementar", "revisar")
    )
    (conf / "pstack.toml").write_text(
        f'[pstack]\ncommit = "abc1234567"\nexigir = true\n{celdas}'
    )
    return conf


def _briefs(conf_dir: Path, tmp: Path) -> dict[str, tuple[str, dict]]:
    """El encargo completo (con anexo) de implementar y de revisar en gate, con feedback vacío."""
    conf = load_conf(conf_dir)
    fx = ef.Effects(
        SimpleNamespace(
            home=tmp / "home",
            conf=conf,
            ws=SimpleNamespace(rama=lambda p, b: f"motor/{p}/{b}"),
        )
    )
    cwd = tmp / "repo"
    cwd.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=cwd, check=True)
    plan = parse_plan(PLAN)
    block = plan.block("A")
    rc = RepoConf.parse('[repo]\nprincipal = "main"\n')
    briefs = {}
    for role in ("implementar", "revisar"):
        a = m.StartAttempt("A", role, "sonnet", 1, "a" * 40, (), False)
        d = tmp / f"intento-{role}"
        d.mkdir(exist_ok=True)
        info = fx._prepara_pstack(d, "gate", role, "claude", cwd, "s1", a.correccion)
        briefs[role] = (fx._encargo(plan, block, a, cwd, rc, info), info)
    return briefs


class TestEncargo(unittest.TestCase):
    """A.2: el encargo lleva las guías completas en «## Anexo: tus guías», al final."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pstack-encargo-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.conf_dir = _conf_falsa(self.tmp)
        self.briefs = _briefs(self.conf_dir, self.tmp)

    def test_la_entrega_va_antes_del_anexo(self):
        impl, _ = self.briefs["implementar"]
        rev, _ = self.briefs["revisar"]
        anexo_impl = impl.index("\n## Anexo: tus guías\n")
        anexo_rev = rev.index("\n## Anexo: tus guías\n")
        self.assertLess(impl.index("## Cómo se entrega"), anexo_impl)
        self.assertLess(rev.index("MOTOR_RESULT"), anexo_rev)

    def test_ningun_encargo_habla_de_cargar_ni_de_detenerse(self):
        for brief, _ in self.briefs.values():
            for prohibido in ("antes de tu acción", "ábrelo", "te detiene"):
                self.assertNotIn(prohibido, brief)

    def test_el_anexo_trae_cada_guia_completa_con_su_ruta(self):
        for brief, info in self.briefs.values():
            raiz = Path(info["raiz"])
            for g in info["guias"]:
                self.assertIn(f"### {g['ruta']}", brief)
                self.assertIn(str(raiz / g["ruta"]), brief)
                self.assertIn(
                    (raiz / g["ruta"]).read_text(encoding="utf-8").strip(), brief
                )
        _, info = self.briefs["implementar"]
        guardado = json.loads(
            (self.tmp / "intento-implementar" / "pstack.json").read_text(
                encoding="utf-8"
            )
        )
        raiz = Path(info["raiz"])
        self.assertEqual(
            guardado["guias"],
            [
                {"ruta": g, "bytes": (raiz / g).stat().st_size}
                for g in guias(GATE_REV, "claude", False)
            ],
        )

    def test_si_falta_una_guia_el_intento_no_se_lanza_y_el_error_la_nombra(self):
        os.remove(self.conf_dir / FEATURE)
        conf = load_conf(self.conf_dir)
        fx = ef.Effects(
            SimpleNamespace(
                home=self.tmp / "home2",
                conf=conf,
                ws=SimpleNamespace(rama=lambda p, b: f"motor/{p}/{b}"),
            )
        )
        d = self.tmp / "intento-roto"
        d.mkdir()
        with self.assertRaises(FileNotFoundError) as cm:
            fx._prepara_pstack(
                d, "gate", "implementar", "claude", self.tmp, "s2", False
            )
        self.assertIn(FEATURE, str(cm.exception))
        self.assertFalse((d / "pstack.json").exists())

    def test_con_la_conf_real_el_encargo_cabe_en_45_kb(self):
        real = os.environ.get("MOTOR_CONF_REAL")
        if not real:
            self.skipTest("sin MOTOR_CONF_REAL")
        tmp = Path(tempfile.mkdtemp(prefix="pstack-encargo-real-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for role, (brief, _) in _briefs(Path(real), tmp).items():
            kb = len(brief.encode("utf-8")) / 1024
            self.assertLess(
                len(brief.encode("utf-8")), 45 * 1024, f"{role}: {kb:.1f} KB"
            )


if __name__ == "__main__":
    unittest.main()
