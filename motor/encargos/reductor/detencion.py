"""Manejadores del reductor extraídos de dominio."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..dominio import Encargo


def arbol(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..detencion import DETENCIONES

    return DETENCIONES['arbol'](e, d, hora)

def fallo_interno(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..detencion import DETENCIONES

    return DETENCIONES['fallo_interno'](e, d, hora)
