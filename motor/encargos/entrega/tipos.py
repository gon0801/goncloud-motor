"""Estados del PR observados por el motor, sin consultar GitHub."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


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
