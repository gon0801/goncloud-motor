"""MOTOR 02, bloque D: el motor dice por qué está parado y avisa una vez cuando se atora."""

from __future__ import annotations

import dataclasses
import http.server
import os
import re
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from motor import machine as m
from motor.__main__ import doctor
from motor.avisos import _resumen
from motor.daemon import Daemon
from motor.diagnostico import Causa, diagnostico
from motor.notify import PLANTILLAS, FIJOS, PARCIAL, Notifier, render
from motor.store import Event
from tests.test_no_caerse import Banco
from tests.test_puro import PLAN, entregado, parse_plan, pol

SHA = "a" * 40
PLAN_RELEASE = PLAN.replace("lane: gate", "lane: release", 1)
NOW = 1_800_000_000.0


def e(kind: str, data: dict, hace: float = 0.0, block: str = "A") -> Event:
    return Event(len(kind) + int(hace), NOW - hace, "demo", block, kind, data)


def intento(
    modelo: str, kind: str, n: int, hace: float, role="implementar", **extra
) -> list[Event]:
    """Un intento que terminó `hace` segundos antes de NOW."""
    return [
        e(
            "AttemptStarted",
            {"n": n, "role": role, "modelo": modelo, "proc": {}, "dir": f"d{n}"},
            hace + 5,
        ),
        e(
            "AttemptEnded",
            {"n": n, "role": role, "outcome": {"kind": kind, **extra}},
            hace,
        ),
    ]


def hasta_pr(sha=SHA) -> list[Event]:
    return intento("glm", "entregado", 1, 7200, sha=sha) + [
        e("Verified", {"sha": sha, "ok": True}, 7000),
        e("PrOpened", {"numero": 1}, 6900),
        e("Reviewed", {"sha": sha, "veredicto": "APROBADO", "bloqueantes": []}, 6800),
    ]


def diag(evs, bid="A", policy=None, plan=PLAN, now=NOW):
    snap = m.fold(parse_plan(plan), evs)
    return diagnostico(snap, bid, policy or pol(), now)


