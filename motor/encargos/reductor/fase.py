"""Manejadores del reductor extraídos de dominio."""

from __future__ import annotations
from ..encargo import evolucionar

from typing import TYPE_CHECKING, cast

from .errores import HechoInaplicable, leer_carga

if TYPE_CHECKING:
    from ..dominio import Encargo


def fases(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import TransicionIlegal, _adoptar_fases

    try:
        return _adoptar_fases(e, d, hora, nuevo)
    except TransicionIlegal as exc:
        mensaje = str(exc)
        carga_ilegible = (
            mensaje.startswith("requiere_despliegue debe")
            or mensaje.startswith("lane ")
            or mensaje.startswith("despliegue ")
            or mensaje.startswith("despliegue.")
            or mensaje.startswith("una fase sin despliegue")
            or mensaje in ("requiere debe ser una lista", "dependencia de fase vacía")
            or "requiere una fase inexistente:" in mensaje
        )
        if nuevo or not carga_ilegible:
            raise
        raise HechoInaplicable from exc

def nivel_efectivo(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace
    from .validacion_fase import leer_nivel_efectivo

    nivel = leer_nivel_efectivo(e, d)
    if nivel is None:
        return e
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
    from ..dominio import TransicionIlegal, _correspondencia_despliegue, con_fase, datos_despliegue, replace
    from .validacion_fase import leer_fase

    previo = e
    pedida = str(d.get("fase") or "")
    if pedida and pedida != e.fase_activa:
        fase = next((f for f in e.fases if f.id == pedida), None)
        if fase is None:
            return e
    else:
        lectura = leer_fase(e, d)
        if lectura is None:
            return e
        e = lectura
        fase = e.fase_actual
    if fase is None or fase.estado == "preparada":
        return previo
    fuente, huella = str(d.get("fuente") or ""), str(d.get("huella") or "")
    if not fuente or not huella:
        return previo
    try:
        datos = datos_despliegue(d)
        correspondencia = _correspondencia_despliegue(e, d)
    except (TransicionIlegal, TypeError, ValueError):
        return previo
    if "requiere_despliegue" not in datos:
        return previo
    return replace(
        con_fase(e, replace(fase, **datos, despliegue_fuente=fuente, despliegue_huella=huella)),
        hora=hora or e.hora,
        **correspondencia,
    )

def producto_listo(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import TransicionIlegal, VersionProducto, con_fase, replace
    from .validacion_fase import leer_fase

    lectura = leer_fase(e, d)
    if lectura is None:
        return e
    previo = e
    e = lectura
    fase = e.fase_actual
    if fase is None:
        return previo
    try:
        version = VersionProducto.desde(
            d.get("version") or "", d.get("manifiesto"), d.get("sha_git") or ""
        )
    except (TransicionIlegal, TypeError, ValueError):
        return previo
    nueva = replace(fase, producto=version, estado="revision")
    return replace(
        con_fase(e, nueva),
        ultimo_hecho=f"producto de {fase.id} listo para revisar",
        hora=hora or e.hora,
    )

def ci_pr(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _ci_pr
    from .validacion_fase import leer_fase

    if not nuevo and str(d.get("estado") or "") in (
        "verde", "rojo", "error", "reintentando", "sin_resultado",
    ):
        lectura = leer_fase(e, d)
        if lectura is not None and lectura.fase_actual is not None:
            leer_carga(nuevo, lambda: tuple(str(x) for x in (d.get("fallas") or ())))
    return _ci_pr(e, d, hora)

def entrega_externa(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import TransicionIlegal, _entrega_externa
    from .validacion_fase import leer_fase

    if not nuevo:
        if leer_fase(e, d) is None:
            raise HechoInaplicable
        if d.get("numero"):
            leer_carga(nuevo, lambda: int(d["numero"]))
    try:
        return _entrega_externa(e, d, hora)
    except TransicionIlegal as exc:
        if nuevo or str(exc) != "no hay fase para la entrega":
            raise
        raise HechoInaplicable from exc

def entrega_bloqueada(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _con_progreso, con_fase, replace
    from .validacion_fase import leer_fase

    lectura = leer_fase(e, d)
    if lectura is None:
        return e
    previo = e
    e = lectura
    fase = e.fase_actual
    if fase is None:
        return previo
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
    from ..dominio import replace
    from .validacion_fase import leer_fase

    lectura = leer_fase(e, d)
    if lectura is None:
        return e
    previo = e
    e = lectura
    fase = e.fase_actual
    dep = str(d.get("dep") or "").strip()
    version = str(d.get("version") or "").strip()
    if fase is None or not dep or not version:
        return previo
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
    from ..dominio import Fase, _CERRADOS, asegurar_fase, con_fase, hashlib, json, replace

    evidencia = str(d.get("evidencia") or "").strip()
    if not evidencia or e.estado in _CERRADOS:
        return e
    e = asegurar_fase(e)
    fase = e.fase_actual
    e = evolucionar(e, cierre_forzado_fase=fase, cierre_forzado_huella=hashlib.sha256(
        json.dumps([e.id, cast(Fase, fase).id, hora, evidencia, e.reaperturas]).encode()
    ).hexdigest())
    if fase is not None:
        e = con_fase(
            e,
            replace(fase, estado="preparada", evidencia=evidencia),
        )
    return evolucionar(
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
    from ..dominio import TransicionIlegal, _conciliacion_fase

    if not nuevo and any(f.id == str(d.get("fase") or "") for f in e.fases):
        paso = str(d.get("paso") or "")
        campo = {"en_curso": "intentos", "reintento": "ordenes",
                 "integrado": "numero"}.get(paso)
        if paso == "integrado" and not (d.get("recibo") or d.get("sha")):
            campo = None
        if campo and d.get(campo):
            leer_carga(nuevo, lambda: int(d[campo]))
        if (paso == "espera" and d.get("intentos")
            or paso == "espera_agotada" and str(d.get("causa") or "").strip()
            and d.get("ordenes")):
            try:
                int(d["intentos" if paso == "espera" else "ordenes"])
            except (TypeError, ValueError):
                pass
            except OverflowError as exc:
                raise HechoInaplicable from exc
    try:
        return _conciliacion_fase(e, d, hora)
    except TransicionIlegal as exc:
        if nuevo:
            raise
        raise HechoInaplicable from exc

def fase_desplegada(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _fase_desplegada

    return _fase_desplegada(e, d, hora)

def resultado_directo(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import asegurar_fase, replace

    evidencia = str(d.get("evidencia") or "")
    if (e.via != "directa" or e.entrega.nivel != "terminado"
        or e.proc is not None or e.reserva
        or e.orden_parada == "cancelar" or e.estado in ("cancelado", "terminado")
        or not evidencia):
        return e
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
    return evolucionar(
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
