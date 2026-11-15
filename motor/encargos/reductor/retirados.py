"""Manejadores del reductor extraídos de dominio."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..dominio import Encargo


def registrar(e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import replace

    # Eventos sin efecto sobre el estado: los retirados de una versión
    # anterior, la marca de que una pregunta vencida se pasó al revisor y
    # la de que un cerrado ya no tiene nada que reintentar en la autoridad.
    return replace(e, hora=hora or e.hora)
