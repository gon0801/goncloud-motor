"""Tabla pura de decisiones a partir de los PR observados."""

from __future__ import annotations

from dataclasses import dataclass

from .tipos import (
    EstadoPR, PrAbierto, PrBorrador, PrCerrado, PrMergeado, PrSin,
)


@dataclass(frozen=True)
class ContextoEntrega:
    fase: str
    nivel: str
    pr: EstadoPR
    base_pedida: str
    otras_ramas: tuple[tuple[str, EstadoPR, str], ...] = ()
    aceptada_en: str = ""
    nivel_alcanzado: str = ""
    final: bool = False
    cancelado: bool = False
    canceladas: tuple[str, ...] = ()
    proceso_vivo: bool = False
    reserva: bool = False
    revision_lista: bool = False
    otra_base_de: tuple[str, str] = ("", "")


@dataclass(frozen=True)
class AbrirPR:
    pass


@dataclass(frozen=True)
class MarcarListo:
    pass


@dataclass(frozen=True)
class SeguirRevision:
    pass


@dataclass(frozen=True)
class Mergear:
    pass


@dataclass(frozen=True)
class AnotarMergeado:
    pass


@dataclass(frozen=True)
class AnotarObservado:
    pass


@dataclass(frozen=True)
class Desplegar:
    pass


@dataclass(frozen=True)
class CancelarFase:
    fase: str
    numero: int


@dataclass(frozen=True)
class DetenerOtraBase:
    fase: str
    numero: int
    base: str


@dataclass(frozen=True)
class Esperar:
    pass


@dataclass(frozen=True)
class Nada:
    pass


Accion = (
    AbrirPR | MarcarListo | SeguirRevision | Mergear | AnotarMergeado
    | AnotarObservado | Desplegar | CancelarFase | DetenerOtraBase | Esperar | Nada
)


def _base_integrada(c: ContextoEntrega, base: str) -> bool:
    if base in (c.base_pedida, c.aceptada_en) and base:
        return True
    por_rama = {rama: (pr, aceptada) for rama, pr, aceptada in c.otras_ramas}
    visitadas: set[str] = set()
    while base in por_rama and base not in visitadas:
        visitadas.add(base)
        pr, aceptada = por_rama[base]
        if not isinstance(pr, PrMergeado):
            return False
        if pr.base == c.base_pedida or aceptada == pr.base:
            return True
        base = pr.base
    return False


def decidir(c: ContextoEntrega) -> Accion:
    if c.cancelado or c.fase in c.canceladas:
        return Nada()
    if isinstance(c.pr, PrSin):
        return Nada() if c.final else AbrirPR()
    if c.proceso_vivo or c.reserva:
        return Esperar()
    if isinstance(c.pr, PrBorrador):
        return AnotarObservado() if c.final else MarcarListo()
    if isinstance(c.pr, PrAbierto):
        if c.nivel == "pr" or not c.revision_lista or c.final:
            return SeguirRevision()
        return Mergear()
    if isinstance(c.pr, PrCerrado):
        return CancelarFase(c.fase, c.pr.numero)
    if isinstance(c.pr, PrMergeado):
        if _base_integrada(c, c.pr.base):
            if c.nivel == "desplegado" and c.nivel_alcanzado == "integrado":
                return Desplegar()
            return AnotarMergeado()
        if c.final or any(rama == c.pr.base for rama, _, _ in c.otras_ramas):
            return AnotarObservado()
        if c.otra_base_de == (c.fase, c.pr.base):
            return Nada()
        return DetenerOtraBase(c.fase, c.pr.numero, c.pr.base)
    return Nada()
