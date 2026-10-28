"""Disponibilidad de una cadena autorizada. No cambia perfiles ni cuentas."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from motor.fallas import clasifica
from motor.relevo import (
    CUARENTENA_REINTENTO_S,
    Falla,
    avanza,
    desde_kind,
    pausa_cuenta_s,
    pausa_pasajera_s,
)

from .dominio import Encargo


def elegir(
    enc: Encargo,
    hora: str,
    disponibles: dict[str, str | tuple[str, ...]],
    *,
    rol: str,
    excluir_modelos: frozenset[str] = frozenset(),
) -> tuple[str, str] | None:
    """Primer agente disponible con un modelo para este rol no excluido.

    `disponibles` mapea agente -> modelos que podría correr para el rol,
    en orden de política. Excluir al autor es por identidad de modelo, no
    por CLI ni sesión: si la cabeza es el autor, se mira el siguiente.
    """
    pausas = {p.agente: p for p in enc.pausas}
    for agente in enc.agentes:
        if agente not in disponibles:
            continue
        pausa = pausas.get(agente)
        if pausa is None or pausa.hasta <= hora:
            valor = disponibles[agente]
            modelos = (valor,) if isinstance(valor, str) else tuple(valor)
            for modelo in modelos:
                if modelo and modelo in excluir_modelos:
                    continue
                return agente, modelo
            continue
        if not pausa.relevo:
            return None
    return None


def proximo(enc: Encargo, hora: str, disponibles: set[str]) -> str:
    for agente in enc.agentes:
        pausa = next((p for p in enc.pausas if p.agente == agente), None)
        if agente in disponibles and pausa and pausa.hasta > hora and not pausa.relevo:
            return pausa.hasta
    plazos = [p.hasta for p in enc.pausas if p.agente in disponibles and p.hasta > hora]
    if plazos:
        return min(plazos)
    ahora = datetime.fromisoformat(hora.replace("Z", "+00:00"))
    return (ahora + timedelta(seconds=CUARENTENA_REINTENTO_S)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def fallo(agente: str, mensaje: str, hora: str, seguidas: int) -> dict:
    datos = clasifica(agente, "", mensaje)
    tipo = desde_kind(datos["kind"]) or Falla.TASK_FAILURE
    ahora = datetime.fromisoformat(hora.replace("Z", "+00:00"))
    pedido = datos.get("retry_after_s")
    pausa = pausa_cuenta_s(tipo, pedido)
    if tipo == Falla.AUTH:
        pausa = CUARENTENA_REINTENTO_S
    segundos = max(pausa or pausa_pasajera_s(seguidas), pedido or 0)
    hasta = ahora + timedelta(seconds=segundos)
    if datos.get("reset_at"):
        try:
            reset = datetime.fromisoformat(datos["reset_at"].replace("Z", "+00:00"))
            reset = reset.replace(tzinfo=reset.tzinfo or timezone.utc)
            if reset > ahora:
                hasta = reset
        except ValueError:
            pass
    return {
        "agente": agente,
        "clase": datos["kind"],
        "causa": mensaje,
        "hasta": hasta.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "relevo": avanza(tipo),
    }
