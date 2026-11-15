"""Manejadores de los kinds migrados de la reducción."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import pedido

if TYPE_CHECKING:
    from ..dominio import Encargo


@dataclass(frozen=True)
class Fila:
    aplicar: Callable[[Encargo, dict, str, bool], Encargo]


TABLA: dict[str, Fila] = {
    "recibo": Fila(pedido.recibo),
    "ruta_agentes": Fila(pedido.ruta_agentes),
    "contexto_entrega": Fila(pedido.contexto_entrega),
    "proveedor_fallo": Fila(pedido.proveedor_fallo),
    "proveedor_disponible": Fila(pedido.proveedor_disponible),
    "falla_sesion_local": Fila(pedido.falla_sesion_local),
    "orden": Fila(pedido.orden),
    "ajuste": Fila(pedido.ajuste),
    "procedimiento": Fila(pedido.procedimiento),
    "migracion": Fila(pedido.migracion),
}
