"""Guardias de escritura para hechos de revisión."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..dominio import Encargo


def validar_inspeccion_solicitud(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal, _fase_de_evento

    if _fase_de_evento(e, d).fase_actual is None:
        raise TransicionIlegal("no hay fase para la inspección")
    if not str(d.get("solicitud") or "").strip():
        raise TransicionIlegal("la inspección necesita su solicitud")


def validar_inspeccion_entregada(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal, _fase_de_evento

    if _fase_de_evento(e, d).fase_actual is None:
        raise TransicionIlegal("no hay fase para la entrega de la inspección")
    if not str(d.get("marca") or "").strip():
        raise TransicionIlegal("la entrega de la inspección necesita su marca")


def validar_resolucion(e: Encargo, d: dict) -> None:
    from ..dominio import ESTADOS_SOLICITUD, TransicionIlegal

    ident = str(d.get("id") or "")
    sol = next((s for s in e.solicitudes if s.id == ident), None)
    if sol is None:
        raise TransicionIlegal("la resolución no tiene solicitud")
    if str(d.get("fase") or "") != sol.fase or str(d.get("intento") or "") != sol.intento:
        raise TransicionIlegal("la resolución no corresponde a la fase e intento de origen")
    if sol.estado in ("resuelta", "denegada"):
        raise TransicionIlegal("la solicitud ya tiene resolución definitiva")
    estado = str(d.get("estado") or "")
    if estado not in ESTADOS_SOLICITUD or estado == "pendiente":
        raise TransicionIlegal("estado de resolución inválido")
