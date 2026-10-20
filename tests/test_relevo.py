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

    # test_transport_failure_without_mutation_advances
    def test_transporte_sin_mutacion_avanza(self):
        self.assertTrue(relevo.avanza(Falla.TRANSPORT, mutado=False))

    # test_transport_failure_after_mutation_blocks_the_stage
    def test_transporte_tras_mutacion_bloquea(self):
        self.assertFalse(relevo.avanza(Falla.TRANSPORT, mutado=True))

    # test_task_failure_without_mutation_advances_to_the_next_slot
    def test_falla_de_tarea_sin_mutacion_avanza(self):
        self.assertTrue(relevo.avanza(Falla.TASK_FAILURE))

    # test_task_failure_after_mutation_blocks_the_stage
    def test_falla_de_tarea_tras_mutacion_bloquea(self):
        self.assertFalse(relevo.avanza(Falla.TASK_FAILURE, mutado=True))

    # test_a_rejected_envelope_advances_to_the_next_slot_when_nothing_changed
    def test_sobre_rechazado_sin_cambios_avanza(self):
        self.assertTrue(relevo.avanza(Falla.INVALID_TERMINAL))

    # test_a_rejected_envelope_blocks_once_the_workspace_was_mutated
    def test_sobre_rechazado_con_cambios_bloquea(self):
        self.assertFalse(relevo.avanza(Falla.INVALID_TERMINAL, mutado=True))

    # test_non_advancing_failure_blocks_the_stage
    def test_peticion_invalida_no_avanza(self):
        self.assertFalse(relevo.avanza(Falla.INVALID_REQUEST))

    def test_cuota_y_auth_avanzan_aunque_haya_mutacion(self):
        # solo transporte, sobre inválido y falla de tarea se frenan por mutación (_BLOCK_AFTER_MUTATION)
        for f in (Falla.QUOTA, Falla.RATE_LIMIT, Falla.AUTH, Falla.MODEL_UNAVAILABLE):
            self.assertTrue(relevo.avanza(f, mutado=True), f)

    # F12: la reclasificación de JEV nunca saca a una falla del bloqueo por mutación
    def test_la_clase_de_las_reglas_manda_sobre_la_reclasificada(self):
        self.assertFalse(
            relevo.avanza(Falla.QUOTA, mutado=True, heuristica=Falla.TRANSPORT)
        )
        self.assertTrue(
            relevo.avanza(Falla.QUOTA, mutado=False, heuristica=Falla.TRANSPORT)
        )

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

    # test_plan_exhaustion_advances_at_once_and_benches_the_account
    def test_cuota_avanza_de_inmediato_y_pausa(self):
        paso = relevo.tras_falla(Falla.QUOTA)
        self.assertEqual(
            (paso.accion, paso.espera_s, paso.pausa_s), ("avanzar", 0.0, 900)
        )

    # F14: la pausa sigue a la clase de las reglas, no a la de JEV
    def test_la_pausa_sigue_a_la_clase_de_las_reglas(self):
        paso = relevo.tras_falla(Falla.TRANSPORT, heuristica=Falla.QUOTA)
        self.assertEqual((paso.accion, paso.pausa_s), ("avanzar", 900))
        paso = relevo.tras_falla(Falla.QUOTA, heuristica=Falla.TRANSPORT)
        self.assertEqual(
            (paso.accion, paso.pausa_s),
            ("avanzar", None),
            "un transporte leído como cuota no pausa nada",
        )


