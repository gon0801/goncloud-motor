"""Manejadores de los kinds migrados de la reducción."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from . import detencion, esperas, fase, intento, necesidades, retirados, revision, pedido
from . import validacion_intento
from . import validacion_esperas
from . import validacion_necesidades
from . import validacion_fase
from . import validacion_revision
from . import validacion_pedido
from . import validacion_detencion
from .errores import HechoInaplicable

if TYPE_CHECKING:
    from ..dominio import Encargo


@dataclass(frozen=True)
class Fila:
    aplicar: Callable[[Encargo, dict, str, bool], Encargo]
    alcance: Literal["encargo", "fase", "intento"] = "encargo"
    corta_series: bool = True
    marcador: bool = False
    validar: Callable[[Encargo, dict], None] | None = None
    carga_parcial: bool = False

    def reducir(self, e: Encargo, d: dict, hora: str, nuevo: bool) -> Encargo:
        if nuevo and self.validar is not None:
            self.validar(e, d)
        try:
            return self.aplicar(e, d, hora, nuevo)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            if nuevo or not self.carga_parcial:
                raise
            raise HechoInaplicable from exc


def aplicar_fila(e: Encargo, kind: str, d: dict, hora: str, nuevo: bool) -> Encargo:
    from ..dominio import TransicionIlegal

    fila = TABLA.get(kind)
    if fila is None:
        if nuevo:
            raise TransicionIlegal(f"evento desconocido: {kind}")
        return e
    return fila.reducir(e, d, hora, nuevo)


def plegar_evento(
    anterior: Encargo | None, evento: dict, nuevo: bool,
    reducir_evento: Callable[[Encargo | None, dict, bool], Encargo],
    derivados: Callable[[Encargo, dict], Encargo],
) -> Encargo:
    try:
        aplicado = reducir_evento(anterior, evento, nuevo)
    except HechoInaplicable:
        if nuevo or anterior is None:
            raise
        return anterior
    if aplicado is anterior and (
        evento["kind"] not in TABLA
        or evento["kind"] in ("cierre_forzado", "bloqueo_fase", "producto_listo")
    ):
        return aplicado
    return derivados(aplicado, evento)


TABLA: dict[str, Fila] = {
    "recibo": Fila(pedido.recibo),
    "ruta_agentes": Fila(pedido.ruta_agentes, validar=validacion_pedido.validar_ruta_agentes, carga_parcial=True),
    "contexto_entrega": Fila(pedido.contexto_entrega, corta_series=False, validar=validacion_pedido.validar_contexto_entrega),
    "proveedor_fallo": Fila(pedido.proveedor_fallo, validar=validacion_pedido.validar_proveedor_fallo, carga_parcial=True),
    "proveedor_disponible": Fila(pedido.proveedor_disponible, validar=validacion_pedido.validar_proveedor_disponible, carga_parcial=True),
    "falla_sesion_local": Fila(pedido.falla_sesion_local),
    "orden": Fila(pedido.orden, validar=validacion_pedido.validar_orden, carga_parcial=True),
    "ajuste": Fila(pedido.ajuste, alcance="fase", validar=validacion_pedido.validar_ajuste, carga_parcial=True),
    "procedimiento": Fila(pedido.procedimiento, validar=validacion_pedido.validar_procedimiento, carga_parcial=True),
    "migracion": Fila(pedido.migracion),
    'actividad': Fila(intento.actividad, alcance="intento", validar=validacion_intento.validar_actividad),
    'afirmacion': Fila(esperas.afirmacion, validar=validacion_esperas.validar_afirmacion),
    'arbol': Fila(detencion.arbol),
    'autoridad': Fila(necesidades.autoridad, validar=validacion_necesidades.validar_autoridad),
    'autoridad_cerrada': Fila(retirados.registrar),
    'autorizacion': Fila(necesidades.autorizacion, validar=validacion_necesidades.validar_autorizacion),
    'bloqueo_fase': Fila(fase.bloqueo_fase, alcance="fase", validar=validacion_fase.validar_bloqueo_fase),
    'ci_pr': Fila(fase.ci_pr, alcance="fase", validar=validacion_fase.validar_ci_pr, carga_parcial=True),
    'ci_pr_sondeo': Fila(retirados.registrar),
    'ci_producto': Fila(retirados.registrar),
    'ci_revision_rojo': Fila(retirados.registrar),
    'ci_sondeo': Fila(revision.ci_sondeo, corta_series=False, marcador=True, carga_parcial=True),
    'cierre_forzado': Fila(fase.cierre_forzado, alcance="fase", validar=validacion_fase.validar_cierre_forzado),
    'colgada': Fila(intento.colgada, alcance="intento", validar=validacion_intento.validar_colgada),
    'comprobacion': Fila(esperas.comprobacion, validar=validacion_esperas.validar_comprobacion),
    'comprobaciones_supervisor': Fila(revision.comprobaciones_supervisor, alcance="fase", carga_parcial=True),
    'concesion': Fila(retirados.registrar),
    'conciliacion_fase': Fila(fase.conciliacion_fase, alcance="fase", corta_series=False, marcador=True, validar=validacion_fase.validar_conciliacion_fase, carga_parcial=True),
    'conductor': Fila(intento.conductor, alcance="intento", validar=validacion_intento.validar_conductor),
    'conductor_fase': Fila(intento.conductor_fase, alcance="fase", validar=validacion_intento.validar_conductor_fase),
    'decision': Fila(necesidades.decision, validar=validacion_necesidades.validar_decision),
    'deepseek_cerrado': Fila(revision.deepseek_cerrado, corta_series=False, marcador=True),
    'deepseek_lectura': Fila(revision.deepseek_lectura, corta_series=False, marcador=True),
    'disputa': Fila(retirados.registrar),
    'efecto': Fila(esperas.efecto, validar=validacion_esperas.validar_efecto),
    'entrada_incorporada': Fila(fase.entrada_incorporada, alcance="fase", validar=validacion_fase.validar_entrada_incorporada),
    'entrega_bloqueada': Fila(fase.entrega_bloqueada, alcance="fase", validar=validacion_fase.validar_entrega_bloqueada),
    'entrega_devuelta': Fila(revision.entrega_devuelta, alcance="fase"),
    'entrega_externa': Fila(fase.entrega_externa, alcance="fase", carga_parcial=True),
    'espera': Fila(esperas.espera, corta_series=False, validar=validacion_esperas.validar_espera),
    'estrategia': Fila(necesidades.estrategia, validar=validacion_necesidades.validar_estrategia),
    'excepcion': Fila(esperas.excepcion, corta_series=False, marcador=True),
    'fallo_interno': Fila(detencion.fallo_interno, validar=validacion_detencion.validar_fallo_interno),
    'fase_desplegada': Fila(fase.fase_desplegada, alcance="fase", validar=validacion_fase.validar_fase_desplegada),
    'fase_reabierta': Fila(fase.fase_reabierta, alcance="fase", validar=validacion_fase.validar_fase_reabierta),
    'fase_reactivada': Fila(fase.fase_reactivada, alcance="fase", validar=validacion_fase.validar_fase_reactivada),
    'fases': Fila(fase.fases),
    'hallazgo_fuera_de_alcance': Fila(revision.hallazgo_fuera_de_alcance),
    'herramienta': Fila(necesidades.herramienta, validar=validacion_necesidades.validar_herramienta),
    'impedimento': Fila(necesidades.impedimento, validar=validacion_necesidades.validar_impedimento),
    'impedimento_resuelto': Fila(necesidades.impedimento_resuelto, validar=validacion_necesidades.validar_impedimento_resuelto),
    'incorporado': Fila(esperas.incorporado, corta_series=False, marcador=True, validar=validacion_esperas.validar_incorporado),
    'inspeccion_entregada': Fila(revision.inspeccion_entregada, alcance="fase", validar=validacion_revision.validar_inspeccion_entregada),
    'inspeccion_solicitud': Fila(revision.inspeccion_solicitud, alcance="fase", validar=validacion_revision.validar_inspeccion_solicitud),
    'instrumento_fallo': Fila(necesidades.instrumento_fallo, corta_series=False),
    'instrumento_no_disponible': Fila(necesidades.instrumento_no_disponible, validar=validacion_necesidades.validar_instrumento_no_disponible),
    'intento_abierto': Fila(intento.intento_abierto, alcance="intento", validar=validacion_intento.validar_intento_abierto, carga_parcial=True),
    'intento_cerrado': Fila(intento.intento_cerrado, alcance="intento"),
    'lectura_fallida': Fila(fase.lectura_fallida, alcance="fase"),
    'nivel_efectivo': Fila(fase.nivel_efectivo, validar=validacion_fase.validar_nivel_efectivo),
    'nota_pr': Fila(revision.nota_pr, alcance="fase"),
    'observacion_supervisor': Fila(revision.observacion_supervisor),
    'pendiente_entrega': Fila(esperas.pendiente_entrega),
    'plazo': Fila(esperas.plazo, corta_series=False),
    'politica_despliegue': Fila(fase.politica_despliegue, alcance="fase", validar=validacion_fase.validar_politica_despliegue),
    'pregunta': Fila(necesidades.pregunta),
    'producto_listo': Fila(fase.producto_listo, alcance="fase", validar=validacion_fase.validar_producto_listo),
    'quietud': Fila(necesidades.quietud, validar=validacion_necesidades.validar_quietud),
    'quietud_vigilada': Fila(esperas.quietud_vigilada, corta_series=False, marcador=True, validar=validacion_esperas.validar_quietud_vigilada),
    'reserva': Fila(intento.reserva, alcance="intento", corta_series=False, validar=validacion_intento.validar_reserva, carga_parcial=True),
    'reserva_cerrada': Fila(intento.reserva_cerrada, alcance="intento", corta_series=False),
    'resolucion': Fila(revision.resolucion, alcance="fase", validar=validacion_revision.validar_resolucion),
    'respuesta': Fila(revision.respuesta, alcance="fase"),
    'respuesta_revisor_pedida': Fila(retirados.registrar),
    'resultado_directo': Fila(fase.resultado_directo, validar=validacion_fase.validar_resultado_directo),
    'revision': Fila(revision.revision, alcance="fase", carga_parcial=True),
    # La recuperación de la copia de sesión no es un suceso distinto entre
    # esperas: perder la sesión acompaña a las notas de copia, que tampoco
    # cortan la serie.
    'sesion_perdida': Fila(intento.sesion_perdida, alcance="intento", corta_series=False),
    'sin_progreso': Fila(esperas.sin_progreso),
    'solicitud': Fila(necesidades.solicitud, validar=validacion_necesidades.validar_solicitud),
    'solicitud_tardia': Fila(necesidades.solicitud_tardia),
    'via': Fila(necesidades.via, validar=validacion_necesidades.validar_via),
}

EVENTOS_DE_MARCADOR: tuple[str, ...] = tuple(
    kind for kind, fila in TABLA.items() if fila.marcador
)
ENTRE_REINTENTOS: tuple[str, ...] = tuple(
    kind for kind, fila in TABLA.items() if not fila.corta_series and not fila.marcador
)
