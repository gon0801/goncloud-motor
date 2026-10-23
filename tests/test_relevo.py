"""Reglas de relevo portadas de `tests/test_subscription_router.py` de Hermes (goncloud-hermes,
feat/hermes-team). Cada prueba nombra la de Hermes de la que sale; aquí son puras: sin base ni reloj."""

from __future__ import annotations

import time
import unittest

from motor import machine as m
from motor import relevo
from motor.relevo import Falla
from motor.store import Event


class AvanceTest(unittest.TestCase):
    # test_auth_and_quota_advance_in_order
    def test_auth_y_cuota_avanzan(self):
        self.assertTrue(relevo.avanza(Falla.AUTH))
        self.assertTrue(relevo.avanza(Falla.QUOTA))

    # test_a_timeout_failure_blocks_the_stage_instead_of_advancing
    def test_un_tiempo_agotado_nunca_avanza(self):
        self.assertFalse(relevo.avanza(Falla.TIMEOUT))
        self.assertNotIn(Falla.TIMEOUT, relevo.AVANZAN)

    # test_non_advancing_failure_blocks_the_stage
    def test_peticion_invalida_no_avanza(self):
        self.assertFalse(relevo.avanza(Falla.INVALID_REQUEST))

    def test_modelo_no_disponible_avanza(self):
        self.assertTrue(relevo.avanza(Falla.MODEL_UNAVAILABLE))

    def test_las_que_avanzan_son_exactamente_siete(self):
        self.assertEqual(len(relevo.AVANZAN), 7)
        self.assertEqual(relevo.AVANZAN & {Falla.TIMEOUT, Falla.INVALID_REQUEST}, set())


class PausaTest(unittest.TestCase):
    # test_quota_cooldown_skips_adapter
    def test_cuota_pausa_la_cuenta_15_minutos(self):
        self.assertEqual(relevo.pausa_cuenta_s(Falla.QUOTA), 15 * 60)

    # test_a_persistent_throttle_benches_the_account_briefly_and_keeps_it_ready
    def test_rate_limit_pausa_solo_2_minutos(self):
        self.assertEqual(relevo.pausa_cuenta_s(Falla.RATE_LIMIT), 120)

    # test_a_timeout_failure_leaves_no_cooldown_bench
    def test_un_tiempo_no_deja_pausa(self):
        self.assertIsNone(relevo.pausa_cuenta_s(Falla.TIMEOUT))

    def test_transporte_y_auth_no_pausan_la_cuenta(self):
        # auth se maneja con cuarentena, no con pausa
        self.assertIsNone(relevo.pausa_cuenta_s(Falla.TRANSPORT))
        self.assertIsNone(relevo.pausa_cuenta_s(Falla.AUTH))
        self.assertIsNone(relevo.pausa_cuenta_s(None))

    # _bench_for: retry_after
    def test_retry_after_largo_se_respeta_hasta_la_ventana_de_cuota(self):
        self.assertEqual(relevo.pausa_cuenta_s(Falla.RATE_LIMIT, 600), 600)
        self.assertEqual(relevo.pausa_cuenta_s(Falla.RATE_LIMIT, 99999), 15 * 60)

    def test_retry_after_corto_no_acorta_la_pausa_base(self):
        self.assertEqual(relevo.pausa_cuenta_s(Falla.RATE_LIMIT, 5), 120)
        self.assertEqual(relevo.pausa_cuenta_s(Falla.QUOTA, 30), 15 * 60)


class CuarentenaTest(unittest.TestCase):
    # test_a_fresh_quota_quarantine_still_skips_the_route
    def test_una_cuarentena_fresca_obliga(self):
        self.assertTrue(relevo.cuarentena_activa(True, 1000.0, 1000.0 + 60))

    # test_an_expired_quota_quarantine_is_retried_and_lifted_by_real_success
    def test_pasada_la_hora_se_vuelve_a_probar(self):
        self.assertFalse(relevo.cuarentena_activa(True, 1000.0, 1000.0 + 3601))

    def test_justo_en_el_horizonte_aun_obliga(self):
        self.assertTrue(relevo.cuarentena_activa(True, 1000.0, 1000.0 + 3600))

    def test_sin_cuarentena_no_obliga(self):
        self.assertFalse(relevo.cuarentena_activa(False, 1000.0, 1000.0))

    def test_sin_fecha_se_asume_vigente(self):
        self.assertTrue(relevo.cuarentena_activa(True, None, 10**9))

    # test_a_throttle_on_an_expired_quarantine_does_not_rearm_it
    def test_un_freno_corto_no_rearma_la_cuarentena(self):
        self.assertFalse(relevo.rearma_cuarentena(Falla.RATE_LIMIT))

    # test_an_expired_quarantine_that_fails_again_is_rearmed / test_..._rearms_on_every_failure_kind
    def test_cualquier_otra_falla_rearma_la_cuarentena(self):
        for f in Falla:
            if f is not Falla.RATE_LIMIT:
                self.assertTrue(relevo.rearma_cuarentena(f), f)


