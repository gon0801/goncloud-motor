"""Guardias de escritura de detenciones."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..dominio import Encargo


def validar_fallo_interno(e: Encargo, d: dict) -> None:
    from ..dominio import _CERRADOS, TransicionIlegal

    if e.estado in _CERRADOS:
        raise TransicionIlegal("un encargo cerrado o que ya se detiene no cambia de causa")
