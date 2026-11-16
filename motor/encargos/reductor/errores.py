"""Señales internas para la aplicación histórica de un evento."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TypeVar


class HechoInaplicable(Exception):
    """Una carga guardada no se puede aplicar sin inventar un estado."""


T = TypeVar("T")


def leer_carga(nuevo: bool, lector: Callable[[], T]) -> T:
    try:
        return lector()
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        if nuevo:
            raise
        raise HechoInaplicable from exc


_observador: ContextVar[Callable[[str, str], None] | None] = ContextVar(
    "observador_omisiones", default=None,
)


@contextmanager
def observar_omisiones(avisar: Callable[[str, str], None]) -> Iterator[None]:
    token = _observador.set(avisar)
    try:
        yield
    finally:
        _observador.reset(token)


def avisar_omision(kind: str, exc: HechoInaplicable) -> None:
    observador = _observador.get()
    if observador is not None:
        observador(kind, type(exc.__cause__ or exc).__name__)
