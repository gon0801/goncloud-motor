"""Validación de escritura de esperas y observaciones."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..dominio import Encargo, Fase


def leer_incorporado(d: dict) -> int | None:
    try:
        generacion = int(d.get("generacion") or 0)
    except (TypeError, ValueError):
        return None
    return generacion if generacion >= 0 else None


def leer_quietud_vigilada(d: dict) -> tuple[int, int, float, float] | None:
    try:
        return (
            int(d.get("evento") or 0),
            int(d.get("vueltas") or 0),
            float(d.get("desde") or 0),
            float(d.get("visto") or 0),
        )
    except (TypeError, ValueError):
        return None


def leer_cierre(
    e: Encargo, fase: Fase, evidencia: str,
) -> tuple[tuple[Fase, ...], Fase | None, tuple[Fase, ...]]:
    from ..dominio import _fase_disponible, _preparar_con_entrada, replace

    preparada = replace(fase, estado="preparada", evidencia=evidencia)
    firma = fase.producto.firma if fase.producto else ""
    fases = _preparar_con_entrada(e.fases, preparada, firma)
    disponible = _fase_disponible(fases)
    pendientes = tuple(f for f in fases if f.estado != "preparada")
    return fases, disponible, pendientes


def validar_incorporado(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal

    if leer_incorporado(d) is None:
        raise TransicionIlegal("la incorporación necesita una generación")


def validar_quietud_vigilada(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal

    if leer_quietud_vigilada(d) is None:
        raise TransicionIlegal("la quietud vigilada necesita números")


def validar_espera(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal, _CERRADOS

    if e.estado not in _CERRADOS and (not d.get("motivo") or not d.get("reanuda_en")):
        raise TransicionIlegal("la espera necesita motivo y reanudación")


def validar_afirmacion(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal

    if "texto" not in d:
        raise TransicionIlegal("afirmación sin texto")


def validar_efecto(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal

    if "id" not in d or "estado" not in d:
        raise TransicionIlegal("efecto sin id o estado")


def validar_comprobacion(e: Encargo, d: dict) -> None:
    from ..dominio import (
        TransicionIlegal, _espera_despliegue_pendiente, asegurar_fase,
        nivel_cumplido, queda_trabajo,
    )

    clase = d.get("clase")
    if clase not in (
        "producto_aprobado", "confirmado", "cierre_comprobado",
        "instrumento", "defecto", "no_concluyente",
    ):
        raise TransicionIlegal(f"comprobación desconocida: {clase}")
    if clase not in ("confirmado", "cierre_comprobado"):
        return
    if not str(d.get("evidencia") or ""):
        raise TransicionIlegal("confirmado sin evidencia")
    e = asegurar_fase(e)
    pedida = str(d.get("fase") or "")
    if pedida and pedida != e.fase_activa:
        raise TransicionIlegal("el cierre pertenece a otra fase")
    intento = str(d.get("intento") or "")
    if intento and e.intento and intento != e.intento:
        raise TransicionIlegal("el cierre pertenece a otro intento")
    if e.fase_actual is None:
        raise TransicionIlegal("no hay fase que cerrar")
    if e.entrega.nivel != "terminado" and not nivel_cumplido(e):
        return
    if e.estado in ("cancelado", "deteniendo", "detenido"):
        return
    fases, disponible, pendientes = leer_cierre(e, e.fase_actual, str(d["evidencia"]))
    if (disponible is not None or pendientes and queda_trabajo(fases)
        or _espera_despliegue_pendiente(e, fases)):
        return
    # Quien cierra anota cuántas correcciones cubre (vistas o dichas en la
    # nota). Una que entró después no la atendió nadie: el registro rechaza
    # el cierre en la misma transacción en que lo escribe, haya o no un
    # intento que pueda recibirla, así que no hay carrera con corregir.
    # Sólo al escribir: un cierre ya registrado se relee tal como se
    # aceptó, también los que escribió la versión anterior.
    vistas = d.get("ajustes_vistos")
    if vistas is not None:
        try:
            n = int(vistas)
        except (TypeError, ValueError):
            raise TransicionIlegal("ajustes vistos con número inválido") from None
        if len(e.ajustes) > n:
            raise TransicionIlegal("llegó una corrección durante el cierre")
