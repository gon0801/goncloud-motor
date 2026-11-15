"""Manejadores del reductor extraídos de dominio."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ..dominio import Encargo


def fases(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _adoptar_fases

    return _adoptar_fases(e, d, hora, nuevo)

def nivel_efectivo(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import TransicionIlegal, _ORDEN_NIVEL, replace

    nivel = str(d.get("nivel") or "")
    if nivel not in ("", "pr", "integrado"):
        raise TransicionIlegal(f"nivel efectivo desconocido: {nivel}")
    if nivel and _ORDEN_NIVEL.get(nivel, 0) >= _ORDEN_NIVEL.get(
        e.entrega.nivel, 1
    ):
        raise TransicionIlegal("el nivel efectivo no supera al solicitado")
    if e.nivel_efectivo and _ORDEN_NIVEL.get(nivel, 99) > _ORDEN_NIVEL[
        e.nivel_efectivo
    ]:
        # El nivel efectivo es un tope que solo se aprieta: una fuente que
        # no vio la limitación (p. ej. el sondeo de máquina sin gh) no
        # relaja lo que otra ya comprobó (h_6a425441). El nivel igual es
        # un sin registro, no un error.
        raise TransicionIlegal("el nivel efectivo solo se limita más; no se relaja")
    if nivel == e.nivel_efectivo:
        return e
    motivo = str(d.get("motivo") or f"nivel efectivo limitado a {nivel}")
    return replace(
        e,
        nivel_efectivo=nivel,
        nivel_efectivo_motivo=motivo,
        ultimo_hecho=motivo,
        hora=hora or e.hora,
    )

def politica_despliegue(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import TransicionIlegal, _correspondencia_despliegue, _fase_de_evento, con_fase, datos_despliegue, replace

    pedida = str(d.get("fase") or "")
    if pedida and pedida != e.fase_activa:
        fase = next((f for f in e.fases if f.id == pedida), None)
        if fase is None:
            raise TransicionIlegal("la política nombra una fase inexistente")
    else:
        e = _fase_de_evento(e, d)
        fase = e.fase_actual
    if fase is None or fase.estado == "preparada":
        raise TransicionIlegal("no se cambia la política de una fase cerrada")
    fuente, huella = str(d.get("fuente") or ""), str(d.get("huella") or "")
    if not fuente or not huella:
        raise TransicionIlegal("política de despliegue sin fuente comprobable")
    datos = datos_despliegue(d)
    if "requiere_despliegue" not in datos:
        raise TransicionIlegal("política sin declaración de despliegue")
    correspondencia = _correspondencia_despliegue(e, d)
    return replace(
        con_fase(e, replace(fase, **datos, despliegue_fuente=fuente, despliegue_huella=huella)),
        hora=hora or e.hora,
        **correspondencia,
    )

def producto_listo(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import TransicionIlegal, VersionProducto, _fase_de_evento, con_fase, replace

    e = _fase_de_evento(e, d)
    fase = e.fase_actual
    if fase is None:
        raise TransicionIlegal("no hay fase para el producto")
    version = VersionProducto.desde(
        d.get("version") or "", d.get("manifiesto"), d.get("sha_git") or ""
    )
    nueva = replace(fase, producto=version, estado="revision")
    return replace(
        con_fase(e, nueva),
        ultimo_hecho=f"producto de {fase.id} listo para revisar",
        hora=hora or e.hora,
    )

def ci_pr(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _ci_pr

    return _ci_pr(e, d, hora)

def entrega_externa(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _entrega_externa

    return _entrega_externa(e, d, hora)

def entrega_bloqueada(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import TransicionIlegal, _con_progreso, _fase_de_evento, con_fase, replace

    e = _fase_de_evento(e, d)
    fase = e.fase_actual
    if fase is None:
        raise TransicionIlegal("no hay fase para la entrega bloqueada")
    return _con_progreso(
        e,
        replace(
            con_fase(
                e,
                replace(
                    fase,
                    entrega_bloqueada_sha=str(d.get("sha") or ""),
                ),
            ),
            ultimo_hecho=str(
                d.get("causa") or "entrega bloqueada por un conflicto con la base"
            ),
            hora=hora or e.hora,
        ),
    )

def lectura_fallida(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import con_fase, replace

    # Se ve en `ver` (deepseek.error) y no toca motivo, siguiente ni causa,
    # que son lo que lee el próximo turno del agente. Una causa vacía anota
    # que la lectura volvió a salir bien.
    fase = e.fase_actual
    if fase is None or str(d.get("fase") or fase.id) != fase.id:
        return e
    return con_fase(e, replace(fase, deepseek_error=str(d.get("causa") or "")))

def fase_reabierta(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _fase_reabierta

    return _fase_reabierta(e, d, hora)

def entrada_incorporada(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import TransicionIlegal, _fase_de_evento, replace

    e = _fase_de_evento(e, d)
    fase = e.fase_actual
    dep = str(d.get("dep") or "").strip()
    version = str(d.get("version") or "").strip()
    if fase is None or not dep or not version:
        raise TransicionIlegal("la entrada incorporada necesita fase, dep y versión")
    # La propuesta de fases es la que vale: incorporar una punta terminada
    # es un hecho de la entrega, no una dependencia nueva. Reescribir
    # `requiere` cambiaria el plan ya validado y podria condenar la fase
    # por una arista que nadie propuso. La ancestria del producto es la
    # prueba de la incorporacion.
    return replace(
        e,
        ultimo_hecho=f"entrada de {dep} incorporada: {version[:12]}",
        hora=hora or e.hora,
    )

def cierre_forzado(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import Fase, TransicionIlegal, _CERRADOS, asegurar_fase, con_fase, hashlib, json, replace

    evidencia = str(d.get("evidencia") or "").strip()
    if not evidencia:
        raise TransicionIlegal("cierre forzado sin evidencia")
    if e.estado in _CERRADOS:
        raise TransicionIlegal("el encargo ya cerró")
    e = asegurar_fase(e)
    fase = e.fase_actual
    e = replace(e, cierre_forzado_fase=fase, cierre_forzado_huella=hashlib.sha256(
        json.dumps([e.id, cast(Fase, fase).id, hora, evidencia, e.reaperturas]).encode()
    ).hexdigest())
    if fase is not None:
        e = con_fase(
            e,
            replace(fase, estado="preparada", evidencia=evidencia),
        )
    return replace(
        e,
        estado="terminado",
        evidencia=e.evidencia + (evidencia,),
        intentos_nuevos=False,
        escrituras=False,
        ultimo_hecho=evidencia,
        siguiente="ninguna",
        motivo="",
        hora=hora or e.hora,
    )

def bloqueo_fase(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _bloqueo_fase

    return _bloqueo_fase(e, d, hora)

def fase_reactivada(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _fase_reactivada

    return _fase_reactivada(e, d, hora)

def conciliacion_fase(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _conciliacion_fase

    return _conciliacion_fase(e, d, hora)

def fase_desplegada(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _fase_desplegada

    return _fase_desplegada(e, d, hora)

def resultado_directo(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import TransicionIlegal, asegurar_fase, replace

    if e.via != "directa":
        raise TransicionIlegal("el resultado directo solo cierra la via directa")
    if e.entrega.nivel != "terminado":
        raise TransicionIlegal(
            "el resultado directo no cumple niveles externos de entrega"
        )
    if e.proc is not None or e.reserva:
        raise TransicionIlegal("hay un escritor abierto")
    if e.orden_parada == "cancelar" or e.estado in ("cancelado", "terminado"):
        raise TransicionIlegal("el encargo ya cerró")
    evidencia = str(d.get("evidencia") or "")
    if not evidencia:
        raise TransicionIlegal("terminado sin evidencia")
    e = asegurar_fase(e)
    fase = e.fase_actual
    fases = e.fases
    if fase is not None:
        fase_id = fase.id
        fases = tuple(
            replace(fase, estado="preparada", evidencia=evidencia)
            if f.id == fase_id
            else f
            for f in e.fases
        )
    return replace(
        e,
        fases=fases,
        estado="terminado",
        cierre_forzado_fase=None,
        cierre_forzado_huella="",
        evidencia=e.evidencia + (evidencia,),
        intentos_nuevos=False,
        escrituras=False,
        ultimo_hecho="resultado directo comprobado",
        siguiente="publicado al canal de origen",
        hora=hora or e.hora,
        motivo="",
    )
