"""Manejadores de los kinds migrados de la reducción."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import detencion, esperas, fase, intento, necesidades, retirados, revision, pedido

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
    'actividad': Fila(intento.actividad),
    'afirmacion': Fila(esperas.afirmacion),
    'arbol': Fila(detencion.arbol),
    'autoridad': Fila(necesidades.autoridad),
    'autoridad_cerrada': Fila(retirados.registrar),
    'autorizacion': Fila(necesidades.autorizacion),
    'bloqueo_fase': Fila(fase.bloqueo_fase),
    'ci_pr': Fila(fase.ci_pr),
    'ci_pr_sondeo': Fila(retirados.registrar),
    'ci_producto': Fila(retirados.registrar),
    'ci_revision_rojo': Fila(retirados.registrar),
    'ci_sondeo': Fila(revision.ci_sondeo),
    'cierre_forzado': Fila(fase.cierre_forzado),
    'colgada': Fila(intento.colgada),
    'comprobacion': Fila(esperas.comprobacion),
    'comprobaciones_supervisor': Fila(revision.comprobaciones_supervisor),
    'concesion': Fila(retirados.registrar),
    'conciliacion_fase': Fila(fase.conciliacion_fase),
    'conductor': Fila(intento.conductor),
    'conductor_fase': Fila(intento.conductor_fase),
    'decision': Fila(necesidades.decision),
    'deepseek_cerrado': Fila(revision.deepseek_cerrado),
    'deepseek_lectura': Fila(revision.deepseek_lectura),
    'disputa': Fila(retirados.registrar),
    'efecto': Fila(esperas.efecto),
    'entrada_incorporada': Fila(fase.entrada_incorporada),
    'entrega_bloqueada': Fila(fase.entrega_bloqueada),
    'entrega_devuelta': Fila(revision.entrega_devuelta),
    'entrega_externa': Fila(fase.entrega_externa),
    'espera': Fila(esperas.espera),
    'estrategia': Fila(necesidades.estrategia),
    'excepcion': Fila(esperas.excepcion),
    'fallo_interno': Fila(detencion.fallo_interno),
    'fase_desplegada': Fila(fase.fase_desplegada),
    'fase_reabierta': Fila(fase.fase_reabierta),
    'fase_reactivada': Fila(fase.fase_reactivada),
    'fases': Fila(fase.fases),
    'hallazgo_fuera_de_alcance': Fila(revision.hallazgo_fuera_de_alcance),
    'herramienta': Fila(necesidades.herramienta),
    'impedimento': Fila(necesidades.impedimento),
    'impedimento_resuelto': Fila(necesidades.impedimento_resuelto),
    'incorporado': Fila(esperas.incorporado),
    'inspeccion_entregada': Fila(revision.inspeccion_entregada),
    'inspeccion_solicitud': Fila(revision.inspeccion_solicitud),
    'instrumento_fallo': Fila(necesidades.instrumento_fallo),
    'instrumento_no_disponible': Fila(necesidades.instrumento_no_disponible),
    'intento_abierto': Fila(intento.intento_abierto),
    'intento_cerrado': Fila(intento.intento_cerrado),
    'lectura_fallida': Fila(fase.lectura_fallida),
    'nivel_efectivo': Fila(fase.nivel_efectivo),
    'nota_pr': Fila(revision.nota_pr),
    'observacion_supervisor': Fila(revision.observacion_supervisor),
    'pendiente_entrega': Fila(esperas.pendiente_entrega),
    'plazo': Fila(esperas.plazo),
    'politica_despliegue': Fila(fase.politica_despliegue),
    'pregunta': Fila(necesidades.pregunta),
    'producto_listo': Fila(fase.producto_listo),
    'quietud': Fila(necesidades.quietud),
    'quietud_vigilada': Fila(esperas.quietud_vigilada),
    'reserva': Fila(intento.reserva),
    'reserva_cerrada': Fila(intento.reserva_cerrada),
    'resolucion': Fila(revision.resolucion),
    'respuesta': Fila(revision.respuesta),
    'respuesta_revisor_pedida': Fila(retirados.registrar),
    'resultado_directo': Fila(fase.resultado_directo),
    'revision': Fila(revision.revision),
    'sesion_perdida': Fila(intento.sesion_perdida),
    'sin_progreso': Fila(esperas.sin_progreso),
    'solicitud': Fila(necesidades.solicitud),
    'solicitud_tardia': Fila(necesidades.solicitud_tardia),
    'via': Fila(necesidades.via),
}
