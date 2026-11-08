"""Resultado cerrado de un paso del barrido.

Un paso avanza, deja una espera visible, devuelve el trabajo a un rol o pide
lanzar un agente. Solo `NecesitaAgente` autoriza un turno nuevo: ni "escribió
eventos" ni "dejó de escribirlos" deciden un lanzamiento.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Avanzo:
    """El paso produjo un hecho nuevo o cerró el trabajo; no lanza otro turno."""


@dataclass(frozen=True)
class Espera:
    """El paso dejó una espera visible con motivo y hora de reanudación."""

    hasta: str
    motivo: str


@dataclass(frozen=True)
class Devolver:
    """El paso devolvió el trabajo a un rol con su causa."""

    rol: str
    causa: str


@dataclass(frozen=True)
class NecesitaAgente:
    """El paso no puede avanzar sin un turno del rol nombrado."""

    rol: str


Resultado = Avanzo | Espera | Devolver | NecesitaAgente


def permite_lanzar(resultado: Resultado) -> bool:
    """Solo `NecesitaAgente` abre un turno nuevo."""
    return isinstance(resultado, NecesitaAgente)