class CausasTest(unittest.TestCase):
    """Una prueba por causa: cada una con su motivo, su clave propia y si es atasco."""

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

    def assertCausa(self, d, causa, atorado=False):
        self.assertIs(d.causa, causa, d)
        self.assertEqual(d.atorado, atorado, d)
        self.assertTrue(d.motivo)

    def test_hecho(self):
        evs = entregado() + [
            e("Merged", {"sha": "a" * 40, "commit": "c"}),
            e("Proved", {"sha": "a" * 40, "ok": True}),
        ]
        self.assertCausa(diag(evs), Causa.HECHO)

    def test_espera_dependencias(self):
        d = diag([], "E", plan=self.PASOS)
        self.assertCausa(d, Causa.ESPERA_DEPENDENCIAS)
        self.assertEqual(d.clave, "")

    def test_espera_humano(self):
        evs = entregado("F") + [
            e("Merged", {"sha": SHA, "commit": "c"}, block="F"),
            e("Proved", {"sha": SHA, "ok": True}, block="F"),
        ]
        d = diag(evs, "E", plan=self.PASOS)
        self.assertCausa(d, Causa.ESPERA_HUMANO)
        self.assertEqual(d.clave, "humano:E")

    def test_trabajando(self):
        evs = [
            e(
                "AttemptStarted",
                {
                    "n": 1,
                    "role": "implementar",
                    "modelo": "sonnet",
                    "proc": {},
                    "dir": "d",
                },
                600,
            )
        ]
        self.assertCausa(diag(evs), Causa.TRABAJANDO)

    def test_revisando(self):
        evs = hasta_pr()[:-1] + [
            e(
                "AttemptStarted",
                {
                    "n": 1,
                    "role": "revisar",
                    "modelo": "opus",
                    "proc": {},
                    "dir": "r",
                    "sha": SHA,
                },
                60,
            )
        ]
        self.assertCausa(diag(evs), Causa.REVISANDO)

    def test_paso_del_motor(self):
        d = diag(entregado())
        self.assertCausa(d, Causa.PASO_DEL_MOTOR)
        self.assertEqual(d.motivo, "la verifico")

    def test_espera_checks(self):
        evs = hasta_pr() + [e("MarkedReady", {"sha": SHA}, 600)]
        d = diag(evs)
        self.assertCausa(d, Causa.ESPERA_CHECKS)
        self.assertIn("desde hace 10 min", d.motivo)

    def test_rehacer(self):
        evs = hasta_pr()[:-1] + [
            e(
                "Reviewed",
                {
                    "sha": SHA,
                    "veredicto": "CAMBIOS",
                    "bloqueantes": [{"texto": "t", "repro": "false"}],
                },
                6800,
            )
        ]
        d = diag(evs)
        self.assertCausa(d, Causa.REHACER)
        self.assertEqual(d.motivo, "toca arreglarla porque el revisor pidió cambios")
        self.assertIn("BLOQUEANTE: t", d.detalle)

    def test_por_revisar(self):
        evs = hasta_pr()[:-1]
        self.assertCausa(diag(evs), Causa.POR_REVISAR)

    def test_turno(self):
        pol1 = m.Policy(
            implementadores=("sonnet",),
            revisores=("opus",),
            cuenta={"sonnet": "anthropic", "opus": "anthropic"},
            max_parallel={"anthropic": 1},
        )
        otro = [
            e(
                "AttemptStarted",
                {
                    "n": 1,
                    "role": "implementar",
                    "modelo": "sonnet",
                    "proc": {},
                    "dir": "c",
                },
                60,
                block="C",
            )
        ]
        d = diag(otro, "A", pol1)
        self.assertCausa(d, Causa.TURNO)

    def test_pausa_cuentas(self):
        evs = intento("sonnet", "transitorio", 1, 30) + intento(
            "glm", "transitorio", 2, 20
        )
        self.assertCausa(diag(evs), Causa.PAUSA_CUENTAS)

    def test_sin_cupo(self):
        evs = [
            e("Cooldown", {"cuenta": "anthropic", "hasta": NOW + 600}, block=""),
            e("Cooldown", {"cuenta": "zai", "hasta": NOW + 1200}, block=""),
        ]
        d = diag(evs)
        self.assertCausa(d, Causa.SIN_CUPO)
        self.assertEqual(d.clave, f"lento:{int(NOW + 600)}")

    def test_sin_sesion(self):
        evs = intento("sonnet", "auth", 1, 600) + intento("glm", "auth", 2, 500)
        d = diag(evs)
        self.assertCausa(d, Causa.SIN_SESION, atorado=True)
        self.assertEqual(d.clave, f"humano:auth:anthropic:{int(NOW - 600)}")

    def test_escalera_agotada(self):
        evs = (
            intento("sonnet", "falla", 1, 900)
            + intento("sonnet", "falla", 2, 800)
            + intento("glm", "falla", 3, 700)
            + intento("glm", "falla", 4, 600)
        )
        d = diag(evs)
        self.assertCausa(d, Causa.ESCALERA_AGOTADA, atorado=True)
        self.assertEqual(d.clave, "atorado:A:agotada:4")

    def test_checks_incoherente(self):
        evs = hasta_pr() + [
            e("MarkedReady", {"sha": SHA}, 3600),
            e(
                "ChecksEsperando",
                {"sha": SHA, "faltan": ["completa"], "github_termino": True},
                40 * 60,
            ),
        ]
        d = diag(evs)
        self.assertCausa(d, Causa.CHECKS_INCOHERENTE, atorado=True)
        self.assertEqual(d.clave, "atorado:A:incoherente:" + SHA[:12])

    def test_checks_lento(self):
        evs = hasta_pr() + [e("MarkedReady", {"sha": SHA}, 7 * 3600)]
        d = diag(evs)
        self.assertCausa(d, Causa.CHECKS_LENTO, atorado=True)
        self.assertEqual(d.clave, "atorado:A:checks:" + SHA[:12])

    def test_falta_comando(self):
        evs = entregado() + [
            e("Verified", {"sha": SHA, "ok": False, "falta_comando": ["python"]})
        ]
        d = diag(evs)
        self.assertCausa(d, Causa.FALTA_COMANDO, atorado=True)
        self.assertEqual(d.clave, "atorado:A:comando:python")

    def test_repetida(self):
        evs = []
        for i in range(3):
            sha = f"{i + 1:x}" * 40
            evs += entregado(sha=sha, n=i + 1) + [
                e("Merged", {"sha": sha, "commit": "c"}),
                e("Proved", {"sha": sha, "ok": False, "detalle": "ABORTA"}),
            ]
        d = diag(evs)
        self.assertCausa(d, Causa.REPETIDA, atorado=True)
        self.assertEqual(d.clave, "atorado:A:repite:prueba:0")

    def test_efecto_fallando(self):
        evs = entregado() + [
            e(
                "EffectFailed",
                {"accion": "Verify", "error": "red", "desde": NOW - 4000},
                10,
            ),
            e(
                "EffectFailed",
                {"accion": "Verify", "error": "red", "desde": NOW - 4000},
                400,
            ),
        ]
        d = diag(evs)
        self.assertCausa(d, Causa.EFECTO_FALLANDO, atorado=True)
        self.assertEqual(d.clave, f"atorado:A:efecto:Verify:{int(NOW - 4000)}")

    def test_toda_causa_esta_probada_y_ninguna_clave_se_comparte(self):
        probadas = {
            n.removeprefix("test_").upper()
            for n in dir(self)
            if n.startswith("test_")
            and n.removeprefix("test_").upper() in Causa.__members__
        }
        self.assertEqual(probadas, set(Causa.__members__), "falta una prueba por causa")
        familias = {
            "espera_humano": r"humano:\w+",
            "sin_sesion": r"humano:auth:\w+:\d+",
            "sin_cupo": r"lento:\d+",
            "agotada": r"atorado:\w+:agotada:\d+",
            "incoherente": r"atorado:\w+:incoherente:\w+",
            "checks": r"atorado:\w+:checks:\w+",
            "comando": r"atorado:\w+:comando:[\w,]+",
            "repetida": r"atorado:\w+:repite:\w+:\d+",
            "efecto": r"atorado:\w+:efecto:\w+:\d+",
        }
        muestras = {
            "espera_humano": "humano:A",
            "sin_sesion": "humano:auth:anthropic:1",
            "sin_cupo": "lento:1",
            "agotada": "atorado:A:agotada:1",
            "incoherente": "atorado:A:incoherente:x",
            "checks": "atorado:A:checks:x",
            "comando": "atorado:A:comando:x",
            "repetida": "atorado:A:repite:prueba:0",
            "efecto": "atorado:A:efecto:Verify:1",
        }
        for nombre, clave in muestras.items():
            coinciden = [f for f, rx in familias.items() if re.fullmatch(rx, clave)]
            self.assertEqual(coinciden, [nombre], clave)

    def test_el_resumen_y_el_estado_usan_el_mismo_motivo(self):
        evs = entregado() + [
            e("Verified", {"sha": SHA, "ok": False, "falta_comando": ["python"]})
        ]
        snap = m.fold(parse_plan(PLAN), evs)
        campos = dict(_resumen(snap, NOW, pol()))
        self.assertIn(diagnostico(snap, "A", pol(), NOW).motivo, campos["atoron"])


