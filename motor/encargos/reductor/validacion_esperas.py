"""Validación de escritura de esperas y observaciones."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..dominio import Encargo, Fase
    from ..tabla_esperas import FilaEspera


def leer_incorporado(d: dict) -> int | None:
    try:
        generacion = int(d.get("generacion") or 0)
    except (TypeError, ValueError, OverflowError):
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
    except (TypeError, ValueError, OverflowError):
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
    from ..tabla_esperas import ESPERAS

    if e.estado not in _CERRADOS and (not d.get("motivo") or not d.get("reanuda_en")):
        raise TransicionIlegal("la espera necesita motivo y reanudación")
    if "clase" in d and (
        not isinstance(d["clase"], str) or d["clase"] not in ESPERAS
        or not isinstance(d.get("abierta"), str) or not d["abierta"]
        or not isinstance(d.get("tope"), str) or not d["tope"]
    ):
        raise TransicionIlegal("la espera necesita clase, apertura y tope válidos")


def leer_tope(e: Encargo, d: dict) -> FilaEspera | None:
    from ..dominio import impedimento_bloquea_trabajo
    from ..situacion import EsperandoReintento
    from ..tabla_esperas import ESPERAS

    if not isinstance(d, dict):
        return None
    clase, abierta, tope = d.get("clase"), d.get("abierta"), d.get("tope")
    if not isinstance(clase, str) or clase not in ESPERAS:
        return None
    fila = ESPERAS[clase]
    if (fila.conducta.al_tope not in ("detener", "avisar")
        or not isinstance(abierta, str) or not abierta
        or not isinstance(tope, str) or not tope
        or not isinstance(d.get("hora"), str) or not d["hora"]
        or e.espera_abierta != abierta
        or e.espera_tope and e.espera_tope != tope):
        return None
    en_espera = (
        isinstance(e.situacion, EsperandoReintento) and e.situacion.clase == clase
        or clase == "deteniendo" and e.estado == "deteniendo"
        or clase == "efecto_incierto" and e.estado == "conciliando"
        or clase == "autoridad" and not e.autoridad
        and e.estado not in ("deteniendo", "detenido", "cancelado", "terminado")
        or clase == "impedimento_trabajo" and impedimento_bloquea_trabajo(e)
        and e.estado not in ("deteniendo", "detenido", "cancelado", "terminado")
    )
    return fila if en_espera else None


def validar_tope_espera(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal

    if leer_tope(e, d) is None:
        raise TransicionIlegal("el tope no corresponde a una espera vigente")


def leer_falta_de_avance(e: Encargo, d: dict) -> bool:
    from .. import politica

    if not isinstance(d, dict) or e.estado in (
        "deteniendo", "detenido", "cancelado", "terminado"
    ):
        return False
    regla, desde, turnos = d.get("regla"), d.get("desde"), d.get("turnos")
    if (regla not in ("turno_vivo", "turnos", "esperas")
        or not isinstance(desde, str) or not desde
        or type(turnos) is not int or turnos < 0
        or not isinstance(d.get("hora"), str) or not d["hora"]):
        return False
    if regla == "turno_vivo" and e.proc is None and not e.reserva:
        return False
    vigente = {
        "turno_vivo": e.reloj_turno,
        "turnos": e.ultimo_avance or e.primer_evento,
        "esperas": e.racha_esperas_desde,
    }[regla]
    return desde == vigente and (
        regla != "turnos" or e.turnos_sin_avance >= politica.TURNOS_SIN_AVANCE
    )


def validar_falta_de_avance(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal

    if not leer_falta_de_avance(e, d):
        raise TransicionIlegal("la falta de avance no corresponde al estado vigente")


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
