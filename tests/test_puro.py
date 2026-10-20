"""Parte pura: plan, decisiones, configuración, avisos y piezas de Windows que no necesitan Windows."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from motor import machine as m
from motor.conf import ConfError, RepoConf, load_maquina
from motor.notify import PLANTILLAS, AvisoInvalido, lint_plain, render
from motor.plan import PlanError, parse_plan
from motor.plataforma.windows import flags_creacion, resolve_shim, tarea_xml, CREATE_BREAKAWAY_FROM_JOB
from motor.servicio import plist_launchd
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

## Bloque C — otro repo
lane: gate
repo: git@github.com:x/docs.git
prueba-real: true
| Fila | Contenido | Acepta |
| C.1 | algo | true |
"""


def ev(kind: str, block: str, data: dict, i=[0]) -> Event:
    i[0] += 1
    return Event(i[0], time.time(), "demo", block, kind, data)


def pol(impl=("sonnet", "glm"), rev=("opus", "k3")) -> m.Policy:
    cuenta = {"sonnet": "anthropic", "opus": "anthropic", "glm": "zai", "k3": "kimi"}
    return m.Policy(implementadores=impl, revisores=rev, cuenta=cuenta,
                    max_parallel={c: 2 for c in set(cuenta.values())})


def entregado(block="A", modelo="glm", sha="a" * 40, n=1):
    return [ev("AttemptStarted", block, {"n": n, "role": "implementar", "modelo": modelo, "proc": {}, "dir": "d"}),
            ev("AttemptEnded", block, {"n": n, "role": "implementar", "outcome": {"kind": "entregado", "sha": sha}})]


class PlanTest(unittest.TestCase):
    def test_multirepo(self):
        p = parse_plan(PLAN)
        self.assertEqual(p.block("A").repo, "git@github.com:x/app.git")
        self.assertEqual(p.block("C").repo, "git@github.com:x/docs.git")

    def test_rechaza_lo_que_paralizaria_despues(self):
        malo = "# x\nplan: x\n## Bloque A — a\nlane: gate\n| Fila | Contenido | Acepta |\n| A.1 | a | - |\n"
        with self.assertRaises(PlanError) as cm:
            parse_plan(malo, min_implementadores=2, implementadores=1)
        t = "\n".join(cm.exception.errores)
        for x in ("autopiloto: motor", "sin repo", "ninguna fila trae 'Acepta'", "prueba-real", "se exigen 2"):
            self.assertIn(x, t)


    def test_formato_de_la_skill_valida(self):
        """El plan que enseña skill/motor/SKILL.md, con su bloque humano, pasa la validación."""
        skill = (Path(__file__).parent.parent / "skill" / "motor" / "SKILL.md").read_text()
        self.assertIn("`lane: fast`", skill)
        p = parse_plan(PLAN + "\n## Bloque H — login\nlane: fast\nhumano: login entra a Seller Central\n"
                              "sonda-humano: test -f x\n")
        self.assertEqual(p.block("H").lane, "fast")


class MachineTest(unittest.TestCase):
    def snap(self, eventos):
        return m.fold(parse_plan(PLAN), eventos)

    def test_merge_exige_revision_listo_y_checks_sobre_el_mismo_sha(self):
        base = entregado() + [ev("Verified", "A", {"sha": "a" * 40, "ok": True}),
                              ev("PrOpened", "A", {"numero": 7}),
                              ev("Reviewed", "A", {"sha": "a" * 40, "veredicto": "APROBADO", "bloqueantes": []})]
        self.assertEqual(m.merge_gate(self.snap(base).blocks["A"]), "el PR no está marcado listo sobre ese SHA")
        base.append(ev("MarkedReady", "A", {"sha": "a" * 40}))
        s = self.snap(base + [ev("ChecksResult", "A", {"sha": "b" * 40, "green": True})])
        self.assertIsInstance(m.merge_gate(s.blocks["A"]), str)
        s = self.snap(base + [ev("ChecksResult", "A", {"sha": "a" * 40, "green": True})])
        self.assertEqual(m.merge_gate(s.blocks["A"]), m.MergeTicket("A", "a" * 40, 7))

    def test_la_cuota_es_por_cuenta(self):
        now = time.time()
        s = self.snap([ev("Cooldown", "", {"cuenta": "anthropic", "hasta": now + 3600})])
        inicio = [a for a in m.decide(s, pol(), now) if isinstance(a, m.StartAttempt)]
        self.assertEqual({a.modelo for a in inicio}, {"glm"}, "sonnet comparte cuenta con opus: ninguno entra")

    def test_revisor_relevo_sin_autores_ni_cuentas_en_cuota(self):
        now = time.time()
        eventos = entregado(modelo="glm") + [
            ev("Verified", "A", {"sha": "a" * 40, "ok": True}), ev("PrOpened", "A", {"numero": 1}),
            ev("Cooldown", "", {"cuenta": "anthropic", "hasta": now + 3600})]
        rev = [a for a in m.decide(self.snap(eventos), pol(), now) if isinstance(a, m.StartAttempt) and a.role == "revisar"]
        self.assertEqual(rev[0].modelo, "k3")   # opus en cuota (cuenta anthropic), glm es autor
        self.assertNotIn("glm", m.revisores_para(self.snap(eventos).blocks["A"], pol()))

    def test_un_corte_de_luz_no_cuenta_como_falla(self):
        now = time.time()
        eventos = []
        for n in (1, 2, 3):
            eventos += [ev("AttemptStarted", "A", {"n": n, "role": "implementar", "modelo": "sonnet", "proc": {}, "dir": "d"}),
                        ev("AttemptEnded", "A", {"n": n, "role": "implementar", "outcome": {"kind": "corte"}})]
        a = [x for x in m.decide(self.snap(eventos), pol(), now) if isinstance(x, m.StartAttempt) and x.block == "A"]
        self.assertEqual(a[0].modelo, "sonnet", "tres cortes no hacen que se releve al agente")
        self.assertIn("corte de luz", " ".join(a[0].feedback))

    def test_cambios_sin_repro_cuenta_como_aprobado(self):
        s = self.snap(entregado() + [ev("Verified", "A", {"sha": "a" * 40, "ok": True}), ev("PrOpened", "A", {"numero": 1}),
                                     ev("Reviewed", "A", {"sha": "a" * 40, "veredicto": "CAMBIOS",
                                                          "bloqueantes": [{"texto": "estilo", "repro": ""}]})])
        self.assertIs(m.estado(s, "A"), m.Estado.LISTO)

    def test_no_existe_fallado_ni_pausado(self):
        self.assertFalse({e.name for e in m.Estado} & {"FALLADO", "FAILED", "PAUSADO", "PAUSED"})

    def test_clasifica(self):
        c = m.classify(1, False, False, "429 weekly limit reached, resets at 2026-10-23T09:00:00Z")
        self.assertEqual((c["kind"], c["reset_at"]), ("cuota", "2026-10-23T09:00:00Z"))
        self.assertEqual(m.classify(1, False, False, "Your macOS login keychain is locked")["kind"], "auth")