class RachaTest(Banco):
    """La alarma de "un paso del motor lleva una hora fallando" con lo que el daemon escribe de verdad."""

    def arranca(self) -> tuple[Daemon, object]:
        d = self.daemon()
        d.store.append(
            "demo", "", "PlanLoaded", {"plan": self.plan, "por": "t"}, ukey="plan:demo"
        )
        return d, d.plans()[0]

    def falla(self, d, plan, now, accion=None):
        d._falla_efecto(plan, accion or m.OpenPr("A", SHA), RuntimeError("red"), now)

    def diag(self, d, plan, now):
        snap = m.fold(plan, d.store.events("demo"))
        return diagnostico(snap, "A", d.cx.policy, now)

    def avisos_de_efecto(self, d):
        return [
            ev_.data["key"]
            for ev_ in d.store.events("demo")
            if ev_.kind == "NoticeQueued"
            and ev_.data["key"].startswith("atorado:A:efecto:")
        ]

    def tick_con(self, d, plan, now, falla: bool):
        """Un tick del plan donde el único efecto que se intenta (StartAttempt) falla o sale bien."""
        real = d.execute

        def execute(p, snap, a):
            if isinstance(a, m.StartAttempt):
                if falla:
                    raise RuntimeError("se cayó la red")
                return
            real(p, snap, a)

        d.execute = execute
        d._tick_plan(plan, [plan], now)

    def test_cuatro_horas_fallando_cada_5_min_avisan_una_vez(self):
        d, plan = self.arranca()
        t0 = time.time() - 5 * 3600
        for i in range(0, 4 * 3600 + 1, 300):
            self.tick_con(d, plan, t0 + i, falla=True)
            if i < 3600:
                self.assertFalse(self.diag(d, plan, t0 + i).atorado, i)
        avisos = self.avisos_de_efecto(d)
        self.assertEqual(len(avisos), 1, avisos)
        fallas = [e_ for e_ in d.store.events("demo") if e_.kind == "EffectFailed"]
        self.assertGreater(
            len(fallas), 40, "cada falla queda en el log, no una por hora"
        )
        self.assertEqual({e_.data["desde"] for e_ in fallas}, {t0})

    def test_dos_fallas_sueltas_separadas_por_una_hora_no_avisan(self):
        d, plan = self.arranca()
        t0 = time.time() - 7200
        self.falla(d, plan, t0)
        self.assertFalse(self.diag(d, plan, t0).atorado)
        self.falla(d, plan, t0 + 3700)
        d2 = [
            e_.data["desde"]
            for e_ in d.store.events("demo")
            if e_.kind == "EffectFailed"
        ]
        self.assertEqual(d2, [t0, t0 + 3700], "la segunda empieza otra racha")
        self.assertFalse(self.diag(d, plan, t0 + 3700).atorado)
        self.assertEqual(self.avisos_de_efecto(d), [])

    def test_tras_un_exito_una_falla_nueva_empieza_otra_racha(self):
        d, plan = self.arranca()
        t0 = time.time() - 3 * 3600
        for i in range(0, 4201, 300):
            self.tick_con(d, plan, t0 + i, falla=True)
        self.assertEqual(len(self.avisos_de_efecto(d)), 1)
        self.tick_con(d, plan, t0 + 4500, falla=False)
        self.tick_con(d, plan, t0 + 4800, falla=False)
        oks = [
            e_
            for e_ in d.store.events("demo")
            if e_.kind == "EffectOk" and e_.block == "A"
        ]
        self.assertEqual(len(oks), 1, "el éxito se anota una vez")
        self.assertEqual(oks[0].data["accion"], "StartAttempt")
        self.tick_con(d, plan, t0 + 5100, falla=True)
        ultima = [e_ for e_ in d.store.events("demo") if e_.kind == "EffectFailed"][-1]
        self.assertEqual(ultima.data["desde"], t0 + 5100, "otra racha")
        self.assertFalse(self.diag(d, plan, t0 + 5100).atorado)

    def test_la_racha_sobrevive_a_un_reinicio_del_motor(self):
        d, plan = self.arranca()
        t0 = time.time() - 2 * 3600
        for i in range(0, 1800, 300):
            self.tick_con(d, plan, t0 + i, falla=True)
        d2 = self.daemon()  # "el motor se reinició"
        d2.tick_ = None
        self.tick_con(d2, plan, t0 + 1800, falla=True)
        desdes = {
            e_.data["desde"]
            for e_ in d2.store.events("demo")
            if e_.kind == "EffectFailed"
        }
        self.assertEqual(desdes, {t0})


