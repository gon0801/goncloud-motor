"""Manejadores del reductor extraídos de dominio."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..dominio import Encargo


def incorporado(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import TransicionIlegal, replace

    # El resultado de un intento ya se incorporó. La generación identifica
    # el intento; la marca sobrevive a la limpieza de su directorio.
    try:
        generacion = int(d.get("generacion") or 0)
    except (TypeError, ValueError):
        raise TransicionIlegal(
            "la incorporación necesita una generación"
        ) from None
    if generacion < 0:
        raise TransicionIlegal("la incorporación necesita una generación")
    marcas = e.incorporado if generacion in e.incorporado else (
        tuple(sorted(e.incorporado + (generacion,)))
    )
    return replace(e, incorporado=marcas)

def quietud_vigilada(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import TransicionIlegal, replace

    # Una vuelta del supervisor observó el encargo quieto sobre el evento
    # `evento`. La cuenta vive en el registro; un evento nuevo la estrena.
    try:
        evento_id = int(d.get("evento") or 0)
        vueltas = int(d.get("vueltas") or 0)
        desde = float(d.get("desde") or 0)
        visto = float(d.get("visto") or 0)
    except (TypeError, ValueError):
        raise TransicionIlegal(
            "la quietud vigilada necesita números"
        ) from None
    return replace(e, quietud_vigilada=(evento_id, vueltas, desde, visto))

def sin_progreso(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _clave_del_sin_progreso, con_escalon, escalon_gastado, replace

    # La escalera se lee de los ids: la clave sale del conjunto abierto (o
    # del pedido del revisor) y los pasos ya gastados se guardan sobre esa
    # clave. No hay contador global que resincronizar.
    rol = str(d.get("rol") or "responsable")
    clave = _clave_del_sin_progreso(e, d, rol)
    diagnostico = str(d.get("diagnostico") or d.get("causa") or "")
    e = replace(e, vuelta_contada=e.generacion)
    if d.get("nueva"):
        # La vuelta trajo bloqueantes nuevos: es información nueva, no «el
        # mismo bloqueo». La clave nueva estrena escalera sola.
        return replace(
            e,
            ultimo_hecho="la vuelta trajo bloqueantes nuevos: vuelven al responsable",
            hora=hora or e.hora,
        )
    if d.get("escalar"):
        # El mismo bloqueo sin información nueva ya no detiene el encargo.
        # Primero pasa el rol a otro modelo autorizado; si el relevo
        # tampoco avanza, se entrega el PR con los pendientes escritos.
        # La escalera es por rol y por clave: un relevo del responsable no
        # deja al revisor sin turno ni fuerza la entrega sin revisar.
        if escalon_gastado(e, rol, "relevo", clave) or escalon_gastado(
            e, rol, "pendientes", clave
        ):
            pendiente = diagnostico or (
                "el bloqueo sigue abierto tras el relevo: " + clave
            )
            return replace(
                con_escalon(e, rol, clave, "pendientes"),
                estado=(
                    "recibido"
                    if e.estado in ("recibido", "ejecutando")
                    else e.estado
                ),
                pendientes_entrega=e.pendientes_entrega + (pendiente,),
                motivo=diagnostico or "el relevo tampoco avanzó en el mismo bloqueo",
                siguiente="entregar el PR con los pendientes abiertos",
                ultimo_hecho=(
                    "el relevo tampoco avanzó: se entrega con pendientes"
                ),
                hora=hora or e.hora,
            )
        return replace(
            con_escalon(e, rol, clave, "relevo"),
            estado=(
                "recibido"
                if e.estado in ("recibido", "ejecutando")
                else e.estado
            ),
            motivo=diagnostico or "el mismo bloqueo sin progreso",
            siguiente="seguir con otro modelo autorizado del mismo rol",
            ultimo_hecho=diagnostico or "se releva a otro modelo del mismo rol",
            hora=hora or e.hora,
        )
    return replace(
        con_escalon(e, rol, clave, "diagnostico"),
        motivo="la corrección no resolvió el bloqueo",
        siguiente="diagnosticar por qué la corrección no resolvió el bloqueo",
        ultimo_hecho="sin progreso: ningun bloqueante cerrado en la vuelta",
        hora=hora or e.hora,
    )

def pendiente_entrega(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace

    texto = str(d.get("texto") or "").strip()
    if texto and texto not in e.pendientes_entrega:
        return replace(
            e,
            pendientes_entrega=e.pendientes_entrega + (texto,),
            hora=hora or e.hora,
        )
    return e

def comprobacion(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _comprobacion

    return _comprobacion(e, d, hora, nuevo)

def afirmacion(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace

    return replace(
        e, procedimiento=e.procedimiento + (f"afirmación: {d['texto']}",)
    )

def efecto(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _efecto_evento

    return _efecto_evento(e, d, hora)

def plazo(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace

    if e.estado != "esperando_reintento":
        return e
    return replace(
        e,
        estado="recibido",
        motivo="",
        reanuda_en="",
        siguiente="seguir después de la espera",
        ultimo_hecho=f"terminó la espera: {e.motivo}",
        hora=hora or e.hora,
    )

def espera(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import TransicionIlegal, _CERRADOS, replace

    if e.estado in _CERRADOS:
        return e
    if not d.get("motivo") or not d.get("reanuda_en"):
        raise TransicionIlegal("la espera necesita motivo y reanudación")
    return replace(
        e,
        estado="esperando_reintento",
        motivo=d["motivo"],
        reanuda_en=d["reanuda_en"],
        siguiente=f"reanudar en {d['reanuda_en']}",
        ultimo_hecho=d["motivo"],
        hora=hora or e.hora,
    )

def excepcion(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace

    # La excepción del check de merge se deriva del pliegue: tipo, SHA e id
    # con su motivo y hora. El último evento de una misma clave manda.
    tipo = str(d.get("tipo") or "descarte")
    sha = str(d.get("sha") or "")
    hid = str(d.get("id") or "")
    if sha and tipo in ("descarte", "fuera_alcance", "omision"):
        motivo = " ".join(str(d.get("motivo") or "").split())
        clave = (tipo, sha, hid)
        actuales = tuple(
            x for x in e.excepciones if (x[0], x[1], x[2]) != clave
        )
        return replace(
            e,
            excepciones=actuales
            + ((tipo, sha, hid, motivo, str(d.get("hora") or "")),),
            hora=hora or e.hora,
        )
    return replace(e, hora=hora or e.hora)
