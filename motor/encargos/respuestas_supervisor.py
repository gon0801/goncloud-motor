"""Atiende respuestas antes del barrido sin recrear carpetas de cerrados."""

from __future__ import annotations

from .ciclo import plegar_aislado
from .registro import Registro


def atender_respuestas(reg: Registro, hora: str) -> None:
    try:
        from .ciclo import _directorio, _espacio
        from .necesidades import continuar_respuestas
    except Exception:
        return
    for ident in reg.ids():
        try:
            enc = plegar_aislado(reg, ident)
            if enc is None or enc.estado in ("terminado", "cancelado"):
                continue
            if any(s.estado == "respuesta" for s in enc.solicitudes):
                continuar_respuestas(
                    reg, enc, _espacio(reg.base, ident),
                    _directorio(reg.base, ident), hora,
                )
        except Exception:
            continue