class PrTest(unittest.TestCase):
    def test_un_pr_nuevo_vuelve_a_pasar_por_listo_y_por_las_pruebas(self):
        base = hasta_pr() + [
            e("MarkedReady", {"sha": SHA}, 600),
            e("ChecksResult", {"sha": SHA, "green": True}, 300),
        ]
        snap = m.fold(parse_plan(PLAN), base)
        self.assertIs(m.estado(snap, "A"), m.Estado.MERGE)
        cerrado = base + [e("PrCerrado", {"numero": 1, "estado": "cerrado"}, 100)]
        snap = m.fold(parse_plan(PLAN), cerrado)
        bv = snap.blocks["A"]
        self.assertEqual((bv.ready, bv.listo_desde, bv.checks), (set(), {}, {}))
        nuevo = cerrado + [e("PrOpened", {"numero": 2}, 50)]
        snap = m.fold(parse_plan(PLAN), nuevo)
        self.assertIs(
            m.estado(snap, "A"), m.Estado.LISTO, "el siguiente paso es marcar listo"
        )
        acc = m.decide(snap, pol(), NOW)
        self.assertEqual([type(a) for a in acc if isinstance(a, m.Merge)], [])
        self.assertTrue([a for a in acc if isinstance(a, m.MarkReady)])
        self.assertIsInstance(m.merge_gate(snap.blocks["A"]), str)


