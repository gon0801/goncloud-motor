"""Manejadores del reductor extraídos de dominio."""

from __future__ import annotations
from ..encargo import evolucionar

import socket
from typing import TYPE_CHECKING

from .errores import leer_carga

if TYPE_CHECKING:
    from ..dominio import Encargo


def reserva(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from .validacion_intento import leer_reserva

    carga = leer_carga(nuevo, lambda: leer_reserva(e, d))
    if carga is None:
        return e
    return evolucionar(
        e,
        reserva=True,
        ajustes_reservados=carga[0],
        conductor=str(d.get("conductor") or e.conductor),
        candidato=str(d.get("candidato") or ""),
        host=str(d.get("host") or socket.gethostname()),
        rol=str(d.get("rol") or e.rol or "responsable"),
        generacion=carga[1],
        estado="ejecutando" if e.estado == "recibido" else e.estado,
        ultimo_hecho=e.ultimo_hecho or "intento reservado",
        hora=hora or e.hora,
    )

def reserva_cerrada(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    return evolucionar(
        e,
        reserva=False,
        candidato="",
        ajustes_reservados=e.ajustes_vistos,
        hora=hora or e.hora,
    )

def conductor(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace

    return replace(
        e, conductor=d.get("token", e.conductor), candidato="",
        host=d.get("host", e.host), hora=hora or e.hora,
    )

def intento_abierto(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import Proc
    from .validacion_intento import leer_intento_abierto

    carga = leer_carga(nuevo, lambda: leer_intento_abierto(d))
    if carga is None:
        return e
    rol_intento = str(d.get("rol") or e.rol or "responsable")
    poteto = str(d.get("poteto_digest") or "")
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
    return evolucionar(
        e,
        reserva=False,
        ajustes_vistos=max(e.ajustes_vistos, e.ajustes_reservados),
        rol=rol_intento,
        agente=str(d.get("agente") or e.agente),
        modelos=modelos,
        modelos_fase=modelos_fase,
        sesion_modo=str(d.get("sesion_modo") or e.sesion_modo),
        poteto_digest=poteto or e.poteto_digest,
        intento=carga[0],
        intento_interrumpido=False,
        sesion=d.get("sesion") or e.sesion,
        proc=Proc(
            pid=carga[1],
            inicio=str(carga[2]),
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
    from ..dominio import _CERRADOS

    if e.estado in _CERRADOS or e.estado == "esperando_reintento":
        return e
    if not d.get("observada"):
        return e
    return evolucionar(
        e,
        estado="ejecutando",
        ultimo_hecho=d.get("hecho", ""),
        hora=hora or e.hora,
        herramienta=str(d.get("herramienta") or ""),
        motivo="",
    )

def colgada(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace

    return replace(
        e,
        hora=hora or e.hora,
        ultimo_hecho=d.get("hecho", ""),
        motivo="sesión colgada",
        siguiente="cerrar el escritor y no abrir otro hasta verlo ausente",
        causa=d.get("causa") or e.causa,
    )

def intento_cerrado(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    sesion_runtime = str(d.get("sesion_runtime") or "")
    cambios = {}
    if sesion_runtime:
        if e.rol == "revisor":
            cambios["sesion_revisor"] = sesion_runtime
        else:
            cambios["sesion_responsable"] = sesion_runtime
    return evolucionar(
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
    return e
