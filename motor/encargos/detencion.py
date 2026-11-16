"""Los hechos que detienen un encargo sin una orden nueva del dueño.

`arbol` cierra la detención que pidió una orden cuando ya no queda proceso.
`fallo_interno` es la quinta causa de `detenido`, que David decidió el
2026-11-13: el registro rechazó un hecho que un paso del barrido tenía que
guardar. El encargo no lo calla ni lo reintenta en cada vuelta; se detiene con
la causa escrita, sin valores, hasta que alguien mande `continuar`.

`reductor.detencion` los aplica desde `DETENCIONES` por la tabla. Este módulo
importa `dominio` solo dentro de sus funciones para evitar el ciclo.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .dominio import Encargo

_PORQUE = {
    "ajena": "su clave ya guarda otro hecho",
    "ilegal": "el estado del encargo no lo admite",
}


def causa_de_fallo_interno(d: dict) -> str:
    """Lo que lee el dueño: el kind, la clase de la ukey y por qué, sin valores."""
    porque = _PORQUE.get(str(d.get("rechazo") or ""), "lo rechazó")
    return (
        f"fallo interno: el registro rechazó el evento {d.get('kind') or '?'} "
        f"({d.get('ukey') or '?'}) porque {porque}; se reanuda con continuar"
    )


def fallo_interno(e: Encargo, d: dict, hora: str) -> Encargo:
    """Un proceso vivo se cierra primero, como con `parar`: `arbol` termina la
    detención y conserva la causa en `motivo`."""
    from .dominio import TransicionIlegal, _CERRADOS

    if e.estado in _CERRADOS:
        raise TransicionIlegal("un encargo cerrado o que ya se detiene no cambia de causa")
    causa = causa_de_fallo_interno(d)
    vivo = e.proc is not None or e.reserva
    return replace(
        e,
        estado="deteniendo" if vivo else "detenido",
        orden_parada="fallo_interno",
        intento_interrumpido=e.intento_interrumpido or vivo,
        motivo=causa,
        ultimo_hecho=causa,
        siguiente="cerrar el proceso y quedar detenido hasta continuar"
        if vivo
        else "detenido hasta continuar",
        intentos_nuevos=False,
        escrituras=False,
        hora=hora or e.hora,
    )


def arbol(e: Encargo, d: dict, hora: str) -> Encargo:
    if d.get("observacion") != "ausente" or e.estado != "deteniendo" or e.reserva:
        return e
    estado = "cancelado" if e.orden_parada == "cancelar" else "detenido"
    return replace(
        e,
        estado=estado,
        proc=None,
        herramienta="",
        hora=hora or e.hora,
        ultimo_hecho="el árbol de procesos quedó ausente",
        siguiente="ninguna" if estado == "cancelado" else "detenido hasta continuar",
        motivo={"cancelar": "cancelado", "fallo_interno": e.motivo}.get(e.orden_parada, ""),
    )


DETENCIONES: dict[str, Callable[[Encargo, dict, str], Encargo]] = {
    "arbol": arbol,
    "fallo_interno": fallo_interno,
}
