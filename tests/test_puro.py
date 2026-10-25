"""Parte pura: plan, decisiones, configuración, avisos y piezas de Windows que no necesitan Windows."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from motor import machine as m
from motor.conf import ConfError, RepoConf, load_maquina
from motor.__main__ import conf_valida, doctor
from motor.diagnostico import diagnostico
from motor.github import veredicto
from motor.notify import PLANTILLAS, AvisoInvalido, lint_plain, render
from motor.plan import PlanError, _celdas, parse_plan
from motor.plataforma.windows import (
    flags_creacion,
    resolve_shim,
    tarea_xml,
    CREATE_BREAKAWAY_FROM_JOB,
)
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
    return m.Policy(
        implementadores=impl,
        revisores=rev,
        cuenta=cuenta,
        max_parallel={c: 2 for c in set(cuenta.values())},
    )


def entregado(block="A", modelo="glm", sha="a" * 40, n=1):
    return [
        ev(
            "AttemptStarted",
            block,
            {"n": n, "role": "implementar", "modelo": modelo, "proc": {}, "dir": "d"},
        ),
        ev(
            "AttemptEnded",
            block,
            {
                "n": n,
                "role": "implementar",
                "outcome": {"kind": "entregado", "sha": sha},
            },
        ),
    ]


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
        for x in (
            "autopiloto: motor",
            "sin repo",
            "ninguna fila trae 'Acepta'",
            "prueba-real",
            "se exigen 2",
        ):
            self.assertIn(x, t)

    def test_formato_de_la_skill_valida(self):
        """El plan que enseña skill/motor/SKILL.md, con su bloque humano, pasa la validación."""
        skill = (
            Path(__file__).parent.parent / "skill" / "motor" / "SKILL.md"
        ).read_text()
        self.assertIn("`lane: fast`", skill)
        p = parse_plan(
            PLAN
            + "\n## Bloque H — login\nlane: fast\nhumano: login entra a Seller Central\n"
            "sonda-humano: test -f x\n"
        )
        self.assertEqual(p.block("H").lane, "fast")


class MachineTest(unittest.TestCase):
    def snap(self, eventos):
        return m.fold(parse_plan(PLAN), eventos)

    def test_merge_exige_revision_listo_y_checks_sobre_el_mismo_sha(self):
        base = entregado() + [
            ev("Verified", "A", {"sha": "a" * 40, "ok": True}),
            ev("PrOpened", "A", {"numero": 7}),
            ev(
                "Reviewed",
                "A",
                {"sha": "a" * 40, "veredicto": "APROBADO", "bloqueantes": []},
            ),
        ]
        self.assertEqual(
            m.merge_gate(self.snap(base).blocks["A"]),
            "el PR no está marcado listo sobre ese SHA",
        )
        base.append(ev("MarkedReady", "A", {"sha": "a" * 40}))
        s = self.snap(
            base + [ev("ChecksResult", "A", {"sha": "b" * 40, "green": True})]
        )
        self.assertIsInstance(m.merge_gate(s.blocks["A"]), str)
        s = self.snap(
            base + [ev("ChecksResult", "A", {"sha": "a" * 40, "green": True})]
        )
        self.assertEqual(m.merge_gate(s.blocks["A"]), m.MergeTicket("A", "a" * 40, 7))

    def test_la_cuota_es_por_cuenta(self):
        now = time.time()
        s = self.snap(
            [ev("Cooldown", "", {"cuenta": "anthropic", "hasta": now + 3600})]
        )
        inicio = [a for a in m.decide(s, pol(), now) if isinstance(a, m.StartAttempt)]
        self.assertEqual(
            {a.modelo for a in inicio},
            {"glm"},
            "sonnet comparte cuenta con opus: ninguno entra",
        )

    def test_revisor_relevo_sin_autores_ni_cuentas_en_cuota(self):
        now = time.time()
        eventos = entregado(modelo="glm") + [
            ev("Verified", "A", {"sha": "a" * 40, "ok": True}),
            ev("PrOpened", "A", {"numero": 1}),
            ev("Cooldown", "", {"cuenta": "anthropic", "hasta": now + 3600}),
        ]
        acciones = m.decide(self.snap(eventos), pol(), now)
        rev = [a for a in acciones if isinstance(a, m.StartAttempt) and a.role == "revisar"]
        self.assertEqual(rev, [])
        self.assertTrue(any(isinstance(a, m.MarkReady) for a in acciones))
        self.assertNotIn("glm", m.revisores_para(self.snap(eventos).blocks["A"], pol()))

    def test_un_corte_de_luz_no_cuenta_como_falla(self):
        now = time.time()
        eventos = []
        for n in (1, 2, 3):
            eventos += [
                ev(
                    "AttemptStarted",
                    "A",
                    {
                        "n": n,
                        "role": "implementar",
                        "modelo": "sonnet",
                        "proc": {},
                        "dir": "d",
                    },
                ),
                ev(
                    "AttemptEnded",
                    "A",
                    {"n": n, "role": "implementar", "outcome": {"kind": "corte"}},
                ),
            ]
        a = [
            x
            for x in m.decide(self.snap(eventos), pol(), now)
            if isinstance(x, m.StartAttempt) and x.block == "A"
        ]
        self.assertEqual(
            a[0].modelo, "sonnet", "tres cortes no hacen que se releve al agente"
        )
        self.assertIn("corte de luz", " ".join(a[0].feedback))

    def test_cambios_sin_repro_cuenta_como_aprobado(self):
        s = self.snap(
            entregado()
            + [
                ev("Verified", "A", {"sha": "a" * 40, "ok": True}),
                ev("PrOpened", "A", {"numero": 1}),
                ev(
                    "Reviewed",
                    "A",
                    {
                        "sha": "a" * 40,
                        "veredicto": "CAMBIOS",
                        "bloqueantes": [{"texto": "estilo", "repro": ""}],
                    },
                ),
            ]
        )
        self.assertIs(m.estado(s, "A"), m.Estado.LISTO)

    def test_no_existe_fallado_ni_pausado(self):
        self.assertFalse(
            {e.name for e in m.Estado} & {"FALLADO", "FAILED", "PAUSADO", "PAUSED"}
        )

    def test_clasifica(self):
        c = m.classify(
            1, False, False, "429 weekly limit reached, resets at 2026-10-23T09:00:00Z"
        )
        self.assertEqual((c["kind"], c["reset_at"]), ("cuota", "2026-10-23T09:00:00Z"))
        self.assertEqual(
            m.classify(1, False, False, "Your macOS login keychain is locked")["kind"],
            "auth",
        )


class AutoresTest(unittest.TestCase):
    """Un intento que no llegó a tocar el código no saca a su modelo de la revisión."""

    def vista(self, *intentos: tuple[str, dict]) -> m.BlockView:
        evs = []
        for n, (modelo, fin) in enumerate(intentos, 1):
            evs.append(
                ev(
                    "AttemptStarted",
                    "A",
                    {
                        "n": n,
                        "role": "implementar",
                        "modelo": modelo,
                        "proc": {},
                        "dir": "d",
                    },
                )
            )
            if fin is not None:
                evs.append(
                    ev(
                        "AttemptEnded",
                        "A",
                        {"n": n, "role": "implementar", "outcome": fin},
                    )
                )
        return m.fold(parse_plan(PLAN), evs).blocks["A"]

    def test_el_caso_de_motor_03_b_deja_revisor_y_no_es_opus(self):
        """Sonnet, k3 y grok terminaron en segundos sin tocar nada; glm entregó; opus está agotado y el
        plan no trae `revisa:`. Antes todos eran autores y no quedaba nadie que revisara."""
        now = time.time()
        sin_tocar = {"kind": "falla", "toco_carpeta": False}
        evs = []
        for n, mo in enumerate(("sonnet", "k3", "grok"), 1):
            evs += [
                ev(
                    "AttemptStarted",
                    "A",
                    {
                        "n": n,
                        "role": "implementar",
                        "modelo": mo,
                        "proc": {},
                        "dir": "d",
                    },
                ),
                ev(
                    "AttemptEnded",
                    "A",
                    {"n": n, "role": "implementar", "outcome": sin_tocar},
                ),
            ]
        evs += entregado(modelo="glm", n=4)
        evs += [
            ev("Verified", "A", {"sha": "a" * 40, "ok": True}),
            ev("PrOpened", "A", {"numero": 1}),
            ev("Cooldown", "", {"cuenta": "anthropic", "hasta": now + 3600}),
        ]
        p = m.Policy(
            implementadores=("sonnet", "k3", "glm", "grok"),
            revisores=("opus", "k3", "glm", "grok"),
            cuenta={
                "sonnet": "anthropic",
                "opus": "anthropic",
                "k3": "kimi",
                "glm": "zai",
                "grok": "xai",
            },
            max_parallel={c: 4 for c in ("anthropic", "kimi", "zai", "xai")},
            agentes_por_maquina=4,
        )
        snap = m.fold(parse_plan(PLAN), evs)
        self.assertEqual(snap.blocks["A"].autores(), {"glm"})
        revisando = [
            a.modelo
            for a in m.decide(snap, p, now)
            if isinstance(a, m.StartAttempt) and a.role == "revisar"
        ]
        self.assertEqual(revisando, [])
        self.assertTrue(any(isinstance(a, m.MarkReady) for a in m.decide(snap, p, now)))

    def test_el_que_toco_la_carpeta_y_se_corto_sigue_siendo_autor(self):
        bv = self.vista(("glm", {"kind": "tiempo", "toco_carpeta": True}))
        self.assertEqual(bv.autores(), {"glm"})

    def test_el_que_entrego_sin_tocar_esa_carpeta_sigue_siendo_autor(self):
        bv = self.vista(
            ("glm", {"kind": "entregado", "sha": "a" * 40, "toco_carpeta": False})
        )
        self.assertEqual(bv.autores(), {"glm"})

    def test_un_resultado_viejo_sin_el_campo_sigue_siendo_autor(self):
        bv = self.vista(("glm", {"kind": "tiempo"}))
        self.assertEqual(bv.autores(), {"glm"})

    def test_el_que_sigue_corriendo_es_autor(self):
        self.assertEqual(self.vista(("glm", None)).autores(), {"glm"})


class PedidoDeModelosTest(unittest.TestCase):
    """David: "haz este plan con DeepSeek como revisor y Qwen como implementer" (2026-10-20)."""

    PEDIDO = PLAN.replace(
        "repo: git@github.com:x/app.git\n",
        "repo: git@github.com:x/app.git\nimplementa: Qwen\nrevisa: DeepSeek\n",
        1,
    )

    def pol(self):
        cuenta = {
            "sonnet": "anthropic",
            "opus": "anthropic",
            "glm": "zai",
            "k3": "kimi",
            "qwen": "qwen",
            "deepseek": "deepseek",
        }
        return m.Policy(
            implementadores=("sonnet", "glm"),
            revisores=("opus", "k3"),
            cuenta=cuenta,
            max_parallel={c: 2 for c in set(cuenta.values())},
        )

    def test_lee_el_pedido_sin_importar_mayusculas(self):
        p = parse_plan(self.PEDIDO, modelos={"qwen", "deepseek", "sonnet"})
        self.assertEqual((p.implementa, p.revisa), (("qwen",), ("deepseek",)))
        self.assertEqual(
            type(p).from_dict(p.to_dict()), p, "el pedido sobrevive al registro"
        )

    def test_modelo_desconocido_se_rechaza_con_la_lista(self):
        with self.assertRaises(PlanError) as cm:
            parse_plan(self.PEDIDO, modelos={"sonnet", "opus"})
        self.assertIn(
            "no conozco el modelo qwen, deepseek; los que hay: opus, sonnet",
            str(cm.exception),
        )

    def test_nadie_revisa_su_propio_trabajo(self):
        mismo = self.PEDIDO.replace("revisa: DeepSeek", "revisa: qwen")
        with self.assertRaises(PlanError) as cm:
            parse_plan(mismo)
        self.assertIn("nadie revisa su propio trabajo", str(cm.exception))

    def test_un_solo_implementador_pedido_no_choca_con_el_minimo(self):
        parse_plan(self.PEDIDO, min_implementadores=2, implementadores=1)

    def test_el_motor_usa_lo_pedido_y_no_la_tabla(self):
        now = time.time()
        p = parse_plan(self.PEDIDO)
        inicio = [
            a
            for a in m.decide(m.fold(p, []), self.pol(), now)
            if isinstance(a, m.StartAttempt)
        ]
        self.assertEqual({a.modelo for a in inicio}, {"qwen"})
        eventos = entregado(modelo="qwen") + [
            ev("Verified", "A", {"sha": "a" * 40, "ok": True}),
            ev("PrOpened", "A", {"numero": 1}),
        ]
        rev = [
            a
            for a in m.decide(m.fold(p, eventos), self.pol(), now)
            if isinstance(a, m.StartAttempt) and a.role == "revisar"
        ]
        self.assertEqual(rev[0].modelo, "deepseek")
        sin_cuota = eventos + [
            ev("Cooldown", "", {"cuenta": "deepseek", "hasta": now + 3600})
        ]
        rev = [
            a
            for a in m.decide(m.fold(p, sin_cuota), self.pol(), now)
            if isinstance(a, m.StartAttempt) and a.role == "revisar"
        ]
        self.assertEqual(
            rev,
            [],
            "si DeepSeek no tiene cuota se espera; no entra otro que David no pidió",
        )


class RevisionGrokTest(unittest.TestCase):
    """Hallazgos de la revisión de Grok a MOTOR 01 (2026-10-20)."""

    PASOS = (
        PLAN
        + """
