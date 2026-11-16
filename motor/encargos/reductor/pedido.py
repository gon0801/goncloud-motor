"""Pedido y órdenes aplicados al encargo."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from ..catalogo_agentes import identidad_modelo
from .errores import HechoInaplicable, leer_carga

if TYPE_CHECKING:
    from ..dominio import Encargo


def recibo(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    return e


def ruta_agentes(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    agentes = leer_carga(nuevo, lambda: tuple(d["agentes"]))
    return replace(e, agentes=agentes)


def contexto_entrega(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import TransicionIlegal

    base = str(d.get("base") or "").strip()
    if not base or (e.entrega.base and e.entrega.base != base):
        if not nuevo:
            raise HechoInaplicable
        raise TransicionIlegal("el contexto no puede cambiar una base autorizada")
    return replace(
        e, entrega=replace(e.entrega, base=base),
        causa="", motivo="", siguiente="seguir el mismo encargo", hora=hora or e.hora,
    )


def proveedor_fallo(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import PausaAgente, _con_pausa

    pausa = leer_carga(
        nuevo, lambda: PausaAgente(
            d["agente"], d["hasta"], d["causa"], d["relevo"],
            str(d.get("modelo") or ""),
        ),
    )
    return replace(
        e,
        pausas=_con_pausa(e.pausas, pausa),
        causa=d["causa"],
    )


def proveedor_disponible(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    agente = leer_carga(nuevo, lambda: d["agente"]) if e.pausas else None
    modelo = str(d.get("modelo") or "")
    if not modelo:
        return replace(
            e, pausas=tuple(p for p in e.pausas if p.agente != agente)
        )
    return replace(
        e,
        pausas=tuple(
            p
            for p in e.pausas
            if not (
                p.agente == agente
                and p.modelo
                and identidad_modelo(p.modelo) == identidad_modelo(modelo)
            )
        ),
    )


def falla_sesion_local(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    # Un fallo local de la sesión no pausa al proveedor: la causa real queda
    # dicha en el encargo y la espera la pone el evento `espera` que sigue.
    return replace(e, causa=str(d.get("causa") or ""))


def orden(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import TransicionIlegal, _orden

    if not nuevo:
        leer_carga(nuevo, lambda: d["accion"])
    try:
        return _orden(e, d, hora)
    except TransicionIlegal as exc:
        if nuevo:
            raise
        raise HechoInaplicable from exc


def ajuste(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import (
        TransicionIlegal,
        _cerrar_hallazgos,
        _con_impedimentos,
        _ids_cerrados_por_texto,
        _nueva_info_del_propietario,
        _reabrir_en_el_pr,
        _resolver_cierres,
        _soltar_impedimento_de_inspeccion,
    )

    if not nuevo:
        leer_carga(nuevo, lambda: (d["texto"], tuple(d.get("cierra") or ())))
    if e.estado == "terminado":
        fase = e.fase_actual
        # Con el PR abierto la corrección vuelve al responsable en la misma
        # rama; integrado o desplegado ya no hay PR que corregir.
        if fase is None or not e.pr_numero or e.nivel_alcanzado in ("integrado", "desplegado"):
            if not nuevo:
                raise HechoInaplicable
            raise TransicionIlegal("el encargo ya terminó; no admite correcciones")
        e = _reabrir_en_el_pr(
            e,
            fase,
            "corrección del propietario sobre el PR abierto",
            "aplicar la corrección en la misma rama y el mismo PR",
            hora,
        )
    if e.orden_parada == "cancelar":
        if not nuevo:
            raise HechoInaplicable
        raise TransicionIlegal("el encargo está cancelado; no admite correcciones")
    # Un ajuste trae información nueva del operador. Sólo levanta las causas
    # que el host no puede comprobar: las demás siguen hasta verificarse.
    # Con el trabajo en curso no toca el reloj del último hecho: la
    # detección de una sesión colgada no se reinicia por una nota.
    en_curso = e.estado in ("recibido", "ejecutando", "conciliando")
    e = _con_impedimentos(
        e,
        tuple(i for i in e.impedimentos if i.verificable),
        "" if en_curso else hora,
    )
    fase = e.fase_actual
    abiertos = (
        [h for h in fase.hallazgos if h.estado == "abierto"]
        if fase is not None
        else []
    )
    ids = _resolver_cierres(d.get("cierra") or (), abiertos)
    if not ids:
        ids = _ids_cerrados_por_texto(d["texto"], abiertos)
    e, cerrados = _cerrar_hallazgos(e, ids, str(d.get("evidencia") or ""))
    e = replace(e, ajustes=e.ajustes + (d["texto"],))
    if cerrados:
        e = replace(
            e,
            ultimo_hecho=(
                "el propietario declaro cerrados: " + ", ".join(cerrados)
            ),
        )
    e = _soltar_impedimento_de_inspeccion(_nueva_info_del_propietario(e))
    if e.estado == "detenido":
        return replace(
            e,
            hora=hora,
            ultimo_hecho="ajuste incorporado con el trabajo detenido",
            siguiente="seguir detenido hasta continuar",
        )
    return e


def procedimiento(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    nota, ajustes = leer_carga(
        nuevo, lambda: (
            d["nota"],
            int(d.get("ajustes_anotados", d.get("ajustes_vistos", e.ajustes_anotados))),
        ),
    )
    return replace(
        e,
        procedimiento=e.procedimiento + (nota,),
        # La nota de la versión anterior traía este conteo como
        # "ajustes_vistos"; sólo la escribía la nota sin intento.
        ajustes_anotados=ajustes,
        hora=hora or e.hora,
    )


def migracion(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    return replace(
        e,
        procedimiento=e.procedimiento + (str(d.get("nota") or "migración"),),
        hora=hora or e.hora,
    )
