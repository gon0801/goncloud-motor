"""Manejadores de los kinds migrados de la reducción."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from . import detencion, esperas, fase, intento, necesidades, retirados, revision, pedido

if TYPE_CHECKING:
    from ..dominio import Encargo


@dataclass(frozen=True)
class Fila:
    aplicar: Callable[[Encargo, dict, str, bool], Encargo]
    alcance: Literal["encargo", "fase", "intento"] = "encargo"
    corta_series: bool = True
    marcador: bool = False


TABLA: dict[str, Fila] = {
    "recibo": Fila(pedido.recibo),
    "ruta_agentes": Fila(pedido.ruta_agentes),
    "contexto_entrega": Fila(pedido.contexto_entrega, corta_series=False),
    "proveedor_fallo": Fila(pedido.proveedor_fallo),
    "proveedor_disponible": Fila(pedido.proveedor_disponible),
    "falla_sesion_local": Fila(pedido.falla_sesion_local),
    "orden": Fila(pedido.orden),
    "ajuste": Fila(pedido.ajuste, alcance="fase"),
    "procedimiento": Fila(pedido.procedimiento),
    "migracion": Fila(pedido.migracion),
    'actividad': Fila(intento.actividad, alcance="intento"),
    'afirmacion': Fila(esperas.afirmacion),
    'arbol': Fila(detencion.arbol),
    'autoridad': Fila(necesidades.autoridad),
    'autoridad_cerrada': Fila(retirados.registrar),
    'autorizacion': Fila(necesidades.autorizacion),
    'bloqueo_fase': Fila(fase.bloqueo_fase, alcance="fase"),
    'ci_pr': Fila(fase.ci_pr, alcance="fase"),
    'ci_pr_sondeo': Fila(retirados.registrar),
    'ci_producto': Fila(retirados.registrar),
    'ci_revision_rojo': Fila(retirados.registrar),
    'ci_sondeo': Fila(revision.ci_sondeo, corta_series=False, marcador=True),
    'cierre_forzado': Fila(fase.cierre_forzado, alcance="fase"),
    'colgada': Fila(intento.colgada, alcance="intento"),
    'comprobacion': Fila(esperas.comprobacion),
    'comprobaciones_supervisor': Fila(revision.comprobaciones_supervisor, alcance="fase"),
    'concesion': Fila(retirados.registrar),
    'conciliacion_fase': Fila(fase.conciliacion_fase, alcance="fase", corta_series=False, marcador=True),
    'conductor': Fila(intento.conductor, alcance="intento"),
    'conductor_fase': Fila(intento.conductor_fase, alcance="fase"),
    'decision': Fila(necesidades.decision),
    'deepseek_cerrado': Fila(revision.deepseek_cerrado, corta_series=False, marcador=True),
    'deepseek_lectura': Fila(revision.deepseek_lectura, corta_series=False, marcador=True),
    'disputa': Fila(retirados.registrar),
    'efecto': Fila(esperas.efecto),
    'entrada_incorporada': Fila(fase.entrada_incorporada, alcance="fase"),
    'entrega_bloqueada': Fila(fase.entrega_bloqueada, alcance="fase"),
    'entrega_devuelta': Fila(revision.entrega_devuelta, alcance="fase"),
    'entrega_externa': Fila(fase.entrega_externa, alcance="fase"),
    'espera': Fila(esperas.espera, corta_series=False),
    'estrategia': Fila(necesidades.estrategia),
    'excepcion': Fila(esperas.excepcion, corta_series=False, marcador=True),
    'fallo_interno': Fila(detencion.fallo_interno),
    'fase_desplegada': Fila(fase.fase_desplegada, alcance="fase"),
    'fase_reabierta': Fila(fase.fase_reabierta, alcance="fase"),
    'fase_reactivada': Fila(fase.fase_reactivada, alcance="fase"),
    'fases': Fila(fase.fases),
    'hallazgo_fuera_de_alcance': Fila(revision.hallazgo_fuera_de_alcance),
    'herramienta': Fila(necesidades.herramienta),
    'impedimento': Fila(necesidades.impedimento),
    'impedimento_resuelto': Fila(necesidades.impedimento_resuelto),
    'incorporado': Fila(esperas.incorporado, corta_series=False, marcador=True),
    'inspeccion_entregada': Fila(revision.inspeccion_entregada, alcance="fase"),
    'inspeccion_solicitud': Fila(revision.inspeccion_solicitud, alcance="fase"),
    'instrumento_fallo': Fila(necesidades.instrumento_fallo, corta_series=False),
    'instrumento_no_disponible': Fila(necesidades.instrumento_no_disponible),
    'intento_abierto': Fila(intento.intento_abierto, alcance="intento"),
    'intento_cerrado': Fila(intento.intento_cerrado, alcance="intento"),
    'lectura_fallida': Fila(fase.lectura_fallida, alcance="fase"),
    'nivel_efectivo': Fila(fase.nivel_efectivo),
    'nota_pr': Fila(revision.nota_pr, alcance="fase"),
    'observacion_supervisor': Fila(revision.observacion_supervisor),
    'pendiente_entrega': Fila(esperas.pendiente_entrega),
    'plazo': Fila(esperas.plazo, corta_series=False),
    'politica_despliegue': Fila(fase.politica_despliegue, alcance="fase"),
    'pregunta': Fila(necesidades.pregunta),
    'producto_listo': Fila(fase.producto_listo, alcance="fase"),
    'quietud': Fila(necesidades.quietud),
    'quietud_vigilada': Fila(esperas.quietud_vigilada, corta_series=False, marcador=True),
    'reserva': Fila(intento.reserva, alcance="intento", corta_series=False),
    'reserva_cerrada': Fila(intento.reserva_cerrada, alcance="intento", corta_series=False),
    'resolucion': Fila(revision.resolucion, alcance="fase"),
    'respuesta': Fila(revision.respuesta, alcance="fase"),
    'respuesta_revisor_pedida': Fila(retirados.registrar),
    'resultado_directo': Fila(fase.resultado_directo),
    'revision': Fila(revision.revision, alcance="fase"),
    # La recuperación de la copia de sesión no es un suceso distinto entre
    # esperas: perder la sesión acompaña a las notas de copia, que tampoco
    # cortan la serie.
    'sesion_perdida': Fila(intento.sesion_perdida, alcance="intento", corta_series=False),
    'sin_progreso': Fila(esperas.sin_progreso),
    'solicitud': Fila(necesidades.solicitud),
    'solicitud_tardia': Fila(necesidades.solicitud_tardia),
    'via': Fila(necesidades.via),
}

EVENTOS_DE_MARCADOR: tuple[str, ...] = tuple(
    kind for kind, fila in TABLA.items() if fila.marcador
)
ENTRE_REINTENTOS: tuple[str, ...] = tuple(
    kind for kind, fila in TABLA.items() if not fila.corta_series and not fila.marcador
)