## Bloque F — instalar
lane: gate
prueba-real: true
| Fila | Contenido | Acepta |
| F.1 | instalar | true |

## Bloque E — llaves
lane: fast
depende: F
humano: login pega las llaves
"""
    )

    def test_no_pide_el_paso_humano_antes_de_sus_dependencias(self):
        s = m.fold(parse_plan(self.PASOS), [])
        self.assertIs(m.estado(s, "E"), m.Estado.ESPERANDO)
        avisos = [
            a
            for a in m.decide(s, pol(), time.time())
            if isinstance(a, m.Notify) and a.template == "humano"
        ]
        self.assertEqual(
            avisos, [], "a David no se le pide nada que todavía no puede hacer"
        )

    def test_pipe_escapado_queda_dentro_de_la_celda(self):
        self.assertEqual(
            _celdas(
                r"| G.4 | cerrar | gh pr view 37 --json state -q .state \| grep -q CLOSED |"
            ),
            ["G.4", "cerrar", "gh pr view 37 --json state -q .state | grep -q CLOSED"],
        )

    def test_checks_por_bloque(self):
        p = parse_plan(
            PLAN.replace(
                "lane: gate\nprueba-real: true\n| Fila",
                "lane: gate\nchecks: espejo-de-g, gate\nprueba-real: true\n| Fila",
                1,
            )
        )
        self.assertEqual(p.block("A").checks, ("espejo-de-g", "gate"))
        self.assertEqual(
            type(p).from_dict(p.to_dict()).block("A").checks, ("espejo-de-g", "gate")
        )

    def test_check_en_matriz(self):
        corre = [
            {
                "name": f"completa ({i}, 3)",
                "status": "completed",
                "conclusion": "success",
            }
            for i in (1, 2)
        ]
        corre.append(
            {"name": "completa (3, 3)", "status": "in_progress", "conclusion": None}
        )
        v = veredicto("s", ("completa",), corre)
        self.assertEqual((v.terminal, v.faltan), (False, ("completa",)))
        corre[-1] = {
            "name": "completa (3, 3)",
            "status": "completed",
            "conclusion": "failure",
        }
        self.assertEqual(veredicto("s", ("completa",), corre).fallas, ("completa",))
        corre[-1]["conclusion"] = "success"
        self.assertTrue(veredicto("s", ("completa",), corre).green)
        self.assertEqual(
            veredicto("s", ("comp",), corre).faltan,
            ("comp",),
            "un prefijo suelto no cuenta",
        )
        saltado = [{"name": "completa", "status": "completed", "conclusion": "skipped"}]
        self.assertEqual(
            veredicto("s", ("completa",), saltado).faltan,
            ("completa",),
            "saltado en borrador no es aprobado (MOTOR 01, PR #484)",
        )

    def test_saltado_a_proposito_tras_listo_es_aprobado(self):
        """Orbit PR #487 (INGESTA 01): listo a las 19:37:40; `completa` se saltó en borrador y otra
        vez ya listo, porque no había código. El motor esperó horas una prueba que nunca iba a correr."""

        def run(nombre, conclusion, hora):
            return {
                "name": nombre,
                "status": "completed",
                "conclusion": conclusion,
                "completed_at": f"2026-10-21T{hora}Z",
            }

        req = ("gate", "reconcile", "completa")
        borrador = [
            run("gate", "success", "19:37:23"),
            run("reconcile", "success", "19:36:30"),
            run("completa", "skipped", "19:36:20"),
        ]
        listo = "2026-10-21T19:37:40Z"
        self.assertEqual(
            veredicto("s", req, borrador, listo).faltan,
            ("completa",),
            "el salto del borrador no cuenta: falta la corrida de ya listo",
        )
        self.assertEqual(
            veredicto("s", req, borrador, None).faltan,
            ("completa",),
            "en borrador ningún salto cuenta",
        )
        mismo_segundo = [*borrador, run("completa", "skipped", "19:37:40")]
        self.assertEqual(
            veredicto("s", req, mismo_segundo, listo).faltan,
            ("completa",),
            "en el mismo segundo no se sabe si fue antes o después de listo",
        )
        tras_listo = [*borrador, run("completa", "skipped", "19:37:51")]
        self.assertTrue(veredicto("s", req, tras_listo, listo).green)
        corre = [*borrador, run("completa", "success", "19:45:00")]
        self.assertTrue(veredicto("s", req, corre, listo).green)
        corre[-1]["conclusion"] = "failure"
        self.assertEqual(veredicto("s", req, corre, listo).fallas, ("completa",))
        self.assertTrue(
            veredicto("s", req, borrador, "").green,
            "nunca fue borrador: el salto cuenta",
        )

    def test_checks_que_nunca_terminan_avisan_una_vez(self):
        p, now = parse_plan(PLAN), time.time()
        base = entregado() + [
            ev("Verified", "A", {"sha": "a" * 40, "ok": True}),
            ev("PrOpened", "A", {"numero": 1}),
            ev(
                "Reviewed",
                "A",
                {"sha": "a" * 40, "veredicto": "APROBADO", "bloqueantes": []},
            ),
            ev("MarkedReady", "A", {"sha": "a" * 40}),
        ]
        base[-1] = Event(
            base[-1].id, now - 7 * 3600, "demo", "A", "MarkedReady", {"sha": "a" * 40}
        )
        acc = [
            a for a in m.decide(m.fold(p, base), pol(), now) if isinstance(a, m.Notify)
        ]
        self.assertIn("atorado:A:checks:" + "a" * 12, [a.key for a in acc])

    def _en_checks(self, now, listo_hace=600):
        base = entregado() + [
            ev("Verified", "A", {"sha": "a" * 40, "ok": True}),
            ev("PrOpened", "A", {"numero": 1}),
            ev(
                "Reviewed",
                "A",
                {"sha": "a" * 40, "veredicto": "APROBADO", "bloqueantes": []},
            ),
        ]
        return base + [
            Event(900, now - listo_hace, "demo", "A", "MarkedReady", {"sha": "a" * 40})
        ]

    def test_github_terminado_y_esperando_una_prueba_avisa_con_el_motivo(self):
        """INGESTA 01: GitHub había terminado todo y el motor esperó horas una prueba saltada sin
        decir nada; el aviso de cada hora solo decía "espero las pruebas automáticas"."""
        p, now = parse_plan(PLAN), time.time()

        def espera(hace, termino=True):
            return Event(
                901,
                now - hace,
                "demo",
                "A",
                "ChecksEsperando",
                {"sha": "a" * 40, "faltan": ["completa"], "github_termino": termino},
            )

        def avisos(evs):
            return [
                a
                for a in m.decide(m.fold(p, evs), pol(), now)
                if isinstance(a, m.Notify) and a.template == "atorado"
            ]

        self.assertEqual(avisos(self._en_checks(now) + [espera(10 * 60)]), [])
        self.assertEqual(
            avisos(self._en_checks(now, 3600) + [espera(40 * 60, termino=False)]),
            [],
            "GitHub todavía corre algo: es espera, no atasco",
        )
        (a,) = avisos(self._en_checks(now, 3600) + [espera(40 * 60)])
        motivo = dict(a.campos)["motivo"]
        self.assertIn("«completa»", motivo)
        self.assertIn("GitHub ya terminó", motivo)
        texto = render("atorado", dict(a.campos), "Mini", "INGESTA 01")
        self.assertIn("Motivo: GitHub ya terminó", texto)
        ya = self._en_checks(now, 3600) + [
            espera(40 * 60),
            ev("NoticeQueued", "A", {"key": a.key}),
        ]
        self.assertEqual(avisos(ya), [], "una vez")
        snap = m.fold(p, self._en_checks(now, 3 * 3600) + [espera(10 * 60)])
        self.assertIn(
            "espero la prueba «completa» de GitHub desde hace 3 h",
            diagnostico(snap, "A", pol(), now).motivo,
        )

    def test_la_misma_falla_tres_veces_avisa_una_vez(self):
        """INGESTA 01, bloque S: la prueba real abortaba por la hora y el bloque rebotaba entre
        agentes sin que nadie se enterara."""
        p, now = parse_plan(PLAN), time.time()

        def rondas(n):
            evs = []
            for i in range(n):
                sha = f"{i:x}" * 40
                evs += entregado(sha=sha[:40], n=i + 1) + [
                    ev("Merged", "A", {"sha": sha[:40], "commit": "c"}),
                    ev(
                        "Proved",
                        "A",
                        {"sha": sha[:40], "ok": False, "detalle": "ABORTA"},
                    ),
                ]
            return evs

        def avisos(evs):
            return [
                a
                for a in m.decide(m.fold(p, evs), pol(), now)
                if isinstance(a, m.Notify) and a.template == "atorado"
            ]

        self.assertEqual(avisos(rondas(2)), [])
        (a,) = avisos(rondas(3))
        self.assertEqual(
            dict(a.campos)["motivo"],
            "falló la prueba con datos reales, 3 veces seguidas",
        )
        self.assertEqual(
            avisos(rondas(4) + [ev("NoticeQueued", "A", {"key": a.key})]),
            [],
            "un aviso por racha, no uno por ronda",
        )

    def test_repetida_deja_de_ser_atasco_con_un_arreglo_en_camino(self):
        """INGESTA 01: el resumen seguía diciendo "atorado" mientras el revisor ya veía el arreglo."""
        p, now = parse_plan(PLAN), time.time()
        evs = []
        for i in range(3):
            sha = f"{i + 1:x}" * 40
            evs += entregado(sha=sha, n=i + 1) + [
                ev("Merged", "A", {"sha": sha, "commit": "c"}),
                ev("Proved", "A", {"sha": sha, "ok": False, "detalle": "x"}),
            ]
        snap = m.fold(p, evs)
        self.assertTrue(diagnostico(snap, "A", pol(), now).atorado)
        snap = m.fold(p, evs + entregado(sha="f" * 40, n=4))
        self.assertFalse(diagnostico(snap, "A", pol(), now).atorado)

    def test_doctor_busca_en_path_extra_de_la_maquina(self):
        with tempfile.TemporaryDirectory() as t:
            h, binario = Path(t), Path(t) / "bin"
            binario.mkdir()
            ejecutable = binario / ("claude.cmd" if os.name == "nt" else "claude")
            ejecutable.write_text("@echo off\r\n" if os.name == "nt" else "#!/bin/sh\n")
            ejecutable.chmod(0o755)
            conf = Path(__file__).resolve().parent.parent / "conf-ejemplo"
            # Rutas entre comillas simples (literales): en Windows el `\` no es escape de TOML.
            (h / "maquina.toml").write_text(
                f"nombre = 'PC'\nconf = '{conf}'\npath_extra = ['{binario}']\n"
            )
            viejo = os.environ.get("PATH", "")
            os.environ["PATH"] = os.pathsep.join(  # git sí; los CLIs de agentes no
                [str(Path(shutil.which("git")).parent)]
                if os.name == "nt"
                else ["/usr/bin", "/bin"]
            )
            try:
                r = doctor(h, programadas_sistema=False)
            finally:
                os.environ["PATH"] = viejo
            self.assertTrue(r["rutas"]["sonnet"]["ejecutable"])
            self.assertFalse(r["rutas"]["k3"]["ejecutable"])


class AvanceCadaHoraTest(unittest.TestCase):
    """David: "prefiero cada hora" (2026-10-20). Si el resumen deja de llegar, algo se cayó."""

    def eventos(self, horas: float, now: float):
        t0 = now - horas * 3600
        return [
            Event(1, t0, "demo", "", "PlanLoaded", {}),
            Event(
                2,
                now - 40 * 60,
                "demo",
                "A",
                "AttemptStarted",
                {
                    "n": 1,
                    "role": "implementar",
                    "modelo": "sonnet",
                    "proc": {},
                    "dir": "d",
                },
            ),
        ]

    def avisos(self, evs, now):
        return [
            a
            for a in m.decide(m.fold(parse_plan(PLAN), evs), pol(), now)
            if isinstance(a, m.Notify) and a.template == "avance"
        ]

    def test_un_resumen_por_hora_con_lo_que_se_hace(self):
        now = time.time()
        self.assertEqual(
            self.avisos(self.eventos(0.5, now), now),
            [],
            "antes de la primera hora, nada",
        )
        a = self.avisos(self.eventos(2.2, now), now)
        self.assertEqual([x.key for x in a], ["avance:2"])
        texto = render("avance", dict(a[0].campos), "Mac", "Motor de planes")
        self.assertIn("0 de 2 partes listas.", texto)
        self.assertIn("Ahora trabajo en «uno» (Sonnet, 40 min);", texto)
        self.assertIn(
            "«otro repo»: toca hacerla.", texto, "también lo que espera turno"
        )
        self.assertIn("Nada atorado.", texto)
        ya = self.eventos(2.2, now) + [
            Event(3, now, "demo", "", "NoticeQueued", {"key": "avance:2", "texto": "x"})
        ]
        self.assertEqual(self.avisos(ya, now), [], "una vez por hora")

    def test_plan_terminado_no_manda_resumen(self):
        now = time.time()
        p = parse_plan(PLAN)
        s = m.fold(
            p,
            self.eventos(3, now)
            + [
                Event(
                    3,
                    now,
                    "demo",
                    "A",
                    "AttemptEnded",
                    {
                        "n": 1,
                        "role": "implementar",
                        "outcome": {"kind": "entregado", "sha": "a" * 40},
                    },
                )
            ],
        )
        for bv in s.blocks.values():
            bv.merged.append({"sha": "a" * 40, "commit": "c"})
            bv.proved["a" * 40] = {"ok": True}
        self.assertFalse(
            [
                a
                for a in m.decide(s, pol(), now)
                if isinstance(a, m.Notify) and a.template == "avance"
            ]
        )

    def test_el_resumen_no_se_pierde_por_un_titulo_con_jerga(self):
        texto = render(
            "avance",
            {
                "listas": "3",
                "total": "9",
                "ahora": "Ahora hago rebase del commit",
                "atoron": "",
            },
            "Mini",
            "Plan",
        )
        self.assertIn("3 de 9 partes listas. Sigo trabajando.", texto)


class TopesPorMaquinaTest(unittest.TestCase):
    """David (2026-10-21): máximo 4 agentes por máquina y 2 por cuenta, sumando TODOS los planes."""

    def inicios(self, externos, **kw):
        pol_ = m.Policy(
            implementadores=("sonnet", "glm"),
            revisores=("opus", "k3"),
            cuenta={
                "sonnet": "anthropic",
                "opus": "anthropic",
                "glm": "zai",
                "k3": "kimi",
            },
            max_parallel={"anthropic": 1, "zai": 1, "kimi": 1},
            **kw,
        )
        acc = m.decide(m.fold(parse_plan(PLAN), []), pol_, time.time(), externos)
        return [a for a in acc if isinstance(a, m.StartAttempt)], [
            a for a in acc if isinstance(a, m.Notify)
        ]

    def test_otro_plan_ocupa_la_cuenta(self):
        inicio, _ = self.inicios({"anthropic": 1})
        self.assertNotIn(
            "sonnet",
            {a.modelo for a in inicio},
            "Claude ya trabaja en otro plan de esta máquina",
        )

    def test_tope_total_de_la_maquina(self):
        inicio, avisos = self.inicios({"kimi": 1, "zai": 1}, agentes_por_maquina=2)
        self.assertEqual(inicio, [], "la máquina ya tiene su tope: nadie más arranca")
        self.assertEqual(
            [a for a in avisos if a.template in ("atorado", "lento")],
            [],
            "esperar turno no es atoro",
        )
        inicio, _ = self.inicios({"kimi": 1}, agentes_por_maquina=2)
        self.assertEqual(len(inicio), 1, "con un lugar libre arranca uno solo")


class RevisionGrokModelosTest(unittest.TestCase):
    """Hallazgos de la revisión de Grok a la elección de modelos (2026-10-21), con sus escenarios."""

    CUENTA = {
        "sonnet": "anthropic",
        "opus": "anthropic",
        "k3": "kimi",
        "glm": "zai",
        "grok": "xai",
        "muse": "meta",
        "deepseek": "deepseek",
        "qwen": "qwen",
    }

    def pol(self, **kw):
        return m.Policy(
            implementadores=("sonnet", "k3", "glm", "grok"),
            revisores=("opus", "k3", "glm", "grok"),
            cuenta=self.CUENTA,
            max_parallel={c: 4 for c in set(self.CUENTA.values())},
            agentes_por_maquina=4,
            **kw,
        )

    def intento(self, i, modelo, kind, ts, role="implementar", block="A", sha=""):
        return [
            Event(
                i,
                ts - 1,
                "demo",
                block,
                "AttemptStarted",
                {
                    "n": i,
                    "role": role,
                    "modelo": modelo,
                    "proc": {},
                    "dir": "d",
                    "sha": sha,
                },
            ),
            Event(
                i + 1000,
                ts,
                "demo",
                block,
                "AttemptEnded",
                {
                    "n": i,
                    "role": role,
                    "outcome": {"kind": kind, "sha": "a" * 40}
                    if kind == "entregado"
                    else {"kind": kind},
                },
            ),
        ]

    def inicios(self, evs, now, **kw):
        acc = m.decide(m.fold(parse_plan(PLAN), evs), self.pol(), now, **kw)
        return [a for a in acc if isinstance(a, m.StartAttempt)], [
            a for a in acc if isinstance(a, m.Notify)
        ]

    def test_1_el_revisor_de_relevo_sale_de_los_carriles(self):
        now = time.time()
        evs = self.intento(1, "grok", "entregado", now - 60) + [
            Event(5, now, "demo", "A", "Verified", {"sha": "a" * 40, "ok": True}),
            Event(6, now, "demo", "A", "PrOpened", {"numero": 1}),
        ]
        evs += [
            Event(
                10 + i, now, "demo", "", "Cooldown", {"cuenta": c, "hasta": now + 3600}
            )
            for i, c in enumerate(("anthropic", "kimi", "zai"))
        ]
        inicio, _ = self.inicios(evs, now)
        rev = [a.modelo for a in inicio if a.role == "revisar"]
        self.assertFalse(
            set(rev) & {"muse", "deepseek", "qwen"},
            f"Muse/DeepSeek/Qwen solo si el plan los pide: {rev}",
        )

    def test_2_esperar_una_cuenta_que_vuelve_sola_no_es_atoro(self):
        now = time.time()
        evs = []
        for i, mo in enumerate(("k3", "k3", "glm", "glm", "grok", "grok")):
            evs += self.intento(20 + i, mo, "falla", now - 600 + i)
        evs.append(
            Event(
                99,
                now,
                "demo",
                "",
                "Cooldown",
                {"cuenta": "anthropic", "hasta": now + 1800},
            )
        )
        _, avisos = self.inicios(evs, now)
        self.assertNotIn(
            "atorado:A",
            [a.key for a in avisos],
            "Claude vuelve en 30 min: no está atorado",
        )

    def test_3_la_pausa_de_cuota_de_otro_plan_cuenta(self):
        now = time.time()
        inicio, _ = self.inicios([], now, cooldowns_fuera={"anthropic": now + 3600})
        self.assertNotIn(
            "sonnet", {a.modelo for a in inicio}, "otro plan ya dejó a Claude sin cupo"
        )

    def test_4_revisa_que_solo_puede_implementar_se_rechaza(self):
        from motor.plan import revisa_sin_quien

        tabla = ("sonnet", "k3", "glm", "grok")
        solo_sonnet = parse_plan(
            PLAN.replace(
                "repo: git@github.com:x/app.git\n",
                "repo: git@github.com:x/app.git\nrevisa: sonnet\n",
                1,
            )
        )
        self.assertTrue(revisa_sin_quien(solo_sonnet, tabla))
        con_opus = parse_plan(
            PLAN.replace(
                "repo: git@github.com:x/app.git\n",
                "repo: git@github.com:x/app.git\nrevisa: sonnet, opus\n",
                1,
            )
        )
        self.assertEqual(revisa_sin_quien(con_opus, tabla), [])

    def test_5_un_corte_tras_una_cuarentena_vencida_no_aparta_a_la_cuenta(self):
        now = time.time()
        evs = self.intento(1, "sonnet", "auth", now - 2 * 3600) + self.intento(
            2, "sonnet", "corte", now - 60
        )
        inicio, _ = self.inicios(evs, now)
        self.assertEqual([a.modelo for a in inicio if a.block == "A"], ["sonnet"])


class ConfValidaTest(unittest.TestCase):
    def test_la_conf_de_ejemplo_trae_los_topes_de_4(self):
        """Revisión de Grok: sin max_parallel la cuenta queda en 1 y sin agentes_por_maquina no hay tope."""
        from motor.conf import load_conf

        pol_ = m.Policy.from_conf(
            load_conf(Path(__file__).resolve().parent.parent / "conf-ejemplo")
        )
        self.assertEqual(pol_.agentes_por_maquina, 4)
        self.assertEqual(set(pol_.max_parallel.values()), {4})

    def test_conf_compartida_y_maquinas(self):
        import shutil

        raiz = Path(__file__).resolve().parent.parent
        self.assertEqual(conf_valida(raiz / "conf-ejemplo"), [])
        with tempfile.TemporaryDirectory() as t:
            d = Path(t) / "conf"
            shutil.copytree(raiz / "conf-ejemplo", d)
            (d / "maquinas").mkdir()
            (d / "maquinas" / "mac.toml").write_text(
                'nombre = "Mac"\nconf = "~/c"\n[avisos]\nchat = "1"\n'
            )
            (d / "maquinas" / "pc.toml").write_text('nombre = "PC"\n')
            (d / "maquinas" / "mini.toml").write_text(
                'nombre = "Mini"\nconf = "~/c"\n[no_dormir]\nencender = "pmset 1"\napagar = []\n'
            )
            with open(d / "rutas.toml", "a") as f:
                f.write(
                    '\n[rutas.x]\ncuenta = "a"\ncli = "x"\nargv = ["x"]\nenv = { K = "sk-abcdefghijklmnopqrstuvwxyz123456" }\n'
                )
            texto = "\n".join(conf_valida(d))
        for esperado in (
            "mac.toml: maquina.toml no puede traer ['avisos']",
            "pc.toml: maquina.toml necesita 'nombre' y 'conf'",
            "mini.toml: [no_dormir] lleva dos listas",
            "ruta x: el comando no recibe el encargo",
            "forma de llave",
        ):
            self.assertIn(esperado, texto)


class ConfTest(unittest.TestCase):
    def test_maquina_no_puede_traer_papeles_ni_modelos(self):
        with tempfile.TemporaryDirectory() as t:
            h = Path(t)
            (h / "maquina.toml").write_text(
                'nombre = "Mini"\nconf = "/x"\n[carriles]\ncodigo = ["glm"]\n'
            )
            with self.assertRaises(ConfError) as cm:
                load_maquina(h)
            self.assertIn("conf compartida", str(cm.exception))

    @unittest.skipUnless(
        os.name == "posix", "los permisos 0600 son de POSIX; en Windows no aplican"
    )
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
        rc = RepoConf.parse(
            '[repo]\nprincipal="master"\n[github]\nchecks_extra=["completa"]\n[deploy]\ndesplegar="d.sh"\n'
        )
        self.assertEqual(
            (rc.principal, rc.checks_extra, rc.desplegar),
            ("master", ("completa",), "d.sh"),
        )


class AvisosTest(unittest.TestCase):
    def test_plantillas_con_maquina_y_filtro(self):
        campos = {
            "n": "1",
            "total": "3",
            "aviso": "ya puedes bloquear búsquedas",
            "prueba": "pasó",
            "que_hacer": "entra a Seller Central",
            "hasta": str(time.time() + 3600),
            "bloque": "el despliegue",
            "listas": "2",
            "ahora": "Ahora trabajo en «el reporte» (Sonnet, 12 min).",
            "atoron": "Nada atorado.",
        }
        for t in PLANTILLAS:
            texto = render(t, campos, "Mini", "Reporte de ventas")
            self.assertTrue(texto.startswith("En la Mini · Reporte de ventas:"))

    def test_jerga_del_plan_cae_a_texto_generico(self):
        texto = render(
            "entregado",
            {
                "n": "1",
                "total": "1",
                "aviso": "commit 3eda9854 listo",
                "prueba": "pasó",
            },
            "PC",
            "x",
        )
        self.assertNotIn("3eda9854", texto)

    def test_aviso_humano_con_jerga_no_queda_vago(self):
        texto = render(
            "humano",
            {"que_hacer": "llena ~/.motor/secretos.env y chmod 600"},
            "Mac",
            "Motor",
        )
        self.assertIn("Necesito tu mano", texto)
        self.assertNotIn("Avancé en un plan", texto)

    def test_rechaza(self):
        for malo in (
            "Listo 3eda9854",
            "lee /Users/dn/x.txt",
            "el commit quedó",
            "\n".join("x" * 9),
        ):
            with self.assertRaises(AvisoInvalido):
                lint_plain(malo)


class ServicioTest(unittest.TestCase):
    def test_windows_resuelve_shims_cmd(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "claude.cmd").write_text("@echo off")
            argv = resolve_shim("claude", t)
            self.assertEqual(argv[1:4], ["/d", "/s", "/c"])
            self.assertTrue(argv[-1].endswith("claude.cmd"))

    def test_windows_prefiere_el_cmd_al_script_sin_extension(self):
        """MOTOR 01, PC: npm deja `opencode` (bash) junto a `opencode.cmd`; el primero no corre en Windows."""
        from motor.plataforma.windows import argv_windows

        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "opencode").write_text("#!/bin/sh\n", encoding="utf-8")
            (Path(t) / "opencode.cmd").write_text("@echo off", encoding="utf-8")
            argv = argv_windows(["opencode", "run", "x"], {"PATH": t})
            self.assertTrue(argv[-3].endswith("opencode.cmd"), argv)
            self.assertEqual(argv[-2:], ["run", "x"])
            self.assertEqual(
                argv_windows([r"C:\x\claude.exe", "-p"], {"PATH": t}),
                [r"C:\x\claude.exe", "-p"],
            )

    def test_wsl_traduce_rutas_de_windows(self):
        """MOTOR 01, PC: Muse corría en WSL pero recibía `C:\\...\\brief.md`, que Linux no ve."""
        from motor.plataforma.windows import argv_en_wsl

        self.assertEqual(
            argv_en_wsl(
                [
                    "muse",
                    "exec",
                    "--prompt-file",
                    r"C:\Users\ehven\.motor\brief.md",
                    "--json",
                ]
            ),
            [
                "wsl.exe",
                "-e",
                "muse",
                "exec",
                "--prompt-file",
                "/mnt/c/Users/ehven/.motor/brief.md",
                "--json",
            ],
        )

    def test_windows_entorno_minimo(self):
        from motor.plataforma.windows import env_minimo_windows

        e = env_minimo_windows(
            {"SystemRoot": r"C:\Windows", "APPDATA": "a", "OTRA": "x"}
        )
        self.assertEqual(e, {"SystemRoot": r"C:\Windows", "APPDATA": "a"})

    def test_todo_el_motor_lee_y_escribe_utf8(self):
        """En Windows el texto por omisión es cp1252: Grok no pudo leer el encargo (MOTOR 01, PC)."""
        import re

        raiz = Path(__file__).resolve().parent.parent / "motor"
        sin = [
            f"{f.name}:{n}"
            for f in raiz.rglob("*.py")
            for n, linea in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
            if re.search(r"\.(read_text|write_text)\(", linea)
            and "encoding=" not in linea
        ]
        self.assertEqual(sin, [])

    def test_windows_grupo_propio_fuera_del_job(self):
        self.assertTrue(flags_creacion() & CREATE_BREAKAWAY_FROM_JOB)

    def test_tarea_arranca_sin_sesion(self):
        x = tarea_xml(
            "ehven",
            r"C:\uv\python.exe",
            r"C:\Users\ehven\.motor",
            r"C:\Users\ehven\motor",
        )
        self.assertIn(
            r"<WorkingDirectory>C:\Users\ehven\motor</WorkingDirectory>",
            x,
            "sin carpeta de trabajo la tarea sale con 1 al instante (MOTOR 01, PC)",
        )
        for esperado in (
            "<BootTrigger>",
            "<LogonType>Password</LogonType>",
            "<ExecutionTimeLimit>PT0S",
            "motor servicio",
        ):
            self.assertIn(esperado, x)

    def test_instalable_pc_pasa_la_carpeta_del_motor(self):
        from motor.servicio import instalable

        x = instalable(
            "pc",
            "ehven",
            r"C:\uv\python.exe",
            r"C:\Users\ehven\.motor",
            r"C:\Users\ehven\motor",
        )
        self.assertIn(r"<WorkingDirectory>C:\Users\ehven\motor</WorkingDirectory>", x)

    def test_boot_id_con_el_path_del_servicio(self):
        """MOTOR 01, Mini: el servicio no tiene /usr/sbin en el PATH y `sysctl` no se encontraba;
        ningún agente arrancaba (boot_id fallaba en StartAttempt)."""
        import subprocess
        import sys

        env = {
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
        }
        r = subprocess.run(
            [
                sys.executable,
                "-c",
                "from motor.plataforma import boot_id; print(boot_id())",
            ],
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.strip())

    def test_launchd_arranca_sin_sesion(self):
        p = plist_launchd(
            "gon", "/usr/bin/python3", "/Users/gon/.motor", "/Users/gon/motor"
        )
        for esperado in (
            "<key>UserName</key><string>gon</string>",
            "<key>KeepAlive</key><true/>",
            "LaunchDaemons",
        ):
            self.assertIn(esperado, p)


class CorreccionTest(unittest.TestCase):
    """StartAttempt.correccion: lo marca un bloque de bug o una revisión no aprobada,
    nunca el texto del feedback (un cambio de plan o una Acepta fallida también lo traen)."""

    def arranque(self, eventos, plan=PLAN):
        s = m.fold(parse_plan(plan), eventos)
        return next(
            a
            for a in m.decide(s, pol(), time.time())
            if isinstance(a, m.StartAttempt) and a.role == "implementar"
        )

    def test_un_bloque_de_bug_arranca_en_correccion(self):
        plan = PLAN.replace(
            "lane: gate\nprueba-real", "lane: gate\ntipo: bug\nprueba-real", 1
        )
        self.assertTrue(self.arranque([], plan).correccion)

    def test_una_revision_no_aprobada_lo_vuelve_correccion(self):
        eventos = entregado() + [
            ev("Verified", "A", {"sha": "a" * 40, "ok": False}),
            ev("PrOpened", "A", {"numero": 7}),
            ev(
                "Reviewed",
                "A",
                {
                    "sha": "a" * 40,
                    "veredicto": "CAMBIOS",
                    "bloqueantes": [{"texto": "x", "repro": "false"}],
                },
            ),
        ]
        self.assertTrue(self.arranque(eventos).correccion)

    def test_una_revision_aprobada_no_es_correccion(self):
        eventos = entregado() + [
            ev("Verified", "A", {"sha": "a" * 40, "ok": True}),
            ev("PrOpened", "A", {"numero": 7}),
            ev(
                "Reviewed",
                "A",
                {"sha": "a" * 40, "veredicto": "APROBADO", "bloqueantes": []},
            ),
            ev("MarkedReady", "A", {"sha": "a" * 40}),
            ev("ChecksResult", "A", {"sha": "a" * 40, "green": False, "detalle": "x"}),
        ]
        a = self.arranque(eventos)
        self.assertFalse(
            a.correccion, "las pruebas en rojo no son una ronda de corrección"
        )

    def test_el_feedback_solo_no_marca_la_correccion(self):
        eventos = [
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
                    "outcome": {
                        "kind": "interrumpido",
                        "detalle": "el plan cambió: la fila A.1",
                    },
                },
            ),
        ]
        a = self.arranque(eventos)
        self.assertTrue(any("plan cambió" in x for x in a.feedback))
        self.assertFalse(a.correccion)


class FeedbackRevisorTest(unittest.TestCase):
    """Un revisor que terminó por algo que su sucesor no puede corregir no le deja feedback."""

    def siguiente_revisor(self, fin: dict):
        eventos = entregado() + [
            ev("Verified", "A", {"sha": "a" * 40, "ok": True}),
            ev("PrOpened", "A", {"numero": 7}),
            ev(
                "AttemptStarted",
                "A",
                {"n": 1, "role": "revisar", "modelo": "opus", "proc": {}, "dir": "d"},
            ),
            ev(
                "AttemptEnded",
                "A",
                {"n": 1, "role": "revisar", "outcome": fin},
            ),
        ]
        pedido = PLAN.replace(
            "repo: git@github.com:x/app.git\n",
            "repo: git@github.com:x/app.git\nrevisa: opus, k3\n",
            1,
        )
        s = m.fold(parse_plan(pedido), eventos)
        return next(
            a
            for a in m.decide(s, pol(), time.time())
            if isinstance(a, m.StartAttempt) and a.role == "revisar"
        )

    def test_un_fin_que_el_siguiente_revisor_no_puede_corregir_no_se_le_pasa(self):
        for kind in sorted(m.FINES_AJENOS_AL_REVISOR):
            with self.subTest(kind=kind):
                self.assertEqual(
                    self.siguiente_revisor({"kind": kind, "detalle": "x"}).feedback, ()
                )
        self.assertEqual(
            m.FINES_AJENOS_AL_REVISOR,
            {"cuota", "rate_limit", "auth", "corte", "perdido", "interrumpido"},
        )

    def test_un_veredicto_invalido_si_se_le_pasa(self):
        a = self.siguiente_revisor(
            {"kind": "veredicto_invalido", "detalle": "falta el sha"}
        )
        self.assertEqual(len(a.feedback), 1)
        self.assertIn("veredicto_invalido", a.feedback[0])
        self.assertIn("falta el sha", a.feedback[0])


if __name__ == "__main__":
    unittest.main()