class ConfTest(unittest.TestCase):
    def test_maquina_no_puede_traer_papeles_ni_modelos(self):
        with tempfile.TemporaryDirectory() as t:
            h = Path(t)
            (h / "maquina.toml").write_text('nombre = "Mini"\nconf = "/x"\n[carriles]\ncodigo = ["glm"]\n')
            with self.assertRaises(ConfError) as cm:
                load_maquina(h)
            self.assertIn("conf compartida", str(cm.exception))

    def test_secretos_deben_ser_privados(self):
        with tempfile.TemporaryDirectory() as t:
            h = Path(t)
            (h / "maquina.toml").write_text('nombre = "Mini"\nconf = "/x"\n')
            (h / "secretos.env").write_text("X=1\n")
            os.chmod(h / "secretos.env", 0o644)
            with self.assertRaises(ConfError):
                load_maquina(h)
            os.chmod(h / "secretos.env", 0o600)
            self.assertEqual(dict(load_maquina(h).secretos), {"X": "1"})

    def test_repo_conf(self):
        rc = RepoConf.parse('[repo]\nprincipal="master"\n[github]\nchecks_extra=["completa"]\n[deploy]\ndesplegar="d.sh"\n')
        self.assertEqual((rc.principal, rc.checks_extra, rc.desplegar), ("master", ("completa",), "d.sh"))


class AvisosTest(unittest.TestCase):
    def test_plantillas_con_maquina_y_filtro(self):
        campos = {"n": "1", "total": "3", "aviso": "ya puedes bloquear búsquedas", "prueba": "pasó",
                  "que_hacer": "entra a Seller Central", "hasta": str(time.time() + 3600), "bloque": "el despliegue"}
        for t in PLANTILLAS:
            texto = render(t, campos, "Mini", "Reporte de ventas")
            self.assertTrue(texto.startswith("En la Mini · Reporte de ventas:"))

    def test_jerga_del_plan_cae_a_texto_generico(self):
        texto = render("entregado", {"n": "1", "total": "1", "aviso": "commit 3eda9854 listo", "prueba": "pasó"}, "PC", "x")
        self.assertNotIn("3eda9854", texto)

    def test_rechaza(self):
        for malo in ("Listo 3eda9854", "lee /Users/dn/x.txt", "el commit quedó", "\n".join("x" * 9)):
            with self.assertRaises(AvisoInvalido):
                lint_plain(malo)


class ServicioTest(unittest.TestCase):
    def test_windows_resuelve_shims_cmd(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "claude.cmd").write_text("@echo off")
            argv = resolve_shim("claude", t)
            self.assertEqual(argv[1:4], ["/d", "/s", "/c"])
            self.assertTrue(argv[-1].endswith("claude.cmd"))

    def test_windows_grupo_propio_fuera_del_job(self):
        self.assertTrue(flags_creacion() & CREATE_BREAKAWAY_FROM_JOB)

    def test_tarea_arranca_sin_sesion(self):
        x = tarea_xml("ehven", r"C:\uv\python.exe", r"C:\Users\ehven\.motor")
        for esperado in ("<BootTrigger>", "<LogonType>Password</LogonType>", "<ExecutionTimeLimit>PT0S", "motor servicio"):
            self.assertIn(esperado, x)

    def test_launchd_arranca_sin_sesion(self):
        p = plist_launchd("gon", "/usr/bin/python3", "/Users/gon/.motor", "/Users/gon/motor")
        for esperado in ("<key>UserName</key><string>gon</string>", "<key>KeepAlive</key><true/>", "LaunchDaemons"):
            self.assertIn(esperado, p)


if __name__ == "__main__":
    unittest.main()