class EscaleraTest(unittest.TestCase):
    def inicios(self, evs, policy=None, now=NOW, plan=PLAN):
        snap = m.fold(parse_plan(plan), evs)
        acc = m.decide(snap, policy or pol(), now)
        return [a for a in acc if isinstance(a, m.StartAttempt) and a.block == "A"], [
            a for a in acc if isinstance(a, m.Notify)
        ]

    def solo_sonnet(self):
        return m.Policy(
            implementadores=("sonnet",),
            revisores=("opus",),
            cuenta={"sonnet": "anthropic", "opus": "anthropic"},
            max_parallel={"anthropic": 1},
            cli={"sonnet": "claude", "opus": "claude"},
        )

    def test_cuatro_fallas_pasajeras_no_agotan_la_escalera(self):
        pol1 = self.solo_sonnet()
        for kind in ("transitorio", "sin_resultado", "perdido", "tiempo"):
            evs = sum(
                (intento("sonnet", kind, n, 7200 - n * 100) for n in range(1, 5)), []
            )
            with self.subTest(kind=kind):
                ini, _ = self.inicios(evs, pol1)
                self.assertEqual(
                    [a.modelo for a in ini], ["sonnet"], "sigue al volver la red"
                )
                d = diag(evs, policy=pol1, now=NOW - 7200 + 450)
                self.assertFalse(d.atorado)

    def test_la_pausa_crece_1_5_15_y_30_minutos(self):
        pol1 = self.solo_sonnet()
        for n, pausa in ((1, 60), (2, 300), (3, 900), (4, 1800), (5, 1800)):
            evs = sum(
                (
                    intento("sonnet", "transitorio", i, 10000 + (n - i) * 5000)
                    for i in range(1, n + 1)
                ),
                [],
            )
            ult = 10000
            with self.subTest(n=n):
                antes, _ = self.inicios(evs, pol1, NOW - ult + pausa - 1)
                despues, _ = self.inicios(evs, pol1, NOW - ult + pausa + 1)
                self.assertEqual((antes, len(despues)), ([], 1))

    def test_las_fallas_de_verdad_si_agotan_y_las_pasajeras_no_las_mezclan(self):
        pol1 = self.solo_sonnet()
        evs = (
            intento("sonnet", "transitorio", 1, 9000)
            + intento("sonnet", "falla", 2, 8000)
            + intento("sonnet", "transitorio", 3, 7000)
            + intento("sonnet", "falla", 4, 6000)
        )
        ini, _ = self.inicios(evs, pol1)
        self.assertEqual(ini, [], "dos fallas de verdad agotan a sonnet")
        self.assertIs(diag(evs, policy=pol1).causa, Causa.ESCALERA_AGOTADA)

    def test_sin_cambios_no_cuenta_si_falta_un_comando(self):
        pol1 = self.solo_sonnet()
        falta = e(
            "Verified", {"sha": SHA, "ok": False, "falta_comando": ["python"]}, 9500
        )
        evs = (
            intento("sonnet", "entregado", 1, 10000, sha=SHA)
            + [falta]
            + intento("sonnet", "sin_cambios", 2, 9000)
            + intento("sonnet", "sin_cambios", 3, 8000)
        )
        ini, _ = self.inicios(evs, pol1)
        self.assertEqual(len(ini), 1, "no es culpa del agente")

    def test_sin_cambios_no_cuenta_si_fallo_la_prueba_real_o_el_despliegue(self):
        pol1 = self.solo_sonnet()
        for malo in (
            e("Proved", {"sha": SHA, "ok": False}, 9500),
            e("Deployed", {"sha": SHA, "ok": False}, 9500),
        ):
            plan = PLAN_RELEASE if malo.kind == "Deployed" else PLAN
            evs = (
                intento("sonnet", "entregado", 1, 10000, sha=SHA)
                + [e("Merged", {"sha": SHA, "commit": "c"}, 9800), malo]
                + intento("sonnet", "sin_cambios", 2, 9000)
                + intento("sonnet", "sin_cambios", 3, 8000)
            )
            with self.subTest(malo=malo.kind):
                ini, _ = self.inicios(evs, pol1, plan=plan)
                self.assertEqual(len(ini), 1)

    def test_sin_cambios_si_cuenta_cuando_el_agente_pudo_cambiar_algo(self):
        pol1 = self.solo_sonnet()
        evs = (
            intento("sonnet", "entregado", 1, 10000, sha=SHA)
            + [e("Verified", {"sha": SHA, "ok": False, "detalle": "x"}, 9500)]
            + intento("sonnet", "sin_cambios", 2, 9000)
            + intento("sonnet", "sin_cambios", 3, 8000)
        )
        ini, _ = self.inicios(evs, pol1)
        self.assertEqual(ini, [])

    def test_todas_sin_sesion_avisa_humano_una_vez_por_cuenta_y_racha(self):
        evs = intento("sonnet", "auth", 1, 900) + intento("glm", "auth", 2, 800)
        snap = m.fold(parse_plan(PLAN), evs)
        pol_ = dataclasses.replace(
            pol(),
            cli={"sonnet": "claude", "opus": "claude", "glm": "opencode", "k3": "kimi"},
        )
        humanos = [
            a
            for a in m.decide(snap, pol_, NOW)
            if isinstance(a, m.Notify) and a.template == "humano"
        ]
        self.assertEqual(len(humanos), 2, "una por cuenta (Claude y Z.ai)")
        self.assertEqual(len({a.key for a in humanos}), 2)
        todo = " ".join(v for a in humanos for _, v in a.campos)
        self.assertIn("Claude", todo)
        self.assertIn("opencode auth login", todo)
        self.assertIn("claude y luego /login", todo)
        texto = render("humano", dict(humanos[0].campos), "Mac", "Plan")
        self.assertIn("claude y luego /login", texto)
        ya = evs + [
            e("NoticeQueued", {"key": a.key, "texto": "x"}, block="") for a in humanos
        ]
        again = [
            a
            for a in m.decide(m.fold(parse_plan(PLAN), ya), pol_, NOW + 600)
            if isinstance(a, m.Notify) and a.template == "humano"
        ]
        self.assertEqual(again, [], "una vez por racha, aunque el reloj reintente")
        # la cuarentena vence, se reintenta, vuelve a fallar: sigue la MISMA racha
        mas = ya + intento("sonnet", "auth", 3, -2000)
        otra = [
            a
            for a in m.decide(m.fold(parse_plan(PLAN), mas), pol_, NOW + 2500)
            if isinstance(a, m.Notify)
            and a.template == "humano"
            and "anthropic" in a.key
        ]
        self.assertEqual(otra, [])

    def test_con_sesion_de_alguna_cuenta_no_avisa_humano(self):
        evs = intento("sonnet", "auth", 1, 900)
        _, avisos = self.inicios(evs)
        self.assertFalse([a for a in avisos if a.template == "humano"])

    def test_todas_sin_cupo_avisa_al_entrar_en_la_pausa_con_la_hora(self):
        hasta = NOW + 1800  # mucho menos que aviso_lento_s
        evs = [
            e("Cooldown", {"cuenta": "anthropic", "hasta": hasta}, 10, block=""),
            e("Cooldown", {"cuenta": "zai", "hasta": hasta + 600}, 10, block=""),
        ]
        snap = m.fold(parse_plan(PLAN), evs)
        lentos = [
            a
            for a in m.decide(snap, pol(), NOW)
            if isinstance(a, m.Notify) and a.template == "lento"
        ]
        self.assertEqual(len(lentos), 1, "un aviso para el plan, no uno por bloque")
        self.assertEqual(dict(lentos[0].campos)["hasta"], str(int(hasta)))
        texto = render("lento", dict(lentos[0].campos), "Mac", "Plan")
        self.assertIn("Sigo solo cuando se renueve, el ", texto)
        ya = evs + [e("NoticeQueued", {"key": lentos[0].key}, 5, block="")]
        self.assertFalse(
            [
                a
                for a in m.decide(m.fold(parse_plan(PLAN), ya), pol(), NOW + 60)
                if isinstance(a, m.Notify) and a.template == "lento"
            ]
        )

    def test_con_la_escalera_agotada_hay_atasco_con_su_motivo_y_el_resumen_lo_dice(
        self,
    ):
        evs = (
            intento("sonnet", "falla", 1, 900)
            + intento("sonnet", "falla", 2, 800)
            + intento("glm", "falla", 3, 700)
            + intento("glm", "falla", 4, 600)
        )
        d = diag(evs)
        self.assertTrue(d.atorado)
        self.assertIn("ningún agente disponible", d.motivo)
        snap = m.fold(parse_plan(PLAN), evs)
        atorados = [
            a
            for a in m.decide(snap, pol(), NOW)
            if isinstance(a, m.Notify) and a.template == "atorado"
        ]
        self.assertEqual([a.key for a in atorados if a.block == "A"], [d.clave])
        self.assertIn(d.motivo, dict(_resumen(snap, NOW, pol()))["atoron"])


