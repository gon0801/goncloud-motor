"""Reglas puras del relevo entre modelos, portadas de `_run_owned` del router de suscripciones de
Hermes (goncloud-hermes, `plugins/goncloud-team/subscriptions/router.py`).

Aquí no hay arriendos, base de datos ni reloj propio: solo "dada esta falla, ¿qué sigue?". El
estado (cuándo falló una cuenta, cuántas veces) lo lleva el log de eventos y lo pasa `machine`.

Reglas que se conservan tal cual de Hermes:
- Qué fallas avanzan al siguiente modelo y cuáles no (un tiempo agotado nunca "pasa la estafeta").
- Una falla que ya cambió el trabajo no avanza: el siguiente rehacería encima de algo sin reportar.
- Cuota agotada = cuenta en pausa 15 min (o lo que pidió el proveedor, con tope); un rate limit
  corto = 2 min, y se reintenta en el mismo modelo con esperas de 20 s y 60 s.
- Una cuarentena (credenciales, red) dura una hora y luego se prueba otra vez; solo un éxito real
  la levanta y otra falla la rearma.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


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
# Avanzan, pero si el trabajo ya cambió se detienen: el siguiente rehacería sobre algo sin reportar.
BLOQUEA_TRAS_MUTACION = frozenset(
    {Falla.TRANSPORT, Falla.INVALID_TERMINAL, Falla.TASK_FAILURE}
)

COOLDOWN_CUOTA_S = 15 * 60
# Un rate limit se despeja en segundos o un minuto. Pausar la cuenta principal toda la ventana de
# cuota por un solo freno empujaba a todos los pasos siguientes a modelos más débiles.
COOLDOWN_RATE_LIMIT_S = 2 * 60
COOLDOWN_S = {Falla.QUOTA: COOLDOWN_CUOTA_S, Falla.RATE_LIMIT: COOLDOWN_RATE_LIMIT_S}

# Cuota, red y hasta credenciales vuelven solas: la cuarentena es un veredicto con vida, no un destierro.
CUARENTENA_REINTENTO_S = 3600

ESPERAS_RATE_LIMIT_S = (
    20.0,
    60.0,
)  # antes de cada reintento en el mismo modelo; su largo es el presupuesto
ESPERA_MAX_S = (
    120.0  # quien pide esperar más que esto ya está en cuota "en todo menos nombre"
)
DORMIDO_MAX_S = 180.0  # tope de espera acumulada de un mismo encargo

# El proveedor sí contestó: la cuenta está bien aunque este intento no sirviera.
CUENTA_SANA_TRAS = frozenset(
    {
        Falla.INVALID_TERMINAL,
        Falla.MODEL_UNAVAILABLE,
        Falla.RATE_LIMIT,
        Falla.TASK_FAILURE,
        Falla.INVALID_REQUEST,
    }
)

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
}
# Las que el reloj devuelve solas (pausa o cuarentena): no cuentan en la racha de fallas del modelo.
LAS_RESUELVE_EL_RELOJ = frozenset({Falla.QUOTA, Falla.RATE_LIMIT, Falla.AUTH})


def desde_kind(kind: str) -> Falla | None:
    return DE_KIND.get(kind)


def avanza(falla: Falla, mutado: bool = False, heuristica: Falla | None = None) -> bool:
    """¿El encargo pasa al siguiente modelo? `heuristica` es la clase de las reglas cuando otra
    cosa (un clasificador más listo) la reclasificó: ninguna reclasificación saca a una falla
    de bloqueo por mutación."""
    if falla not in AVANZAN:
        return False
    if mutado and (
        falla in BLOQUEA_TRAS_MUTACION or heuristica in BLOQUEA_TRAS_MUTACION
    ):
        return False
    return True


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


def espera_rate_limit(
    reintentos_gastados: int, pista_s: float | None, dormido_s: float
) -> float | None:
    """Segundos de espera antes de reintentar un rate limit en el mismo modelo, o None: avanzar."""
    if reintentos_gastados >= len(ESPERAS_RATE_LIMIT_S):
        return None
    espera = ESPERAS_RATE_LIMIT_S[reintentos_gastados] if pista_s is None else pista_s
    if espera > ESPERA_MAX_S or dormido_s + espera > DORMIDO_MAX_S:
        return None
    return espera


def cuarentena_activa(en_cuarentena: bool, desde: float | None, ahora: float) -> bool:
    """La cuarentena obliga solo dentro de su horizonte. Sin fecha se asume vigente."""
    if not en_cuarentena:
        return False
    if desde is None:
        return True
    return ahora - desde <= CUARENTENA_REINTENTO_S


def cuenta_sana(falla: Falla | None) -> bool:
    """Si tras esta falla la cuenta sigue lista (el proveedor contestó) o hay que sospechar de ella."""
    return falla is None or falla in CUENTA_SANA_TRAS


def rearma_cuarentena(falla: Falla | None) -> bool:
    """Cualquier falla rearma el reloj de una cuarentena vencida, salvo un freno corto:
    el plan sirve, solo que no este minuto."""
    return falla is not Falla.RATE_LIMIT


@dataclass(frozen=True)
class Paso:
    """Qué sigue tras una falla: `devolver` (se detiene y lo decide quien manda), `avanzar` al siguiente
    modelo o `reintentar` en el mismo tras `espera_s`. `pausa_s` es lo que se salta la cuenta después."""

    accion: str
    espera_s: float = 0.0
    pausa_s: float | None = None


def tras_falla(
    falla: Falla,
    *,
    mutado: bool = False,
    heuristica: Falla | None = None,
    retry_after_s: float | None = None,
    reintentos: int = 0,
    dormido_s: float = 0.0,
) -> Paso:
    """La regla completa de `_run_owned` tras un intento fallido, sin tocar nada."""
    clase = heuristica if heuristica is not None else falla
    if not avanza(falla, mutado, heuristica):
        return Paso("devolver")
    if falla is Falla.RATE_LIMIT:
        espera = espera_rate_limit(reintentos, retry_after_s, dormido_s)
        if espera is not None:
            return Paso("reintentar", espera_s=espera)
    # La pausa sigue a la clase de las reglas, nunca a la que impuso otro clasificador.
    return Paso("avanzar", pausa_s=pausa_cuenta_s(clase, retry_after_s))
