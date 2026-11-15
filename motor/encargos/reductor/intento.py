"""Manejadores del reductor extraídos de dominio."""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..dominio import Encargo


def reserva(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import TransicionIlegal, _CERRADOS, _ajustes_de_la_reserva, replace

    if e.proc is not None or e.reserva:
        raise TransicionIlegal("ya hay un escritor")
    if (
        not e.intentos_nuevos
        or e.estado in _CERRADOS
        or e.estado == "esperando_reintento"
    ):
        raise TransicionIlegal("no hay autorización para otro intento")
    return replace(
        e,
        reserva=True,
        ajustes_reservados=_ajustes_de_la_reserva(e, d),
        conductor=str(d.get("conductor") or e.conductor),
        candidato=str(d.get("candidato") or ""),
        host=str(d.get("host") or socket.gethostname()),
        rol=str(d.get("rol") or e.rol or "responsable"),
        generacion=int(d.get("generacion") or (e.generacion + 1)),
        estado="ejecutando" if e.estado == "recibido" else e.estado,
        ultimo_hecho=e.ultimo_hecho or "intento reservado",
        hora=hora or e.hora,
    )

def reserva_cerrada(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace

    return replace(
        e,
        reserva=False,
        candidato="",
        ajustes_reservados=e.ajustes_vistos,
        hora=hora or e.hora,
    )

def conductor(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace

    return replace(
        e, conductor=d["token"], candidato="", host=d["host"], hora=hora or e.hora
    )

def intento_abierto(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import Proc, TransicionIlegal, _CERRADOS, replace

    if not e.intentos_nuevos or e.estado in _CERRADOS:
        raise TransicionIlegal("no hay autorización para otro intento")
    if e.proc is not None:
        raise TransicionIlegal("ya hay un escritor")
    rol_intento = str(d.get("rol") or e.rol or "responsable")
    poteto = str(d.get("poteto_digest") or "")
    if "poteto_digest" in d and rol_intento == "responsable" and not poteto:
        raise TransicionIlegal("intento del responsable sin poteto verificado")
    modelo = str(d.get("modelo") or "")
    modelos = e.modelos
    modelos_fase = e.modelos_fase
    if modelo:
        registro_modelo = (rol_intento, str(d.get("agente") or e.agente), modelo)
        if not modelos or modelos[-1] != registro_modelo:
            modelos = modelos + (registro_modelo,)
        if not modelos_fase or modelos_fase[-1] != registro_modelo:
            modelos_fase = modelos_fase + (registro_modelo,)
    viva = (
        {"viva_revisor": True}
        if rol_intento == "revisor"
        else {"viva_responsable": True}
    )
    return replace(
        e,
        reserva=False,
        ajustes_vistos=max(e.ajustes_vistos, e.ajustes_reservados),
        rol=rol_intento,
        agente=str(d.get("agente") or e.agente),
        modelos=modelos,
        modelos_fase=modelos_fase,
        sesion_modo=str(d.get("sesion_modo") or e.sesion_modo),
        poteto_digest=poteto or e.poteto_digest,
        intento=d["id"],
        intento_interrumpido=False,
        sesion=d.get("sesion") or e.sesion,
        proc=Proc(
            pid=int(d["pid"]),
            inicio=str(d["inicio"]),
            boot_id=str(d.get("boot_id") or ""),
            job=str(d.get("job") or ""),
            host=str(d.get("host") or socket.gethostname()),
        ),
        herramientas_observables=bool(d.get("herramientas_observables", True)),
        hora=hora or e.hora,
        ultimo_hecho=e.ultimo_hecho or "intento abierto",
        **viva,
    )

def actividad(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _CERRADOS, replace

    if e.estado in _CERRADOS or e.estado == "esperando_reintento":
        return e
    if not d.get("observada"):
        return e
    return replace(
        e,
        estado="ejecutando",
        ultimo_hecho=d["hecho"],
        hora=hora or e.hora,
        herramienta=str(d.get("herramienta") or ""),
        motivo="",
    )

def colgada(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace

    return replace(
        e,
        hora=hora or e.hora,
        ultimo_hecho=d["hecho"],
        motivo="sesión colgada",
        siguiente="cerrar el escritor y no abrir otro hasta verlo ausente",
        causa=d.get("causa") or e.causa,
    )

def intento_cerrado(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace

    sesion_runtime = str(d.get("sesion_runtime") or "")
    cambios = {}
    if sesion_runtime:
        if e.rol == "revisor":
            cambios["sesion_revisor"] = sesion_runtime
        else:
            cambios["sesion_responsable"] = sesion_runtime
    return replace(
        e,
        proc=None,
        herramienta="",
        reserva=False,
        ajustes_reservados=e.ajustes_vistos,
        **cambios,
    )

def sesion_perdida(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace

    if (d.get("rol") or e.rol) == "revisor":
        return replace(
            e,
            sesion_revisor="",
            viva_revisor=False,
            epoca_revisor=e.epoca_revisor + 1,
        )
    return replace(
        e,
        sesion_responsable="",
        viva_responsable=False,
        epoca_responsable=e.epoca_responsable + 1,
    )

def conductor_fase(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import FASE_IMPLICITA, TransicionIlegal

    fase = str(d.get("fase") or "")
    token = str(d.get("token") or "")
    if not fase:
        raise TransicionIlegal("asociación sin fase")
    if e.conductor and token != e.conductor:
        raise TransicionIlegal("la asociación no corresponde al conductor")
    if e.fases and fase != FASE_IMPLICITA and all(f.id != fase for f in e.fases):
        raise TransicionIlegal("asociación a una fase inexistente")
    return e