class VocabularioTest(unittest.TestCase):
    def test_traduce_lo_que_el_motor_registra(self):
        quiero = {
            "cuota": Falla.QUOTA,
            "rate_limit": Falla.RATE_LIMIT,
            "auth": Falla.AUTH,
            "transitorio": Falla.TRANSPORT,
            "tiempo": Falla.TIMEOUT,
            "sin_avance": Falla.TIMEOUT,
            "falla": Falla.TASK_FAILURE,
        }
        for kind, falla in quiero.items():
            self.assertIs(relevo.desde_kind(kind), falla, kind)

    def test_lo_que_no_es_culpa_del_modelo_no_se_traduce(self):
        for kind in ("entregado", "veredicto", "corte", "terminado", "inventado"):
            self.assertIsNone(relevo.desde_kind(kind), kind)

    def test_cuota_freno_y_auth_los_devuelve_el_reloj(self):
        self.assertEqual(
            relevo.LAS_RESUELVE_EL_RELOJ, {Falla.QUOTA, Falla.RATE_LIMIT, Falla.AUTH}
        )


# ---- el relevo dentro de machine._elige (eventos reales, sin red ni procesos)

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


def _ev(kind, block, data, ts, i=[0]):
    i[0] += 1
    return Event(i[0], ts, "demo", block, kind, data)


def _pol():
    cuenta = {"sonnet": "anthropic", "glm": "zai", "k3": "kimi"}
    return m.Policy(
        implementadores=("sonnet", "glm", "k3"),
        revisores=("opus",),
        cuenta=cuenta,
        max_parallel={c: 2 for c in set(cuenta.values())},
    )


def _quien(eventos, ahora):
    from motor.plan import parse_plan

    p = parse_plan(PLAN)
    acc = [
        a
        for a in m.decide(m.fold(p, eventos), _pol(), ahora)
        if isinstance(a, m.StartAttempt)
    ]
    return acc[0].modelo if acc else None


def _intento(modelo, kind, n, ts, **extra):
    return [
        _ev(
            "AttemptStarted",
            "A",
            {
                "n": n,
                "role": "implementar",
                "modelo": modelo,
                "proc": {},
                "dir": f"d{n}",
            },
            ts - 5,
        ),
        _ev(
            "AttemptEnded",
            "A",
            {"n": n, "role": "implementar", "outcome": {"kind": kind, **extra}},
            ts,
        ),
    ]