class RateLimitTest(unittest.TestCase):
    # test_a_passing_throttle_is_retried_on_the_same_route
    def test_un_freno_pasajero_se_reintenta_en_la_misma_ruta(self):
        paso = relevo.tras_falla(Falla.RATE_LIMIT)
        self.assertEqual((paso.accion, paso.espera_s), ("reintentar", 20.0))

    def test_el_segundo_reintento_espera_60(self):
        self.assertEqual(relevo.espera_rate_limit(1, None, 20), 60.0)

    def test_se_acaba_el_presupuesto_de_reintentos(self):
        self.assertIsNone(relevo.espera_rate_limit(2, None, 80))
        self.assertEqual(
            relevo.tras_falla(Falla.RATE_LIMIT, reintentos=2, dormido_s=80).accion,
            "avanzar",
        )

    # test_a_throttle_waits_as_long_as_the_vendor_asks_within_the_lease
    def test_espera_lo_que_pide_el_proveedor_dentro_del_limite(self):
        self.assertEqual(relevo.espera_rate_limit(0, 90.0, 0), 90.0)
        self.assertEqual(relevo.espera_rate_limit(0, 120.0, 0), 120.0)

    # test_a_throttle_longer_than_the_lease_allows_advances_without_waiting
    def test_un_freno_mas_largo_que_el_limite_avanza_sin_esperar(self):
        self.assertIsNone(relevo.espera_rate_limit(0, 121.0, 0))
        paso = relevo.tras_falla(Falla.RATE_LIMIT, retry_after_s=300)
        self.assertEqual((paso.accion, paso.pausa_s), ("avanzar", 300))

    # test_a_throttle_wait_never_outlasts_the_stage_lease
    def test_la_espera_nunca_pasa_del_limite_por_reintento(self):
        for pista in (1.0, 60.0, 120.0):
            self.assertLessEqual(
                relevo.espera_rate_limit(0, pista, 0), relevo.ESPERA_MAX_S
            )

    # test_a_throttle_on_every_slot_stops_waiting_once_the_stage_budget_is_spent
    def test_con_el_presupuesto_del_encargo_gastado_ya_no_espera(self):
        self.assertIsNone(relevo.espera_rate_limit(0, None, 170))  # 170 + 20 > 180
        self.assertEqual(relevo.espera_rate_limit(0, None, 160), 20.0)  # 160 + 20 = 180

    def test_un_freno_tras_mutacion_se_detiene_si_la_clase_bloquea(self):
        # test_a_throttle_after_a_mutation_retries_the_route_as_a_continuation: el rate limit SÍ reintenta
        paso = relevo.tras_falla(Falla.RATE_LIMIT, mutado=True)
        self.assertEqual(paso.accion, "reintentar")

    def test_simula_la_cadena_de_esperas_de_un_freno_persistente(self):
        esperas, dormido, reintentos = [], 0.0, 0
        while True:
            paso = relevo.tras_falla(
                Falla.RATE_LIMIT, reintentos=reintentos, dormido_s=dormido
            )
            if paso.accion != "reintentar":
                break
            esperas.append(paso.espera_s)
            dormido += paso.espera_s
            reintentos += 1
        self.assertEqual(esperas, [20.0, 60.0])
        self.assertEqual((paso.accion, paso.pausa_s), ("avanzar", 120))


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

    # test_model_unavailable_keeps_the_account_ready / test_task_failure_keeps_a_shared_adapter_ready...
    def test_estas_fallas_dejan_la_cuenta_sana(self):
        for f in (
            Falla.MODEL_UNAVAILABLE,
            Falla.INVALID_TERMINAL,
            Falla.TASK_FAILURE,
            Falla.RATE_LIMIT,
            Falla.INVALID_REQUEST,
            None,
        ):
            self.assertTrue(relevo.cuenta_sana(f), f)

    def test_estas_fallas_hacen_sospechar_de_la_cuenta(self):
        for f in (Falla.AUTH, Falla.QUOTA, Falla.TRANSPORT, Falla.TIMEOUT):
            self.assertFalse(relevo.cuenta_sana(f), f)


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
            _quien(_intento("sonnet", "tiempo", 1, self.t - 10), self.t), "sonnet"
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

    def test_retry_after_en_minutos_y_milisegundos(self):
        self.assertEqual(m.retry_after_s("retry-after: 2 minutes"), 120.0)
        self.assertEqual(m.retry_after_s("retry after 1500ms"), 1.5)
        self.assertIsNone(m.retry_after_s("rate limit exceeded"))


if __name__ == "__main__":
    unittest.main()
