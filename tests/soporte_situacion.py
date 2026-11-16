"""La importación diferida permite ejecutar las pruebas contra la base sin 3.5."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from motor.encargos.encargo import Encargo


def evolucionar(e: Encargo, **cambios: Any) -> Encargo:
    from motor.encargos.encargo import evolucionar as actualizar

    return actualizar(e, **cambios)