class EligeConRelevoTest(unittest.TestCase):
    def setUp(self):
        self.t = time.time()

    def test_no_salta_al_que_fallo_si_el_siguiente_esta_en_pausa(self):
        pol = m.Policy(
            implementadores=("sonnet", "glm"),
            revisores=("opus",),
            cuenta={"sonnet": "anthropic", "glm": "zai", "opus": "anthropic"},
            max_parallel={"anthropic": 2, "zai": 2},
        )
        evs = _intento("sonnet", "falla", 1, self.t - 10) + [
            _ev("Cooldown", "", {"cuenta": "zai", "hasta": self.t + 3600}, self.t - 5)
        ]
        from motor.plan import parse_plan

        acc = m.decide(m.fold(parse_plan(PLAN), evs), pol, self.t)
        inicios = [a for a in acc if isinstance(a, m.StartAttempt)]
        self.assertEqual([a.modelo for a in inicios], ["sonnet"])
        self.assertFalse([a for a in acc if isinstance(a, m.Notify)])

    def test_sin_historial_empieza_el_primero(self):
        self.assertEqual(_quien([], self.t), "sonnet")

    # test_auth_and_quota_advance_in_order
    def test_tras_una_falla_que_avanza_pasa_al_siguiente_modelo(self):
        self.assertEqual(
            _quien(_intento("sonnet", "transitorio", 1, self.t - 10), self.t), "glm"
        )

    # test_a_timeout_failure_blocks_the_stage_instead_of_advancing
    def test_tras_un_tiempo_agotado_reintenta_el_mismo(self):
        self.assertEqual(
            _quien(_intento("sonnet", "tiempo", 1, self.t - 600), self.t), "sonnet"
        )

    def test_dos_tiempos_seguidos_ya_agotan_el_reintento(self):
        evs = _intento("sonnet", "tiempo", 1, self.t - 100) + _intento(
            "sonnet", "tiempo", 2, self.t - 10
        )
        self.assertEqual(_quien(evs, self.t), "glm")

    def test_si_es_el_unico_modelo_la_falla_que_avanza_se_reintenta(self):
        from motor.plan import parse_plan

        pol = m.Policy(
            implementadores=("sonnet",),
            revisores=("opus",),
            cuenta={"sonnet": "anthropic"},
            max_parallel={"anthropic": 1},
        )
        evs = _intento("sonnet", "falla", 1, self.t - 10)
        acc = [
            a
            for a in m.decide(m.fold(parse_plan(PLAN), evs), pol, self.t)
            if isinstance(a, m.StartAttempt)
        ]
        self.assertEqual([a.modelo for a in acc], ["sonnet"])

    # test_a_fresh_quota_quarantine_still_skips_the_route
    def test_una_cuarentena_por_credenciales_salta_la_cuenta(self):
        self.assertEqual(
            _quien(_intento("sonnet", "auth", 1, self.t - 600), self.t), "glm"
        )

    # test_an_expired_quota_quarantine_is_retried_and_lifted_by_real_success
    def test_pasada_la_hora_la_cuenta_vuelve_a_probarse(self):
        self.assertEqual(
            _quien(_intento("sonnet", "auth", 1, self.t - 3700), self.t), "sonnet"
        )

    def test_un_exito_posterior_levanta_la_cuarentena(self):
        from motor.plan import parse_plan

        evs = _intento("sonnet", "auth", 1, self.t - 600) + _intento(
            "sonnet", "entregado", 2, self.t - 300, sha="a" * 40
        )
        snap = m.fold(parse_plan(PLAN), evs)
        self.assertFalse(m._cuarentena(snap, _pol(), "anthropic", self.t))
        self.assertTrue(
            m._cuarentena(
                m.fold(parse_plan(PLAN), evs[:2]), _pol(), "anthropic", self.t
            )
        )

    def test_otra_falla_rearma_la_cuarentena_pero_un_freno_corto_no(self):
        from motor.plan import parse_plan

        base = _intento("sonnet", "auth", 1, self.t - 3700)
        vieja = m.fold(parse_plan(PLAN), base)
        self.assertFalse(m._cuarentena(vieja, _pol(), "anthropic", self.t), "ya venció")
        rearmada = m.fold(
            parse_plan(PLAN), base + _intento("sonnet", "transitorio", 2, self.t - 100)
        )
        self.assertTrue(m._cuarentena(rearmada, _pol(), "anthropic", self.t))
        freno = m.fold(
            parse_plan(PLAN), base + _intento("sonnet", "rate_limit", 2, self.t - 100)
        )
        self.assertFalse(m._cuarentena(freno, _pol(), "anthropic", self.t))

    def test_la_auth_no_agota_al_modelo_para_siempre(self):
        evs = []
        for n in range(1, 4):
            evs += _intento("sonnet", "auth", n, self.t - 7200 + n * 10)
        self.assertEqual(
            _quien(evs, self.t),
            "sonnet",
            "tres auth viejas no cuentan en la racha: las resuelve el reloj",
        )

    def test_una_pausa_de_cuenta_se_respeta(self):
        evs = [
            _ev("Cooldown", "", {"cuenta": "anthropic", "hasta": self.t + 100}, self.t)
        ]
        self.assertEqual(_quien(evs, self.t), "glm")
        self.assertEqual(_quien(evs, self.t + 200), "sonnet")


class ClasificaFrenoTest(unittest.TestCase):
    # el rate limit corto ya no se confunde con la cuota semanal
    def test_un_429_corto_es_rate_limit(self):
        c = m.classify(1, False, False, "Error 429 too many requests, retry after 30s")
        self.assertEqual((c["kind"], c["retry_after_s"]), ("rate_limit", 30.0))

    def test_la_cuota_semanal_sigue_siendo_cuota(self):
        self.assertEqual(
            m.classify(
                1,
                False,
                False,
                "429 weekly limit reached, resets at 2026-10-23T09:00:00Z",
            )["kind"],
            "cuota",
        )


