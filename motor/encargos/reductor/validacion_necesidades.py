"""Validación de escritura de necesidades del encargo."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..dominio import Encargo


def leer_solicitud(d: dict) -> tuple[str, int, tuple[tuple[str, str], ...]] | None:
    try:
        return (
            str(d["operacion"]),
            int(d.get("generacion") or 0),
            tuple((str(k), str(v)) for k, v in (d.get("parametros") or ())),
        )
    except (KeyError, TypeError, ValueError):
        return None


def leer_fases_decision(d: dict) -> tuple[str, ...] | None:
    try:
        return tuple(str(x) for x in (d.get("fases") or ()))
    except TypeError:
        return None


def validar_impedimento(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal

    if not (d.get("clase") or d.get("huella")) or not d.get("causa") or not d.get("condicion"):
        raise TransicionIlegal("el impedimento necesita clase, causa y condicion")


def validar_impedimento_resuelto(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal

    if not (d.get("clase") or d.get("huella")) or not d.get("evidencia"):
        raise TransicionIlegal("el impedimento no se resuelve sin clase y evidencia")


def validar_instrumento_no_disponible(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal

    if not str(d.get("clase") or "").strip():
        raise TransicionIlegal("instrumento_no_disponible sin clase")


def validar_quietud(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal

    if not str(d.get("causa") or "").strip():
        raise TransicionIlegal("la quietud necesita causa")


def validar_via(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal

    if d.get("via") not in ("directa", "durable"):
        raise TransicionIlegal("via desconocida")
    if e.estado in ("cancelado", "terminado"):
        raise TransicionIlegal("el encargo ya cerró")
    if e.proc is not None or e.reserva:
        raise TransicionIlegal("el ejecutor anterior sigue abierto")


def validar_estrategia(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal

    if any(campo not in d for campo in ("estrategia", "motivo", "siguiente")):
        raise TransicionIlegal("estrategia sin datos")


def validar_autoridad(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal

    if "alcanzable" not in d:
        raise TransicionIlegal("autoridad sin alcance")


def validar_solicitud(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal, _CERRADOS

    if e.estado in _CERRADOS or e.estado == "detenido":
        raise TransicionIlegal("el encargo no admite solicitudes en este estado")
    ident = str(d.get("id") or "").strip()
    if not ident:
        raise TransicionIlegal("la solicitud necesita id")
    if any(s.id == ident for s in e.solicitudes):
        raise TransicionIlegal(f"la solicitud {ident} ya existe")
    if str(d.get("fase") or "") != e.fase_activa:
        raise TransicionIlegal("la solicitud no pertenece a la fase activa")
    intento = str(d.get("intento") or "")
    if not intento or (e.intento and intento != e.intento):
        raise TransicionIlegal("la solicitud no pertenece al intento vigente")
    if str(d.get("estado") or "pendiente") not in ("pendiente", "pregunta"):
        raise TransicionIlegal("una solicitud nace pendiente o a la espera del propietario")
    if leer_solicitud(d) is None:
        raise TransicionIlegal("la solicitud necesita operación y parámetros válidos")


def validar_herramienta(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal

    if not any(s.id == str(d.get("id") or "") for s in e.solicitudes):
        raise TransicionIlegal("la herramienta no tiene solicitud")
    if not str(d.get("carpeta") or ""):
        raise TransicionIlegal("la herramienta necesita su carpeta")


def validar_autorizacion(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal

    if not str(d.get("recurso") or "").strip():
        raise TransicionIlegal("la autorización necesita recurso")
    if not str(d.get("operacion") or "").strip():
        raise TransicionIlegal("la autorización necesita la operación explicada")


def validar_decision(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal

    ident = str(d.get("id") or "").strip()
    if not ident:
        raise TransicionIlegal("la decisión necesita id")
    if any(x.id == ident for x in e.decisiones):
        raise TransicionIlegal(f"la decisión {ident} ya existe")
    if not str(d.get("eleccion") or "") or not str(d.get("motivo") or ""):
        raise TransicionIlegal("la decisión necesita elección y motivo")
    if leer_fases_decision(d) is None:
        raise TransicionIlegal("la decisión necesita fases válidas")
