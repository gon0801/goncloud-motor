"""Palabras de entrega derivadas únicamente del estado plegado."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .despliegue import clave_de_recibo
from .dominio import Encargo, Fase, dependencia_bloqueada, nivel_objetivo
from .entrega.tipos import PrAbierto, PrBorrador, PrCerrado, PrDeFase, PrMergeado
from .situacion import Detenido, FalloInterno, PorOrden, PorOtraBase

Palabra = Literal[
    "detenido", "listo para tu merge", "falta limpiar", "cancelado",
    "entrega parcial", "entregado", "integrado", "respondido", "sin cambios",
]


@dataclass(frozen=True)
class ReporteFase:
    id: str
    palabra: Palabra
    causa: str


@dataclass(frozen=True)
class Reporte:
    palabra: Palabra
    causa: str
    fases: tuple[ReporteFase, ...]


def _rama(enc: Encargo, fase: Fase) -> str:
    if fase.rama:
        return fase.rama
    if len(enc.fases) <= 1:
        return enc.entrega.rama
    return f"{enc.entrega.rama}-{fase.id}"


def _mergeada(enc: Encargo, fase: Fase, prs: dict[str, PrDeFase],
              visitadas: frozenset[str] = frozenset(), *, destino: bool = False) -> bool:
    observada = prs.get(fase.id)
    if observada is None:
        return bool(fase.integrado_sha)
    pr = observada.pr
    if not isinstance(pr, PrMergeado):
        return False
    if ((enc.estado == "terminado" and not destino)
        or (pr.base and pr.base in (enc.entrega.base, observada.aceptada_en))):
        return True
    if fase.id in visitadas:
        return False
    return any(
        _mergeada(enc, otra, prs, visitadas | {fase.id}, destino=destino)
        for otra in enc.fases
        if otra.id != fase.id and _rama(enc, otra) == pr.base
    )


def _desplegada(enc: Encargo, fase: Fase) -> bool:
    if not fase.desplegado_recibo:
        return False
    ruta = Path(fase.desplegado_recibo)
    return (ruta.name == "recibo.json"
            and ruta.parent.parent.name == "despliegues"
            and ruta.parent.name == clave_de_recibo(enc, fase))


def _causa_integrada(enc: Encargo, fase: Fase,
                     observada: PrDeFase | None) -> str:
    if fase.desplegado_recibo and not _desplegada(enc, fase):
        return "El recibo es de una versión anterior; falta desplegar la vigente."
    if nivel_objetivo(enc) == "desplegado":
        falta = "falta desplegar"
    elif enc.entrega.nivel == "desplegado":
        falta = f"no se pudo desplegar: {enc.nivel_efectivo_motivo or 'limitación comprobada'}"
    else:
        falta = "no se pidió desplegar"
    if observada is not None and isinstance(observada.pr, PrMergeado):
        if observada.aceptada_en and observada.pr.base == observada.aceptada_en:
            return f"David aceptó la integración en {observada.aceptada_en}; {falta}."
        if observada.pr.base != enc.entrega.base:
            if _desplegada(enc, fase):
                return (f"La fase quedó en {observada.pr.base}, no en {enc.entrega.base}; "
                        "el despliegue no acredita la base pedida.")
            return f"David integró esta fase en {observada.pr.base}; {falta}."
    return f"La fase está integrada; {falta}."


def _fase(enc: Encargo, fase: Fase, prs: dict[str, PrDeFase]) -> ReporteFase:
    observada = prs.get(fase.id)
    if fase.id in enc.canceladas:
        return ReporteFase(fase.id, "cancelado", fase.bloqueo_causa or "La fase se canceló.")
    if fase.estado == "bloqueada":
        return ReporteFase(fase.id, "entrega parcial",
                           fase.bloqueo_causa or "La fase quedó bloqueada.")
    if fase.estado == "pendiente":
        origen = dependencia_bloqueada(enc.fases, fase)
        causa = (f"Dependía de la fase {origen}, que quedó bloqueada."
                 if origen else "La fase todavía no se trabajó.")
        return ReporteFase(fase.id, "entrega parcial", causa)
    if _mergeada(enc, fase, prs):
        if _desplegada(enc, fase) and _mergeada(enc, fase, prs, destino=True):
            base = (
                observada.aceptada_en
                if (observada is not None and isinstance(observada.pr, PrMergeado)
                    and observada.aceptada_en == observada.pr.base)
                else ""
            )
            causa = (f"David aceptó la integración en {base}; integrada y desplegada."
                     if base else "Integrada y desplegada.")
            return ReporteFase(fase.id, "entregado", causa)
        return ReporteFase(fase.id, "integrado", _causa_integrada(enc, fase, observada))
    if fase.ultimo_paso_entrega == "sin_cambios":
        return ReporteFase(fase.id, "sin cambios", "No hubo cambios que entregar.")
    if observada is not None and isinstance(observada.pr, PrAbierto):
        return ReporteFase(fase.id, "listo para tu merge", "El PR sigue abierto.")
    if observada is not None and isinstance(observada.pr, PrBorrador):
        return ReporteFase(fase.id, "entrega parcial", "El PR sigue en borrador.")
    if observada is not None and isinstance(observada.pr, PrCerrado):
        return ReporteFase(fase.id, "entrega parcial", "El PR se cerró sin merge.")
    if fase.estado == "preparada" and not fase.pr_numero:
        return ReporteFase(fase.id, "respondido", "El resultado se entregó sin PR.")
    return ReporteFase(fase.id, "entrega parcial", "No se acreditó la integración.")


def _detencion(enc: Encargo) -> str:
    causa = enc.situacion.causa if isinstance(enc.situacion, Detenido) else None
    if isinstance(causa, PorOtraBase):
        return f"El PR de la fase {causa.fase} se mergeó en {causa.base}; puedes usar continuar."
    if isinstance(causa, FalloInterno):
        return "Un fallo interno detuvo el encargo; puedes usar continuar."
    if isinstance(causa, PorOrden):
        origen = {
            "parar": "por orden de David",
            "reabrir": "al reabrirlo",
            "importar": "al importar un plan anterior",
        }[causa.accion]
        return f"El encargo se detuvo {origen}; puedes usar continuar."
    return "El encargo está detenido; puedes usar continuar."


def reporte(enc: Encargo, limpieza_activa: bool) -> Reporte | None:
    if enc.estado not in ("detenido", "terminado", "cancelado"):
        return None
    prs = {pr.fase: pr for pr in enc.prs}
    fases = tuple(_fase(enc, fase, prs) for fase in enc.fases)
    if enc.estado == "detenido":
        return Reporte("detenido", _detencion(enc), fases)
    if (enc.estado == "terminado" and nivel_objetivo(enc) == "pr"
        and not any(f.palabra in ("entrega parcial", "cancelado") for f in fases)
        and any(isinstance(pr.pr, PrAbierto) and pr.fase not in enc.canceladas
                for pr in enc.prs)):
        return Reporte("listo para tu merge",
                       "El trabajo está listo; David puede mergear el PR.", fases)
    pendientes = sorted({tipo for tipo, _, resultado, _, _ in enc.limpieza
                         if resultado in ("fallo", "agotado")})
    if pendientes or not enc.limpio:
        causa = (f"Falta limpiar: {', '.join(pendientes)}."
                 if pendientes else "Falta constancia de limpieza.")
        if not limpieza_activa:
            causa += " La limpieza automática no está encendida."
        return Reporte("falta limpiar", causa, fases)
    if enc.estado == "cancelado":
        return Reporte("cancelado", "David canceló el encargo.", fases)
    parciales = [f for f in fases if f.palabra in ("entrega parcial", "cancelado")]
    if parciales:
        return Reporte("entrega parcial",
                       "Fases sin terminar: " + "; ".join(
                           f"{f.id}: {f.causa}" for f in parciales
                       ), fases)
    nivel = nivel_objetivo(enc)
    con_cambios = [f for f in fases if f.palabra != "sin cambios"]
    if con_cambios and all(f.palabra == "entregado" for f in con_cambios):
        aceptadas = [
            f"{pr.fase} en {pr.aceptada_en}" for pr in enc.prs
            if pr.aceptada_en and isinstance(pr.pr, PrMergeado)
            and pr.pr.base == pr.aceptada_en
        ]
        causa = "Todas las fases están integradas y desplegadas."
        if aceptadas:
            causa += " David aceptó " + ", ".join(aceptadas) + "."
        return Reporte("entregado", causa, fases)
    if con_cambios and all(f.palabra in ("entregado", "integrado") for f in con_cambios):
        motivos = [f"{f.id}: {f.causa}" for f in con_cambios if f.palabra == "integrado"]
        return Reporte("integrado", "; ".join(motivos) or "Todas las fases están integradas.",
                       fases)
    if (fases and not con_cambios
        or not fases and enc.nivel_alcanzado == "sin_cambios"):
        return Reporte("sin cambios", "No hubo cambios que entregar.", fases)
    sin_pr = not enc.pr_numero and all(
        not isinstance(pr.pr, (PrAbierto, PrBorrador, PrMergeado, PrCerrado))
        for pr in enc.prs
    )
    if nivel == "terminado" and sin_pr and (
        bool(enc.evidencia) or bool(fases) and all(f.palabra == "respondido" for f in fases)
    ):
        return Reporte("respondido", "El resultado se entregó sin PR.", fases)
    return Reporte("entrega parcial", "No se acreditó la entrega de todas las fases.", fases)
