"""Validación de escritura de los hechos de intento."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..dominio import Encargo


def leer_reserva(e: Encargo, d: dict) -> tuple[int, int] | None:
    from ..dominio import _ajustes_de_la_reserva

    try:
        return (
            _ajustes_de_la_reserva(e, d),
            int(d.get("generacion") or (e.generacion + 1)),
        )
    except (TypeError, ValueError):
        return None


def leer_intento_abierto(d: dict) -> tuple[str, int, str] | None:
    try:
        return d["id"], int(d["pid"]), d["inicio"]
    except (KeyError, TypeError, ValueError):
        return None


def validar_reserva(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal, _CERRADOS

    if e.proc is not None or e.reserva:
        raise TransicionIlegal("ya hay un escritor")
    if not e.intentos_nuevos or e.estado in _CERRADOS or e.estado == "esperando_reintento":
        raise TransicionIlegal("no hay autorización para otro intento")
    if leer_reserva(e, d) is None:
        raise TransicionIlegal("reserva con número inválido")


def validar_intento_abierto(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal, _CERRADOS

    if not e.intentos_nuevos or e.estado in _CERRADOS:
        raise TransicionIlegal("no hay autorización para otro intento")
    if e.proc is not None:
        raise TransicionIlegal("ya hay un escritor")
    rol = str(d.get("rol") or e.rol or "responsable")
    if "poteto_digest" in d and rol == "responsable" and not str(d.get("poteto_digest") or ""):
        raise TransicionIlegal("intento del responsable sin poteto verificado")
    if leer_intento_abierto(d) is None:
        raise TransicionIlegal("intento_abierto sin id, pid o inicio")


def validar_conductor_fase(e: Encargo, d: dict) -> None:
    from ..dominio import FASE_IMPLICITA, TransicionIlegal

    fase = str(d.get("fase") or "")
    token = str(d.get("token") or "")
    if not fase:
        raise TransicionIlegal("asociación sin fase")
    if e.conductor and token != e.conductor:
        raise TransicionIlegal("la asociación no corresponde al conductor")
    if e.fases and fase != FASE_IMPLICITA and all(f.id != fase for f in e.fases):
        raise TransicionIlegal("asociación a una fase inexistente")


def validar_conductor(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal

    if "token" not in d or "host" not in d:
        raise TransicionIlegal("conductor sin token o host")


def validar_actividad(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal, _CERRADOS

    if e.estado not in _CERRADOS and e.estado != "esperando_reintento":
        if d.get("observada") and "hecho" not in d:
            raise TransicionIlegal("actividad observada sin hecho")


def validar_colgada(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal

    if "hecho" not in d:
        raise TransicionIlegal("colgada sin hecho")
