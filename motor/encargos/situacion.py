"""Lectura tipada de los estados del encargo, sin guardar otra copia."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .dominio import TransicionIlegal

if TYPE_CHECKING:
    from .dominio import Encargo, Proc


class EstadoIncoherente(TransicionIlegal):
    pass


@dataclass(frozen=True)
class PorOrden:
    accion: Literal["parar", "reabrir", "importar"]


@dataclass(frozen=True)
class FalloInterno:
    kind: str
    clase_ukey: str
    rechazo: Literal["ajena", "ilegal", ""]


@dataclass(frozen=True)
class Parar:
    accion: Literal["parar"] = "parar"


@dataclass(frozen=True)
class Cancelar:
    accion: Literal["cancelar"] = "cancelar"


@dataclass(frozen=True)
class Completo:
    estado: Literal["completo"] = "completo"


@dataclass(frozen=True)
class Parcial:
    fases: tuple[str, ...]
    estado: Literal["parcial"] = "parcial"


@dataclass(frozen=True)
class CierreForzado:
    fase: str
    huella: str


@dataclass(frozen=True)
class Recibido:
    estado: Literal["recibido"] = "recibido"


@dataclass(frozen=True)
class Ejecutando:
    fase: str
    intento: str
    proc: Proc | None
    reservado: bool
    estado: Literal["ejecutando"] = "ejecutando"


@dataclass(frozen=True)
class EsperandoReintento:
    motivo: str
    reanuda_en: str
    estado: Literal["esperando_reintento"] = "esperando_reintento"


@dataclass(frozen=True)
class Conciliando:
    efecto: str
    estado: Literal["conciliando"] = "conciliando"


@dataclass(frozen=True)
class Deteniendo:
    orden: Parar | Cancelar | FalloInterno
    interrumpido: bool
    estado: Literal["deteniendo"] = "deteniendo"


@dataclass(frozen=True)
class Detenido:
    causa: PorOrden | FalloInterno
    estado: Literal["detenido"] = "detenido"


@dataclass(frozen=True)
class Cancelado:
    estado: Literal["cancelado"] = "cancelado"


@dataclass(frozen=True)
class Terminado:
    resultado: Completo | Parcial
    forzado: CierreForzado | None
    estado: Literal["terminado"] = "terminado"


Situacion = (
    Recibido | Ejecutando | EsperandoReintento | Conciliando
    | Deteniendo | Detenido | Cancelado | Terminado
)


def situacion_de(e: Encargo) -> Situacion:
    if e.estado == "recibido":
        return Recibido()
    if e.estado == "ejecutando":
        if not e.ultimo_hecho:
            raise EstadoIncoherente("ejecutando sin hecho")
        return Ejecutando(e.fase_activa, e.intento, e.proc, e.reserva)
    if e.estado == "esperando_reintento":
        if not e.motivo or not e.reanuda_en:
            raise EstadoIncoherente("espera incompleta")
        return EsperandoReintento(e.motivo, e.reanuda_en)
    if e.estado == "conciliando":
        efecto = next((x for x in e.efectos if x.estado == "incierto"), None)
        if efecto is None:
            raise EstadoIncoherente("conciliando sin efecto incierto")
        return Conciliando(efecto.id)
    if e.estado == "deteniendo":
        orden: Parar | Cancelar | FalloInterno
        if e.orden_parada == "cancelar":
            orden = Cancelar()
        elif e.orden_parada == "fallo_interno":
            orden = FalloInterno("", "", "")
        else:
            orden = Parar()
        return Deteniendo(orden, e.intento_interrumpido)
    if e.estado == "detenido":
        if e.orden_parada == "fallo_interno":
            return Detenido(FalloInterno("", "", ""))
        if e.orden_parada == "parar":
            return Detenido(PorOrden("parar"))
        return Detenido(PorOrden("reabrir" if e.reaperturas else "importar"))
    if e.estado == "cancelado":
        return Cancelado()
    if e.estado == "terminado":
        if not e.evidencia:
            raise EstadoIncoherente("terminado sin evidencia")
        resultado: Completo | Parcial = (
            Completo() if e.objetivo_completo()
            else Parcial(tuple(f.id for f in e.fases if f.estado != "preparada"))
        )
        forzado = (
            CierreForzado(e.cierre_forzado_fase.id, e.cierre_forzado_huella)
            if e.cierre_forzado_fase else None
        )
        return Terminado(resultado, forzado)
    raise EstadoIncoherente(f"estado desconocido: {e.estado}")
