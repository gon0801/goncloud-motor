"""Estados del PR observados por el motor, sin consultar GitHub."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from motor.github import PrView


@dataclass(frozen=True)
class PrSin:
    estado: Literal["sin_pr"] = "sin_pr"


@dataclass(frozen=True)
class PrBorrador:
    numero: int
    head: str
    base: str
    estado: Literal["borrador"] = "borrador"


@dataclass(frozen=True)
class PrAbierto:
    numero: int
    head: str
    base: str
    estado: Literal["abierto"] = "abierto"


@dataclass(frozen=True)
class PrMergeado:
    numero: int
    head: str
    base: str
    merge_commit: str
    estado: Literal["mergeado"] = "mergeado"


@dataclass(frozen=True)
class PrCerrado:
    numero: int
    head: str
    base: str
    estado: Literal["cerrado"] = "cerrado"


EstadoPR = PrSin | PrBorrador | PrAbierto | PrMergeado | PrCerrado


@dataclass(frozen=True)
class PrDeFase:
    fase: str
    pr: EstadoPR
    aceptada_en: str = ""


def estado_de(vista: PrView) -> EstadoPR:
    if vista.estado == "mergeado":
        return PrMergeado(vista.numero, vista.head, vista.base, vista.merge_commit)
    if vista.estado == "cerrado":
        return PrCerrado(vista.numero, vista.head, vista.base)
    if vista.estado == "abierto":
        if vista.borrador:
            return PrBorrador(vista.numero, vista.head, vista.base)
        return PrAbierto(vista.numero, vista.head, vista.base)
    if vista.estado == "sin_pr":
        return PrSin()
    raise ValueError("estado de PR desconocido")
