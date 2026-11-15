"""Manejadores del reductor extraídos de dominio."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..dominio import Encargo


def instrumento_fallo(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace

    return replace(
        e,
        causa=str(d.get("causa") or e.causa),
        motivo=f"instrumento: {d.get('fuente') or 'desconocido'}",
        siguiente=str(d.get("siguiente") or "reintentar el mismo paso"),
        ultimo_hecho=str(d.get("causa") or e.ultimo_hecho),
        hora=hora or e.hora,
    )

def impedimento(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import Impedimento, TransicionIlegal, _con_impedimentos, impedimento_bloquea_trabajo, replace

    clase = str(d.get("clase") or d.get("huella") or "")
    causa = str(d.get("causa") or "")
    condicion = str(d.get("condicion") or "")
    if not clase or not causa or not condicion:
        raise TransicionIlegal("el impedimento necesita clase, causa y condicion")
    pendientes = {i.clase: i for i in e.impedimentos}
    pendientes[clase] = Impedimento(
        clase, causa, condicion, bool(d.get("verificable", True)),
        str(d.get("agente") or ""),
    )
    e = _con_impedimentos(e, tuple(pendientes.values()), hora)
    if impedimento_bloquea_trabajo(e):
        return replace(
            e,
            motivo=e.impedimento_causa,
            siguiente="esperar a que se cumpla: " + e.impedimento_condicion,
            ultimo_hecho=e.impedimento_causa,
        )
    # Un aviso de entrega no frena el producto: se anota como hecho visible
    # pero el responsable sigue trabajando, no espera.
    return replace(e, ultimo_hecho=e.impedimento_causa)

def impedimento_resuelto(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import TransicionIlegal, _con_impedimentos, impedimento_bloquea_trabajo, replace

    clase = str(d.get("clase") or d.get("huella") or "")
    evidencia = str(d.get("evidencia") or "")
    if not clase or not evidencia:
        raise TransicionIlegal("el impedimento no se resuelve sin clase y evidencia")
    restantes = tuple(i for i in e.impedimentos if i.clase != clase)
    if len(restantes) == len(e.impedimentos):
        return e
    e = _con_impedimentos(e, restantes, hora)
    hecho = "impedimento resuelto: " + evidencia
    if clase.startswith("despliegue:") and e.pendientes_entrega:
        # El pendiente es el aviso de la causa de despliegue: resuelta la
        # causa, el aviso se va con ella y los demás quedan.
        e = replace(
            e,
            pendientes_entrega=tuple(
                p for p in e.pendientes_entrega
                if not p.startswith("despliegue:")
            ),
        )
    if impedimento_bloquea_trabajo(e):
        return replace(
            e,
            motivo=e.impedimento_causa,
            siguiente="esperar a que se cumpla: " + e.impedimento_condicion,
            ultimo_hecho=hecho,
        )
    if e.estado == "esperando_reintento" and e.motivo and e.reanuda_en:
        # Resolver otra causa no borra una espera explícita: su motivo y su
        # plazo siguen; el barrido la retoma en `reanuda_en`.
        return replace(e, ultimo_hecho=hecho, hora=hora or e.hora)
    return replace(e, motivo="", siguiente="seguir el mismo encargo", ultimo_hecho=hecho)

def instrumento_no_disponible(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import TransicionIlegal, _con_impedimentos, replace

    clase = str(d.get("clase") or "").strip()
    if not clase:
        raise TransicionIlegal("instrumento_no_disponible sin clase")
    limpios = tuple(i for i in e.impedimentos if i.clase != clase)
    return _con_impedimentos(
        replace(
            e,
            herramientas_no_disponibles=tuple(
                sorted(set(e.herramientas_no_disponibles) | {clase})
            ),
            hora=hora or e.hora,
        ),
        limpios,
        "",
    )

def pregunta(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace

    # Aviso informativo: una pregunta no cambia el estado ni detiene el
    # encargo. Los eventos históricos con `bloquea_todo` se pliegan igual,
    # sin reabrir una espera que el motor ya no tiene.
    texto = str(d.get("texto") or "")
    return replace(
        e,
        preguntas=e.preguntas if texto in e.preguntas else e.preguntas + (texto,),
        hora=hora or e.hora,
    )

def quietud(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import TransicionIlegal, replace

    # La quietud es un dato del diagnóstico, no una espera. Se convierte en
    # un pendiente visible que el barrido entrega con lo que haya.
    causa = str(d.get("causa") or "").strip()
    if not causa:
        raise TransicionIlegal("la quietud necesita causa")
    if e.estado not in ("recibido", "ejecutando"):
        return e
    return replace(
        e,
        pendientes_entrega=(
            e.pendientes_entrega
            if causa in e.pendientes_entrega
            else e.pendientes_entrega + (causa,)
        ),
        motivo=causa,
        siguiente="entregar el PR con los pendientes abiertos",
        ultimo_hecho=causa,
        hora=hora or e.hora,
    )

def estrategia(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _bloqueos_abiertos, replace

    if d["estrategia"] in ("investigar", "diagnosticar_bloqueo") and (
        d.get("rol") or "responsable"
    ) == "responsable":
        e = replace(e, devuelto_con=tuple(sorted(_bloqueos_abiertos(e))))
    return replace(
        e,
        estrategia=d["estrategia"],
        rol=str(d["rol"]) if d.get("rol") else e.rol,
        motivo=d["motivo"],
        siguiente=d["siguiente"],
        hora=hora or e.hora,
        ultimo_hecho=d["motivo"],
    )

def autoridad(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace

    alcanzable = bool(d["alcanzable"])
    puede = (
        alcanzable
        and e.intentos_nuevos
        and e.estado not in ("deteniendo", "detenido", "cancelado", "terminado")
    )
    return replace(e, autoridad=alcanzable, escrituras=puede)

def via(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import TransicionIlegal, replace

    via = d.get("via")
    if via not in ("directa", "durable"):
        raise TransicionIlegal("via desconocida")
    if e.estado in ("cancelado", "terminado"):
        raise TransicionIlegal("el encargo ya cerró")
    if e.proc is not None or e.reserva:
        raise TransicionIlegal("el ejecutor anterior sigue abierto")
    if via == e.via:
        return e
    return replace(
        e,
        via=via,
        hora=hora or e.hora,
        ultimo_hecho="via durable" if via == "durable" else "via directa",
        siguiente=(
            "el supervisor puede tomar el encargo"
            if via == "durable"
            else "lo atienden los agentes del director"
        ),
    )

def solicitud(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _solicitud

    return _solicitud(e, d, hora)

def herramienta(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _herramienta

    return _herramienta(e, d, hora)

def autorizacion(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _autorizacion

    return _autorizacion(e, d, hora)

def decision(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import _decision

    return _decision(e, d, hora)

def solicitud_tardia(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace

    return replace(
        e,
        procedimiento=e.procedimiento
        + (
            "solicitud sin efecto: "
            + str(d.get("id") or "")
            + " ("
            + str(d.get("motivo") or "fuera de la fase o del intento")
            + ")",
        ),
        hora=hora or e.hora,
    )
