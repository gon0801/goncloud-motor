"""Variantes tipadas de la situación guardada en Encargo."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal

from .dominio import TransicionIlegal

if TYPE_CHECKING:
    from .dominio import Encargo, Fase, Proc


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
class Contexto:
    orden_parada: Literal["", "parar", "cancelar", "fallo_interno"] = ""
    reanuda_en: str = ""
    intento: str = ""
    proc: Proc | None = None
    reserva: bool = False
    intento_interrumpido: bool = False
    cierre_forzado_fase: Fase | None = None
    cierre_forzado_huella: str = ""
    materializado: bool = field(default=False, compare=False, repr=False)


@dataclass(frozen=True)
class Recibido:
    contexto: Contexto = field(default_factory=Contexto, kw_only=True)
    estado: Literal["recibido"] = "recibido"


@dataclass(frozen=True)
class Ejecutando:
    fase: str
    intento: str
    proc: Proc | None
    reservado: bool
    contexto: Contexto = field(default_factory=Contexto, kw_only=True)
    estado: Literal["ejecutando"] = "ejecutando"


@dataclass(frozen=True)
class EsperandoReintento:
    motivo: str
    reanuda_en: str
    contexto: Contexto = field(default_factory=Contexto, kw_only=True)
    estado: Literal["esperando_reintento"] = "esperando_reintento"


@dataclass(frozen=True)
class Conciliando:
    efecto: str
    contexto: Contexto = field(default_factory=Contexto, kw_only=True)
    estado: Literal["conciliando"] = "conciliando"


@dataclass(frozen=True)
class Deteniendo:
    orden: Parar | Cancelar | FalloInterno
    interrumpido: bool
    contexto: Contexto = field(default_factory=Contexto, kw_only=True)
    estado: Literal["deteniendo"] = "deteniendo"


@dataclass(frozen=True)
class Detenido:
    causa: PorOrden | FalloInterno
    contexto: Contexto = field(default_factory=Contexto, kw_only=True)
    estado: Literal["detenido"] = "detenido"


@dataclass(frozen=True)
class Cancelado:
    contexto: Contexto = field(default_factory=Contexto, kw_only=True)
    estado: Literal["cancelado"] = "cancelado"


@dataclass(frozen=True)
class Terminado:
    resultado: Completo | Parcial
    forzado: CierreForzado | None
    contexto: Contexto = field(default_factory=Contexto, kw_only=True)
    estado: Literal["terminado"] = "terminado"


Situacion = (
    Recibido | Ejecutando | EsperandoReintento | Conciliando
    | Deteniendo | Detenido | Cancelado | Terminado
)


def _no_contradice(anterior: object, actual: object, campo: str) -> None:
    if anterior not in ("", None, False) and anterior != actual:
        raise EstadoIncoherente(f"situación y contexto difieren en {campo}")


def _contexto_inicial(e: Encargo) -> Contexto:
    situacion = e.situacion
    contexto = situacion.contexto
    if isinstance(situacion, Ejecutando):
        _no_contradice(contexto.intento, situacion.intento, "intento")
        _no_contradice(contexto.proc, situacion.proc, "proc")
        _no_contradice(contexto.reserva, situacion.reservado, "reserva")
        _no_contradice(e.fase_activa, situacion.fase, "fase")
        object.__setattr__(e, "fase_activa", situacion.fase)
        contexto = replace(contexto, intento=situacion.intento, proc=situacion.proc,
                           reserva=situacion.reservado)
    elif isinstance(situacion, EsperandoReintento):
        _no_contradice(e.motivo, situacion.motivo, "motivo")
        _no_contradice(contexto.reanuda_en, situacion.reanuda_en, "reanuda_en")
        object.__setattr__(e, "motivo", situacion.motivo)
        contexto = replace(contexto, reanuda_en=situacion.reanuda_en)
    elif isinstance(situacion, Conciliando):
        efecto = next((x for x in e.efectos if x.estado == "incierto"), None)
        if situacion.efecto != (efecto.id if efecto else ""):
            raise EstadoIncoherente("conciliación sin efecto incierto correspondiente")
    elif isinstance(situacion, Deteniendo):
        if isinstance(situacion.orden, FalloInterno) and any((
            situacion.orden.kind, situacion.orden.clase_ukey, situacion.orden.rechazo,
        )):
            raise EstadoIncoherente("datos de fallo interno sin proyección legada")
        orden = (situacion.orden.accion
                 if isinstance(situacion.orden, (Parar, Cancelar)) else "fallo_interno")
        _no_contradice(contexto.orden_parada, orden, "orden_parada")
        _no_contradice(contexto.intento_interrumpido, situacion.interrumpido,
                       "intento_interrumpido")
        contexto = replace(contexto, orden_parada=orden,
                           intento_interrumpido=situacion.interrumpido)
    elif isinstance(situacion, Detenido):
        causa = situacion.causa
        if isinstance(causa, FalloInterno):
            if any((causa.kind, causa.clase_ukey, causa.rechazo)):
                raise EstadoIncoherente("datos de fallo interno sin proyección legada")
            orden = "fallo_interno"
        else:
            if (causa.accion == "reabrir" and not e.reaperturas
                    or causa.accion == "importar" and e.reaperturas):
                raise EstadoIncoherente("causa detenida no coincide con reaperturas")
            orden = "parar" if causa.accion == "parar" else ""
        _no_contradice(contexto.orden_parada, orden, "orden_parada")
        contexto = replace(contexto, orden_parada=orden)
    elif isinstance(situacion, Terminado):
        if situacion.forzado is not None:
            fase = next((f for f in e.fases if f.id == situacion.forzado.fase), None)
            if fase is None:
                raise EstadoIncoherente("cierre forzado sin fase")
            _no_contradice(contexto.cierre_forzado_fase, fase, "cierre_forzado_fase")
            _no_contradice(contexto.cierre_forzado_huella, situacion.forzado.huella,
                           "cierre_forzado_huella")
            contexto = replace(contexto, cierre_forzado_fase=fase,
                               cierre_forzado_huella=situacion.forzado.huella)
        resultado: Completo | Parcial = (
            Completo() if e.objetivo_completo()
            else Parcial(tuple(f.id for f in e.fases if f.estado != "preparada"))
        )
        if situacion.resultado != resultado:
            raise EstadoIncoherente("resultado terminado no coincide con las fases")
    return replace(contexto, materializado=True)


def base_de_estado(estado: str, contexto: Contexto) -> Situacion:
    if estado == "recibido":
        return Recibido(contexto=contexto)
    if estado == "ejecutando":
        return Ejecutando("", contexto.intento, contexto.proc, contexto.reserva,
                         contexto=contexto)
    if estado == "esperando_reintento":
        return EsperandoReintento("", contexto.reanuda_en, contexto=contexto)
    if estado == "conciliando":
        return Conciliando("", contexto=contexto)
    if estado == "deteniendo":
        return Deteniendo(Parar(), contexto.intento_interrumpido, contexto=contexto)
    if estado == "detenido":
        return Detenido(PorOrden("importar"), contexto=contexto)
    if estado == "cancelado":
        return Cancelado(contexto=contexto)
    if estado == "terminado":
        return Terminado(Completo(), None, contexto=contexto)
    raise EstadoIncoherente(f"estado desconocido: {estado}")


def materializar(e: Encargo) -> Situacion:
    contexto = e.situacion.contexto
    if not contexto.materializado:
        contexto = _contexto_inicial(e)
        object.__setattr__(e, "situacion", replace(e.situacion, contexto=contexto))
    if e.estado == "ejecutando":
        return Ejecutando(e.fase_activa, e.intento, e.proc, e.reserva, contexto=contexto)
    if e.estado == "esperando_reintento":
        return EsperandoReintento(e.motivo, e.reanuda_en, contexto=contexto)
    if e.estado == "conciliando":
        efecto = next((x for x in e.efectos if x.estado == "incierto"), None)
        return Conciliando(efecto.id if efecto else "", contexto=contexto)
    if e.estado == "deteniendo":
        orden: Parar | Cancelar | FalloInterno = Parar()
        if e.orden_parada == "cancelar":
            orden = Cancelar()
        elif e.orden_parada == "fallo_interno":
            orden = FalloInterno("", "", "")
        return Deteniendo(orden, e.intento_interrumpido, contexto=contexto)
    if e.estado == "detenido":
        causa: PorOrden | FalloInterno = PorOrden("reabrir" if e.reaperturas else "importar")
        if e.orden_parada == "fallo_interno":
            causa = FalloInterno("", "", "")
        elif e.orden_parada == "parar":
            causa = PorOrden("parar")
        return Detenido(causa, contexto=contexto)
    if e.estado == "terminado":
        resultado: Completo | Parcial = (
            Completo() if e.objetivo_completo()
            else Parcial(tuple(f.id for f in e.fases if f.estado != "preparada"))
        )
        forzado = (CierreForzado(e.cierre_forzado_fase.id, e.cierre_forzado_huella)
                   if e.cierre_forzado_fase else None)
        return Terminado(resultado, forzado, contexto=contexto)
    return base_de_estado(e.estado, contexto)


def situacion_de(e: Encargo) -> Situacion:
    if e.estado == "ejecutando" and not e.ultimo_hecho:
        raise EstadoIncoherente("ejecutando sin hecho")
    if e.estado == "esperando_reintento" and (not e.motivo or not e.reanuda_en):
        raise EstadoIncoherente("espera incompleta")
    if e.estado == "conciliando" and not any(x.estado == "incierto" for x in e.efectos):
        raise EstadoIncoherente("conciliando sin efecto incierto")
    if e.estado == "terminado" and not e.evidencia:
        raise EstadoIncoherente("terminado sin evidencia")
    return e.situacion
