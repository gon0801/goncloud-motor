"""Lo que se lee de fuera es un hecho tipado, nunca un booleano.

`Leido` conserva el dato; `Pendiente` dice desde cuándo se espera; `Error`
conserva la causa real. La política decide sobre esa lectura: confundir un
error o una espera con un «no» pierde la causa y convierte una falla del
instrumento en una decisión del motor.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Leido:
    """La lectura llegó y trae el dato."""

    datos: object = None


@dataclass(frozen=True)
class Pendiente:
    """La lectura todavía no está.

    `desde` es cuándo empezó la espera o, cuando se conoce, la causa de la
    ausencia (por ejemplo, el ejecutable que falta); nunca se descarta al
    mostrarla.
    """

    desde: str = ""


@dataclass(frozen=True)
class Error:
    """La lectura falló; `causa` es el motivo real, no un «no hay»."""

    causa: str = ""


Lectura = Leido | Pendiente | Error


def es_leido(lectura: Lectura) -> bool:
    return isinstance(lectura, Leido)


def causa_de(lectura: Lectura) -> str:
    """La causa que se puede mostrar para una lectura que no llegó."""
    if isinstance(lectura, Error):
        return lectura.causa or "no se pudo leer"
    if isinstance(lectura, Pendiente):
        return lectura.desde or "no hay sesión en este host"
    return ""
