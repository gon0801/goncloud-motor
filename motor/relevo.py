"""Reglas puras del relevo entre modelos, portadas de `_run_owned` del router de suscripciones de
Hermes (goncloud-hermes, `plugins/goncloud-team/subscriptions/router.py`).

Aquí no hay arriendos, base de datos ni reloj propio: solo "dada esta falla, ¿qué sigue?". El
estado (cuándo falló una cuenta, cuántas veces) lo lleva el log de eventos y lo pasa `machine`.

Lo que el motor hace hoy:
- Qué fallas avanzan al siguiente modelo y cuáles no (un tiempo agotado nunca "pasa la estafeta").
- Una pausa de cuenta por cuota dura lo que pida el proveedor y, si no dice, `cooldown_default_s`
  de la conf (5 h); `COOLDOWN_CUOTA_S` solo es el tope de un freno corto. Un rate limit corto
  pausa 2 min, o lo que pidió el proveedor con ese tope.
- Una cuarentena por credenciales caídas dura una hora y luego se prueba otra vez; solo un éxito
  real la levanta y otra falla (salvo un freno corto) la rearma.
"""

from __future__ import annotations

import enum


class Falla(enum.StrEnum):
    AUTH = "auth"
    QUOTA = "quota"  # la ventana del plan se acabó; tarda en volver
    RATE_LIMIT = "rate_limit"  # freno corto: la cuenta está bien y vuelve en segundos
    MODEL_UNAVAILABLE = "model_unavailable"
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    INVALID_REQUEST = "invalid_request"
    INVALID_TERMINAL = (
        "invalid_terminal"  # el proveedor contestó, pero no en el formato del contrato
    )
    TASK_FAILURE = "task_failure"


# Un timeout NO está aquí y no puede volver a estarlo: los encargos no tienen plazo por modelo
# (decisión de David, 2026-10-19), así que nadie le pasa el trabajo al siguiente "por tiempo".
AVANZAN = frozenset(
    {
        Falla.AUTH,
        Falla.QUOTA,
        Falla.RATE_LIMIT,
        Falla.MODEL_UNAVAILABLE,
        Falla.TRANSPORT,
        Falla.INVALID_TERMINAL,
        Falla.TASK_FAILURE,
    }
)
COOLDOWN_CUOTA_S = 15 * 60
# Un rate limit se despeja en segundos o un minuto. Pausar la cuenta principal toda la ventana de
# cuota por un solo freno empujaba a todos los pasos siguientes a modelos más débiles.
COOLDOWN_RATE_LIMIT_S = 2 * 60
COOLDOWN_S = {Falla.QUOTA: COOLDOWN_CUOTA_S, Falla.RATE_LIMIT: COOLDOWN_RATE_LIMIT_S}

# Cuota, red y hasta credenciales vuelven solas: la cuarentena es un veredicto con vida, no un destierro.
CUARENTENA_REINTENTO_S = 3600

# Cómo llama el motor a lo que terminó mal (`AttemptEnded.outcome.kind`) en el vocabulario de Hermes.
# Lo que no está aquí (entregado, veredicto, corte…) no es culpa del modelo ni de la cuenta.
DE_KIND = {
    "cuota": Falla.QUOTA,
    "rate_limit": Falla.RATE_LIMIT,
    "auth": Falla.AUTH,
    "transitorio": Falla.TRANSPORT,
    "modelo_no_disponible": Falla.MODEL_UNAVAILABLE,
    "tiempo": Falla.TIMEOUT,
    "sin_avance": Falla.TIMEOUT,
    "falla": Falla.TASK_FAILURE,
    "sin_resultado": Falla.INVALID_TERMINAL,
    "veredicto_invalido": Falla.INVALID_TERMINAL,
    "perdido": Falla.TASK_FAILURE,
    "sin_pstack": Falla.TASK_FAILURE,  # no usó poteto-mode: falla del modelo, no de la cuenta
    "sin_repro": Falla.TASK_FAILURE,  # arreglo de bug sin una prueba que falle en la base y pase en la punta
}
# Las que el reloj devuelve solas (pausa o cuarentena): no cuentan en la racha de fallas del modelo.
LAS_RESUELVE_EL_RELOJ = frozenset({Falla.QUOTA, Falla.RATE_LIMIT, Falla.AUTH})
# Fallas pasajeras (red, resultado perdido o ilegible, tiempo): no gastan la escalera del bloque,
# solo ponen al modelo en una pausa corta que crece con cada una seguida.
PASAJERAS = frozenset({"transitorio", "sin_resultado", "perdido", "tiempo"})
PAUSAS_PASAJERAS_S = (60, 300, 900, 1800)


def pausa_pasajera_s(seguidas: int) -> float:
    """1, 5, 15 y 30 min; de la cuarta en adelante se queda en 30."""
    return PAUSAS_PASAJERAS_S[min(max(seguidas, 1), len(PAUSAS_PASAJERAS_S)) - 1]


def desde_kind(kind: str) -> Falla | None:
    return DE_KIND.get(kind)


def avanza(falla: Falla) -> bool:
    """¿El encargo pasa al siguiente modelo?"""
    return falla in AVANZAN


def pausa_cuenta_s(
    falla: Falla | None, retry_after_s: float | None = None
) -> float | None:
    """Cuánto se salta la cuenta en los pasos siguientes tras una falla que avanza (None: nada).
    Un freno que pidió esperar demasiado para dormirlo se respeta, hasta la ventana de cuota."""
    base = COOLDOWN_S.get(falla) if falla is not None else None
    if base is None:
        return None
    if retry_after_s is not None:
        return max(base, min(retry_after_s, COOLDOWN_CUOTA_S))
    return base


def cuarentena_activa(en_cuarentena: bool, desde: float | None, ahora: float) -> bool:
    """La cuarentena obliga solo dentro de su horizonte. Sin fecha se asume vigente."""
    if not en_cuarentena:
        return False
    if desde is None:
        return True
    return ahora - desde <= CUARENTENA_REINTENTO_S


def rearma_cuarentena(falla: Falla | None) -> bool:
    """Cualquier falla rearma el reloj de una cuarentena vencida, salvo un freno corto:
    el plan sirve, solo que no este minuto."""
    return falla is not Falla.RATE_LIMIT
