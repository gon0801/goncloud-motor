"""Guardias de escritura de pedido y órdenes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..dominio import Encargo


def validar_ruta_agentes(e: Encargo, d: dict) -> None:
    tuple(d["agentes"])


def validar_contexto_entrega(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal

    base = str(d.get("base") or "").strip()
    if not base or e.entrega.base and e.entrega.base != base:
        raise TransicionIlegal("el contexto no puede cambiar una base autorizada")


def validar_proveedor_fallo(e: Encargo, d: dict) -> None:
    from ..dominio import PausaAgente

    PausaAgente(d["agente"], d["hasta"], d["causa"], d["relevo"],
               str(d.get("modelo") or ""))


def validar_proveedor_disponible(e: Encargo, d: dict) -> None:
    if e.pausas:
        d["agente"]


def validar_orden(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal

    if e.estado == "cancelado":
        raise TransicionIlegal("el encargo está cancelado")
    accion = d["accion"]
    if accion == "reabrir":
        fase = e.cierre_forzado_fase
        if e.estado != "terminado" or fase is None:
            raise TransicionIlegal("reabrir requiere un cierre forzado vigente")
        if d.get("fase") != fase.id or d.get("cierre") != e.cierre_forzado_huella:
            raise TransicionIlegal("la orden no corresponde al cierre forzado vigente")
    elif accion == "parar":
        if e.orden_parada == "cancelar":
            raise TransicionIlegal("no se para un encargo cancelado")
        if e.estado == "terminado":
            raise TransicionIlegal("no se para un encargo terminado")
    elif accion == "cancelar":
        if e.estado == "terminado":
            raise TransicionIlegal("el encargo ya terminó")
    elif accion == "continuar":
        rearmar = e.estado == "recibido" and e.proc is None and not e.reserva and e.intentos_nuevos
        if e.estado != "detenido" and not rearmar:
            raise TransicionIlegal("continuar espera el estado detenido o una pausa sin proceso")
    else:
        raise TransicionIlegal(f"orden desconocida: {accion}")


def validar_ajuste(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal, _resolver_cierres

    if e.estado == "terminado":
        fase = e.fase_actual
        if fase is None or not e.pr_numero or e.nivel_alcanzado in ("integrado", "desplegado"):
            raise TransicionIlegal("el encargo ya terminó; no admite correcciones")
    if e.orden_parada == "cancelar":
        raise TransicionIlegal("el encargo está cancelado; no admite correcciones")
    _resolver_cierres(d.get("cierra") or (), [])
    d["texto"]


def validar_procedimiento(e: Encargo, d: dict) -> None:
    d["nota"]
    int(d.get("ajustes_anotados", d.get("ajustes_vistos", e.ajustes_anotados)))