class AvisosColaTest(unittest.TestCase):
    def test_la_plantilla_atorado_ya_no_promete_a_claude(self):
        for tabla in (PLANTILLAS, FIJOS, PARCIAL):
            self.assertNotIn("Claude la va a revisar", tabla["atorado"])
        texto = render(
            "atorado", {"bloque": "el reporte", "motivo": "x"}, "Mac", "Plan"
        )
        self.assertNotIn("Claude", texto)

    def servidor(self, rechaza: str, codigo=400):
        recibidos = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers["Content-Length"])
                texto = self.rfile.read(n).decode()
                recibidos.append(texto)
                if rechaza in texto:
                    self.send_response(codigo)
                    self.end_headers()
                    self.wfile.write(b'{"ok": false}')
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return srv, recibidos

    def home(self):
        t = Path(tempfile.mkdtemp(prefix="motor-avisos-"))
        self.addCleanup(shutil.rmtree, t, True)
        return t

    def test_un_4xx_manda_el_aviso_a_fallidos_y_la_cola_sigue(self):
        srv, recibidos = self.servidor("MALO")
        h = self.home()
        n = Notifier(h, f"http://127.0.0.1:{srv.server_port}", "tok", "1")
        n.encola("a", "MALO este texto")
        time.sleep(0.01)
        n.encola("b", "bueno")
        self.assertEqual(n.envia_pendientes(), 1)
        self.assertEqual(
            [p.name.split("-", 1)[1] for p in (h / "avisos/fallidos").iterdir()],
            ["a.txt"],
        )
        self.assertEqual(
            [p.name.split("-", 1)[1] for p in (h / "avisos/enviados").iterdir()],
            ["b.txt"],
        )
        self.assertEqual(list((h / "avisos/pendientes").iterdir()), [])
        self.assertEqual(len(recibidos), 2)

    def test_un_429_o_un_5xx_se_reintentan_despues(self):
        for codigo in (429, 502):
            srv, _ = self.servidor("MALO", codigo)
            h = self.home()
            n = Notifier(h, f"http://127.0.0.1:{srv.server_port}", "tok", "1")
            n.encola("a", "MALO")
            with self.subTest(codigo=codigo):
                self.assertEqual(n.envia_pendientes(), 0)
                self.assertEqual(len(list((h / "avisos/pendientes").iterdir())), 1)
                self.assertFalse((h / "avisos/fallidos").exists())

    def test_doctor_marca_error_si_faltan_el_token_o_el_chat(self):
        conf_ejemplo = Path(__file__).resolve().parent.parent / "conf-ejemplo"

        def corre(token: bool, chat: bool) -> list[str]:
            h = self.home()
            conf = h / "conf"
            shutil.copytree(conf_ejemplo, conf)
            g = conf / "general.toml"
            g.write_text(
                g.read_text().replace('chat = ""', f'chat = "{"123" if chat else ""}"')
            )
            (h / "maquina.toml").write_text(f"nombre = 'Mac'\nconf = '{conf}'\n")
            if token:
                s = h / "secretos.env"
                s.write_text("MOTOR_TELEGRAM_TOKEN=abc\n")
                os.chmod(s, 0o600)
            r = doctor(h)
            return [p for p in r["problemas"] if p.startswith("avisos:")]

        self.assertEqual(len(corre(False, False)), 2)
        self.assertEqual(len(corre(False, True)), 1)
        self.assertIn("token", corre(False, True)[0])
        self.assertEqual(len(corre(True, False)), 1)
        self.assertIn("chat", corre(True, False)[0])
        self.assertEqual(corre(True, True), [])

    def test_el_resumen_de_cada_hora_no_pasa_de_4000_caracteres(self):
        bloques = "".join(
            f"\n## Bloque B{i} — {'parte larga ' * 6}{i}\nlane: gate\nprueba-real: true\n"
            f"| Fila | Contenido | Acepta |\n| B{i}.1 | algo | true |\n"
            for i in range(120)
        )
        plan = parse_plan(PLAN + bloques)
        snap = m.fold(plan, [])
        campos = dict(_resumen(snap, NOW, pol()))
        texto = render("avance", campos, "Mini", "Plan con muchas partes")
        self.assertLessEqual(len(texto), 4000)
        self.assertIn("partes listas", texto)

    def test_las_notas_del_revisor_que_no_bloquean_van_en_el_encargo_siguiente(self):
        evs = hasta_pr()[:-1] + [
            e(
                "Reviewed",
                {
                    "sha": SHA,
                    "veredicto": "CAMBIOS",
                    "bloqueantes": [{"texto": "t", "repro": "false"}],
                    "notas": ["renombra esta variable", "falta un comentario"],
                },
                6800,
            )
        ]
        snap = m.fold(parse_plan(PLAN), evs)
        ini = [
            a
            for a in m.decide(snap, pol(), NOW)
            if isinstance(a, m.StartAttempt) and a.block == "A"
        ]
        self.assertEqual(len(ini), 1)
        fb = " | ".join(ini[0].feedback)
        self.assertIn("Nota del revisor (no bloquea): renombra esta variable", fb)
        self.assertIn("Nota del revisor (no bloquea): falta un comentario", fb)
        self.assertIn("BLOQUEANTE: t", fb)
        self.assertIn(
            "el revisor pidió cambios", fb, "el motivo del diagnóstico va primero"
        )


if __name__ == "__main__":
    unittest.main()
