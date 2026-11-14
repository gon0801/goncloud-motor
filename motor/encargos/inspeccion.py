"""La inspección que pide el revisor: la liga, su solicitud y su entrega, sobre un `Lote`.

`ligar` arma la liga en el mismo lote que el juicio no concluyente y su
`incorporado`, así que un apagón deja las tres cosas o ninguna. Si una
capacidad concedida cubre el pedido, deja antes la petición en el buzón: cortado
antes del `COMMIT`, el archivo queda sin registrar y `necesidades.atender` lo
registra con la misma identidad en la vuelta siguiente.
"""

from __future__ import annotations

from pathlib import Path

from . import capacidades, necesidades
from .comprobacion import JuicioValidado
from .dominio import Encargo, Solicitud, marca_inspeccion_entregada, respuesta_consumida
from .lote import Lote
from .registro import Evento


def entregada(enc: Encargo, sol: Solicitud, hora: str) -> Evento:
    """La respuesta de `sol` ya llegó al revisor."""
    marca = marca_inspeccion_entregada(sol)
    return Evento(
        "inspeccion_entregada",
        {
            "fase": enc.fase_activa,
            "intento": enc.intento,
            "solicitud": sol.id,
            "marca": marca,
            "hora": hora,
        },
        f"inspeccion-entregada:{enc.id}:{enc.fase_activa}:{marca}",
    )


def consumir_ligada(lote: Lote, hora: str) -> Lote:
    """Al cambiar de consulta, la respuesta ya entregada queda consumida.

    Si no se registra, volver a la consulta anterior la re-liga como si nadie
    la hubiera visto y el revisor gasta otro turno con el mismo error.
    """
    enc = lote.estado
    fase = enc.fase_actual
    if fase is None or not fase.inspeccion_solicitud:
        return lote
    sol = next((s for s in enc.solicitudes if s.id == fase.inspeccion_solicitud), None)
    if sol is None or sol.estado in ("pendiente", "pregunta"):
        return lote
    if respuesta_consumida(fase, marca_inspeccion_entregada(sol)):
        return lote
    return lote.con(entregada(enc, sol, hora))


def _liga(enc: Encargo, ident: str, hora: str) -> Evento:
    return Evento(
        "inspeccion_solicitud",
        {"fase": enc.fase_activa, "intento": enc.intento, "solicitud": ident, "hora": hora},
        f"inspeccion:{enc.id}:{enc.fase_activa}:{ident}:{enc.generacion}",
    )


def _pregunta(
    enc: Encargo, ident: str, juicio: JuicioValidado, pregunta: str, intento: str, hora: str
) -> Evento:
    """La consulta que ninguna capacidad concedida cubre: visible, sin bloquear."""
    pedido = juicio.inspeccion
    return Evento(
        "solicitud",
        {
            "id": ident,
            "operacion": pedido.operacion if pedido is not None else "",
            "recurso": pedido.recurso if pedido is not None else "",
            "fase": enc.fase_activa,
            "intento": intento,
            "generacion": enc.generacion,
            "conductor": enc.conductor,
            "motivo": pregunta[:necesidades.MAX_MOTIVO],
            "parametros": [list(x) for x in (pedido.parametros if pedido is not None else ())],
            "estado": "pregunta",
            "pregunta": (
                pregunta
                + "Ninguna capacidad concedida del puente la cubre"
                + (
                    f": el recurso {pedido.recurso} no está concedido"
                    if pedido is not None
                    else ""
                )
                + f". Concede la capacidad o responde a la solicitud {ident}."
            ),
            "hora": hora,
        },
        f"solicitud:{enc.id}:{ident}",
    )


def ligar(
    lote: Lote, juicio: JuicioValidado, ident: str, espacio: Path, base: Path, hora: str
) -> Lote:
    """La inspección pendiente pasa por el puente: petición ejecutable si una
    capacidad concedida la cubre, o espera identificada del propietario."""
    enc = lote.estado
    fase = enc.fase_actual
    if fase is None:
        return lote
    previa = next((s for s in enc.solicitudes if s.id == ident), None)
    if fase.inspeccion_solicitud == ident and previa is not None:
        # La pregunta ya está registrada como solicitud visible y no bloquea: el
        # barrido sigue y el freno de no concluyentes acota la repetición.
        return lote
    # Con la liga puesta pero sin solicitud registrada (llegó tarde, con el
    # encargo detenido) el pedido se vuelve a encolar en vez de darse por atendido.
    if previa is not None:
        if respuesta_consumida(fase, marca_inspeccion_entregada(previa)):
            # La respuesta de esa solicitud ya se entregó y consumió: el mismo
            # pedido no re-liga ni gasta otra entrega.
            return lote
        # El juicio vuelve a pedir una inspección cuya respuesta existe pero no
        # se entregó a esta vuelta: se re-liga para que el próximo turno la reciba.
        return lote.con(_liga(enc, ident, hora))
    lote = lote.con(_liga(enc, ident, hora))
    enc = lote.estado
    try:
        recursos = capacidades.cargar(base)
    except capacidades.ConfigInvalida:
        recursos = {}
    rev = enc.revision_vigente.revision_id if enc.revision_vigente else ""
    pregunta = (
        f"La revisión {rev or 'vigente'} de la fase {enc.fase_activa} (versión "
        f"{juicio.version[:12]}) no concluyó: falta «{juicio.inspeccion_faltante}». "
    )
    intento = enc.intento or enc.conductor or "supervisor"
    pedido = juicio.inspeccion
    if pedido is not None and pedido.operacion in capacidades.operaciones_de(
        recursos, pedido.recurso
    ):
        crudo = {
            "id": ident,
            "encargo": enc.id,
            "operacion": pedido.operacion,
            "recurso": pedido.recurso,
            "fase": enc.fase_activa,
            "intento": intento,
            "generacion": enc.generacion,
            "conductor": enc.conductor,
            "motivo": pregunta[:necesidades.MAX_MOTIVO],
            "parametros": dict(pedido.parametros),
        }
        try:
            necesidades.pedir_inspeccion(espacio, enc, crudo)
        except (OSError, ValueError):
            # Sin buzón no hay ejecución: la espera del propietario es la única
            # vía que queda y se registra abajo.
            pass
        else:
            # La liga nace con su solicitud: una liga sin solicitud no bloquea.
            # Si la autorización es del propietario, la solicitud queda visible y
            # admite respuesta, pero no detiene el trabajo: el revisor sigue con
            # lo que tenga y la repetición la corta el freno de no concluyentes.
            return lote.con(necesidades.pedida(enc, crudo, hora))
    return lote.con(_pregunta(enc, ident, juicio, pregunta, intento, hora)).con(Evento(
        "procedimiento",
        {
            "nota": (
                "consulta sin capacidad concedida, visible como solicitud "
                f"{ident}; el trabajo sigue sin esperar respuesta"
            ),
            "hora": hora,
        },
        f"consulta-sin-capacidad:{enc.id}:{ident}",
    ))
