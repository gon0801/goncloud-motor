"""Reglas de escritura de fase; los eventos guardados no se vuelven a validar."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..dominio import Encargo


def leer_fase(e: Encargo, d: dict) -> Encargo | None:
    from ..dominio import asegurar_fase

    e = asegurar_fase(e)
    pedida = str(d.get("fase") or "")
    intento = str(d.get("intento") or "")
    if pedida and pedida != e.fase_activa or intento and e.intento and intento != e.intento:
        return None
    return e


def validar_fase(e: Encargo, d: dict) -> None:
    from ..dominio import _fase_de_evento

    _fase_de_evento(e, d)


def validar_nivel_efectivo(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal, _ORDEN_NIVEL

    nivel = str(d.get("nivel") or "")
    if nivel not in ("", "pr", "integrado"):
        raise TransicionIlegal(f"nivel efectivo desconocido: {nivel}")
    if nivel and _ORDEN_NIVEL.get(nivel, 0) >= _ORDEN_NIVEL.get(e.entrega.nivel, 1):
        raise TransicionIlegal("el nivel efectivo no supera al solicitado")
    if e.nivel_efectivo and _ORDEN_NIVEL.get(nivel, 99) > _ORDEN_NIVEL[e.nivel_efectivo]:
        raise TransicionIlegal("el nivel efectivo solo se limita más; no se relaja")


def leer_nivel_efectivo(e: Encargo, d: dict) -> str | None:
    from ..dominio import _ORDEN_NIVEL

    nivel = str(d.get("nivel") or "")
    if (nivel not in ("", "pr", "integrado")
        or nivel and _ORDEN_NIVEL.get(nivel, 0) >= _ORDEN_NIVEL.get(e.entrega.nivel, 1)
        or e.nivel_efectivo and _ORDEN_NIVEL.get(nivel, 99) >
        _ORDEN_NIVEL.get(e.nivel_efectivo, 0)):
        return None
    return nivel


def validar_politica_despliegue(e: Encargo, d: dict) -> None:
    from ..dominio import (
        TransicionIlegal, _correspondencia_despliegue, _fase_de_evento,
        datos_despliegue,
    )

    pedida = str(d.get("fase") or "")
    preparado = e
    if pedida and pedida != e.fase_activa:
        fase = next((f for f in e.fases if f.id == pedida), None)
        if fase is None:
            raise TransicionIlegal("la política nombra una fase inexistente")
    else:
        preparado = _fase_de_evento(e, d)
        fase = preparado.fase_actual
    if fase is None or fase.estado == "preparada":
        raise TransicionIlegal("no se cambia la política de una fase cerrada")
    if not str(d.get("fuente") or "") or not str(d.get("huella") or ""):
        raise TransicionIlegal("política de despliegue sin fuente comprobable")
    datos = datos_despliegue(d)
    if "requiere_despliegue" not in datos:
        raise TransicionIlegal("política sin declaración de despliegue")
    _correspondencia_despliegue(preparado, d)


def validar_producto_listo(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal, VersionProducto, _fase_de_evento

    if _fase_de_evento(e, d).fase_actual is None:
        raise TransicionIlegal("no hay fase para el producto")
    VersionProducto.desde(d.get("version") or "", d.get("manifiesto"), d.get("sha_git") or "")


def validar_ci_pr(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal, _fase_de_evento

    if _fase_de_evento(e, d).fase_actual is None:
        raise TransicionIlegal("no hay fase para el CI del PR")
    if str(d.get("estado") or "") not in ("verde", "rojo", "error", "reintentando", "sin_resultado"):
        raise TransicionIlegal("estado de CI del PR desconocido")


def validar_entrega_bloqueada(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal, _fase_de_evento

    if _fase_de_evento(e, d).fase_actual is None:
        raise TransicionIlegal("no hay fase para la entrega bloqueada")


def validar_entrada_incorporada(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal, _fase_de_evento

    fase = _fase_de_evento(e, d).fase_actual
    if fase is None or not str(d.get("dep") or "").strip() or not str(d.get("version") or "").strip():
        raise TransicionIlegal("la entrada incorporada necesita fase, dep y versión")


def validar_cierre_forzado(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal, _CERRADOS

    if not str(d.get("evidencia") or "").strip():
        raise TransicionIlegal("cierre forzado sin evidencia")
    if e.estado in _CERRADOS:
        raise TransicionIlegal("el encargo ya cerró")


def validar_resultado_directo(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal

    if e.via != "directa":
        raise TransicionIlegal("el resultado directo solo cierra la via directa")
    if e.entrega.nivel != "terminado":
        raise TransicionIlegal("el resultado directo no cumple niveles externos de entrega")
    if e.proc is not None or e.reserva:
        raise TransicionIlegal("hay un escritor abierto")
    if e.orden_parada == "cancelar" or e.estado in ("cancelado", "terminado"):
        raise TransicionIlegal("el encargo ya cerró")
    if not str(d.get("evidencia") or ""):
        raise TransicionIlegal("terminado sin evidencia")


def validar_bloqueo_fase(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal, _CERRADOS, asegurar_fase

    if not str(d.get("evidencia") or "").strip():
        raise TransicionIlegal("bloqueo de fase sin evidencia")
    if e.estado in _CERRADOS:
        raise TransicionIlegal("el encargo ya cerró")
    fase = asegurar_fase(e).fase_actual
    if fase is None:
        raise TransicionIlegal("no hay fase que bloquear")
    if fase.estado == "bloqueada":
        raise TransicionIlegal(f"la fase {fase.id} ya está bloqueada")


def validar_fase_reactivada(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal, _CERRADOS

    pedida = str(d.get("fase") or "")
    fase = next((f for f in e.fases if f.id == pedida), None)
    if fase is None or fase.estado != "bloqueada":
        raise TransicionIlegal(f"la fase {pedida or '?'} no está bloqueada")
    if e.estado in _CERRADOS:
        raise TransicionIlegal("el encargo ya cerró")


def validar_fase_reabierta(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal

    pedida = str(d.get("fase") or "")
    fase = next((f for f in e.fases if f.id == pedida), None)
    tardia = bool(d.get("tardia")) and e.estado == "terminado"
    if fase is None or fase.estado != "preparada":
        raise TransicionIlegal("solo se reabre una fase preparada")
    if e.estado in ("terminado", "cancelado", "deteniendo", "detenido") and not tardia:
        raise TransicionIlegal("el encargo ya no admite reabrir una fase")


def validar_fase_desplegada(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal

    pedida = str(d.get("fase") or "")
    fase = next((f for f in e.fases if f.id == pedida), None)
    if fase is None:
        raise TransicionIlegal(f"la fase {pedida or '?'} no existe")
    recibo = str(d.get("recibo") or "")
    sha = str(d.get("sha") or "")
    if not recibo or not sha:
        raise TransicionIlegal("una fase desplegada necesita recibo y SHA")
    if sha != fase.integrado_sha:
        raise TransicionIlegal("el despliegue no corresponde al SHA integrado de la fase")


def validar_conciliacion_fase(e: Encargo, d: dict) -> None:
    from ..dominio import TransicionIlegal, _correspondencia_despliegue, datos_despliegue

    pedida = str(d.get("fase") or "")
    fase = next((f for f in e.fases if f.id == pedida), None)
    if fase is None:
        raise TransicionIlegal(
            f"la conciliación nombra una fase inexistente: {pedida or '?'}"
        )
    paso = str(d.get("paso") or "")
    if paso == "politica":
        datos_despliegue(d)
        _correspondencia_despliegue(e, d)
    elif paso == "impedimento" and not str(d.get("causa") or "").strip():
        raise TransicionIlegal("la conciliación impedida no trae causa")
    elif paso == "en_curso":
        int(d.get("intentos") or (fase.despliegue_intentos + 1))
    elif paso == "espera_agotada" and not str(d.get("causa") or "").strip():
        raise TransicionIlegal("la espera agotada no trae causa")
    elif paso == "reintento":
        int(d.get("ordenes") or 0)
    elif paso == "espera":
        if not str(d.get("causa") or "").strip() or not str(d.get("reanuda_en") or "").strip():
            raise TransicionIlegal("la espera de conciliación necesita causa y reanudación")
    elif paso == "integrado":
        if not str(d.get("recibo") or d.get("sha") or ""):
            raise TransicionIlegal("la conciliación integrada no trae el merge commit")
        datos_despliegue(d)
        int(d.get("numero") or fase.pr_numero or 0)
        _correspondencia_despliegue(e, d)
    elif paso == "desplegado" and not str(d.get("recibo") or d.get("sha") or ""):
        raise TransicionIlegal("la conciliación desplegada no trae recibo")
    elif paso not in (
        "consulta", "politica", "impedimento", "en_curso", "espera_agotada",
        "reintento", "espera", "integrado", "desplegado", "preparada",
    ):
        raise TransicionIlegal(f"paso de conciliación desconocido: {paso}")