def _att(modelo, kind, n, role="implementar", **extra):
    return m.Attempt(
        n=n,
        role=role,
        modelo=modelo,
        started=0.0,
        proc={},
        dir=f"d{n}",
        ended={"kind": kind, **extra},
        ended_at=float(n),
    )


class RegresaPorPlaybookTest(unittest.TestCase):
    def setUp(self):
        self.t = time.time()

    def test_el_primer_sin_pstack_solo_playbook_de_un_modelo_es_regreso(self):
        mios = [_att("sonnet", "sin_pstack", 1, solo_playbook=True)]
        self.assertTrue(m.regresa_por_playbook(mios, 0))

    def test_el_segundo_del_mismo_modelo_ya_no(self):
        mios = [
            _att("sonnet", "sin_pstack", 1, solo_playbook=True),
            _att("sonnet", "sin_pstack", 2, solo_playbook=True),
        ]
        self.assertEqual(
            [m.regresa_por_playbook(mios, i) for i in (0, 1)], [True, False]
        )

    def test_el_de_otro_modelo_despues_del_primero_si_es_regreso(self):
        mios = [
            _att("sonnet", "sin_pstack", 1, solo_playbook=True),
            _att("glm", "sin_pstack", 2, solo_playbook=True),
        ]
        self.assertTrue(m.regresa_por_playbook(mios, 1))

    def test_sin_la_marca_u_otro_fin_no_es_regreso(self):
        mios = [_att("sonnet", "sin_pstack", 1), _att("glm", "falla", 2)]
        self.assertEqual(
            [m.regresa_por_playbook(mios, i) for i in (0, 1)], [False, False]
        )

    def test_tras_un_regreso_vuelve_el_mismo_modelo(self):
        evs = _intento("sonnet", "sin_pstack", 1, self.t - 10, solo_playbook=True)
        self.assertEqual(_quien(evs, self.t), "sonnet")

    def test_sin_la_marca_sigue_el_relevo_de_siempre(self):
        evs = _intento("sonnet", "sin_pstack", 1, self.t - 10)
        self.assertEqual(_quien(evs, self.t), "glm")

    def test_el_segundo_solo_playbook_del_mismo_modelo_avanza(self):
        evs = _intento(
            "sonnet", "sin_pstack", 1, self.t - 20, solo_playbook=True
        ) + _intento("sonnet", "sin_pstack", 2, self.t - 10, solo_playbook=True)
        self.assertEqual(_quien(evs, self.t), "glm")

    def test_el_regreso_de_un_modelo_que_no_va_primero_vuelve_a_el(self):
        evs = _intento("sonnet", "sin_pstack", 1, self.t - 20) + _intento(
            "glm", "sin_pstack", 2, self.t - 10, solo_playbook=True
        )
        self.assertEqual(_quien(evs, self.t), "glm")

    def test_el_regreso_no_gasta_la_racha_de_fallas(self):
        from motor.plan import parse_plan

        snap = m.fold(
            parse_plan(PLAN),
            _intento("sonnet", "sin_pstack", 1, self.t - 20, solo_playbook=True)
            + _intento("sonnet", "sin_pstack", 2, self.t - 10, solo_playbook=True),
        )
        bv = snap.blocks["A"]
        self.assertEqual([m._cuenta_falla(a, bv) for a in bv.attempts], [False, True])

    def test_cuenta_falla_con_un_intento_que_no_esta_en_la_lista(self):
        from motor.plan import parse_plan

        snap = m.fold(
            parse_plan(PLAN),
            _intento("sonnet", "sin_pstack", 1, self.t - 10, solo_playbook=True),
        )
        bv = snap.blocks["A"]
        ajeno = _att("sonnet", "sin_pstack", 9, solo_playbook=True)
        self.assertTrue(m._cuenta_falla(ajeno, bv))

    def test_si_el_modelo_no_puede_correr_la_escalera_sigue(self):
        evs = _intento("sonnet", "sin_pstack", 1, self.t - 10, solo_playbook=True) + [
            _ev(
                "Cooldown",
                "",
                {"cuenta": "anthropic", "hasta": self.t + 3600},
                self.t - 5,
            )
        ]
        self.assertEqual(_quien(evs, self.t), "glm")


if __name__ == "__main__":
    unittest.main()
