"""Manejadores del reductor extraídos de dominio."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..dominio import Encargo


def observacion_supervisor(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace

    return replace(
        e,
        observada_version=str(d.get("version") or e.observada_version),
        causa=str(d.get("error_instrumento") or e.causa),
        ultimo_hecho="comprobaciones observadas por el supervisor",
        hora=hora or e.hora,
    )

def comprobaciones_supervisor(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import Hallazgo, _bloqueos_abiertos, _con_progreso, _fusionar, con_fase, normaliza_comando, replace

    # Las comprobaciones en rojo de una observacion son trabajo bloqueante
    # con la misma identidad que un hallazgo: el comando. Al escribir la
    # observacion se abren las que fallan y se cierran las que esta
    # observacion ejecuto y paso en verde. Una comprobacion retirada o con
    # otro comando no se cierra: no hay observacion en verde que la resuelva.
    fase = e.fase_actual
    if fase is None:
        return e
    nuevos = tuple(
        Hallazgo.desde(h)
        for h in (d.get("hallazgos") or ())
        if isinstance(h, dict)
    )
    ids = {h.id for h in nuevos}
    verdes = set(d.get("verdes") or ())
    comandos_verdes = {
        normaliza_comando(x) for x in (d.get("verdes_comandos") or ())
    }
    con_verdes = "verdes" in d
    previos = tuple(
        replace(h, estado="resuelto")
        if h.origen == "supervisor" and h.estado == "abierto" and h.id not in ids
        and (
            not con_verdes
            or h.id in verdes
            or normaliza_comando(h.ancla) in comandos_verdes
        )
        else h
        for h in fase.hallazgos
    )
    hallazgos = _fusionar(previos, nuevos)
    if hallazgos == fase.hallazgos:
        return replace(e, hora=hora or e.hora)
    actualizado = replace(
        con_fase(e, replace(fase, hallazgos=hallazgos)),
        ultimo_hecho="comprobaciones del supervisor registradas",
        hora=hora or e.hora,
    )
    if _bloqueos_abiertos(e) - _bloqueos_abiertos(actualizado):
        return _con_progreso(e, actualizado)
    return actualizado

def revision(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _revision

    return _revision(e, d, hora)

def deepseek_lectura(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import LecturaDeepSeek, replace

    return replace(e, lectura_deepseek=LecturaDeepSeek.de_dict(d))

def deepseek_cerrado(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import MarcasDeepSeekCerrado, replace

    return replace(e, deepseek_cerrado=MarcasDeepSeekCerrado.de_dict(d))

def ci_sondeo(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import SondeoCI, replace

    return replace(e, sondeo_ci=SondeoCI.de_dict(d))

def nota_pr(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import con_fase, replace

    texto = str(d.get("texto") or "").strip()
    if texto and texto not in e.notas_pr:
        # La nota es de la fase que la produjo; el log del encargo se
        # conserva para vista y prompts. Sin fases declaradas no se crea
        # ninguna fase nueva por escribir una nota.
        fase = e.fase_actual
        if e.fases and fase is not None and texto not in fase.notas:
            e = con_fase(e, replace(fase, notas=fase.notas + (texto,)))
        return replace(e, notas_pr=e.notas_pr + (texto,), hora=hora or e.hora)
    return e

def hallazgo_fuera_de_alcance(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace

    hid = str(d.get("id") or "").strip()
    titulo = str(d.get("titulo") or "").strip()
    razon = " ".join(str(d.get("razon") or "").split())
    if hid and razon:
        actuales = tuple(x for x in e.fuera_de_alcance if x[0] != hid)
        legado = tuple(x for x in e.excepciones_legado if x[0] != hid)
        return replace(
            e,
            fuera_de_alcance=actuales + ((hid, titulo, razon),),
            excepciones_legado=legado + ((hid, razon, hora),),
            hora=hora or e.hora,
        )
    return e

def entrega_devuelta(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    # La devolución de la entrega se lee del registro: el producto y el
    # estado no cambian, solo deja de reintentarse hasta la próxima vuelta.
    return e

def inspeccion_solicitud(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import TransicionIlegal, _fase_de_evento, con_fase, replace

    e = _fase_de_evento(e, d)
    fase = e.fase_actual
    if fase is None:
        raise TransicionIlegal("no hay fase para la inspección")
    ident = str(d.get("solicitud") or "").strip()
    if not ident:
        raise TransicionIlegal("la inspección necesita su solicitud")
    return replace(
        con_fase(e, replace(fase, inspeccion_solicitud=ident)),
        hora=hora or e.hora,
    )

def inspeccion_entregada(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import TransicionIlegal, _fase_de_evento, con_fase, replace

    e = _fase_de_evento(e, d)
    fase = e.fase_actual
    if fase is None:
        raise TransicionIlegal("no hay fase para la entrega de la inspección")
    marca = str(d.get("marca") or "").strip()
    if not marca:
        raise TransicionIlegal("la entrega de la inspección necesita su marca")
    consumidas = fase.inspecciones_consumidas
    if marca not in consumidas:
        consumidas = consumidas + (marca,)
    return replace(
        con_fase(
            e,
            replace(
                fase,
                inspeccion_entregada=marca,
                inspecciones_consumidas=consumidas,
            ),
        ),
        hora=hora or e.hora,
    )

def resolucion(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _resolucion

    return _resolucion(e, d, hora)

def respuesta(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _respuesta

    return _respuesta(e, d, hora)
