"""Manejadores del reductor extraídos de dominio."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .errores import HechoInaplicable

if TYPE_CHECKING:
    from ..dominio import Encargo


def arbol(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..detencion import DETENCIONES

    return DETENCIONES['arbol'](e, d, hora)

def fallo_interno(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..detencion import DETENCIONES
    from ..dominio import TransicionIlegal

    try:
        return DETENCIONES['fallo_interno'](e, d, hora)
    except TransicionIlegal as exc:
        if nuevo:
            raise
        raise HechoInaplicable from exc
