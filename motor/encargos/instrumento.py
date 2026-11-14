"""El fallo de un instrumento y el impedimento que deja, armados sobre una `Escritura`.

Las salidas del paso del revisor les pasan un `Lote` y lo guardan con su
`incorporado` en una transacción; los demás pasos les pasan `Directo`, que
escribe cada evento al momento. Las ukeys y los datos son los de los registros
ya guardados.
"""

from __future__ import annotations

from typing import TypeVar

from .devolucion import corte_de_instrumento
from .dominio import Encargo
from .lote import Escritura
from .registro import Evento

E = TypeVar("E", bound=Escritura)

_VERIFICABLES = (
    "entorno",
    "comprobaciones.json",
    "fases.json",
    "pedido-efecto.json",
    "disputa.json",
)


def es_verificable(clase: str) -> bool:
    # El juicio también: su archivo se puede volver a validar contra la versión
    # vigente sin gastar otro turno del revisor.
    return clase in _VERIFICABLES or clase.startswith(("juicio:", "inspeccion:"))


def apariciones(enc: Encargo, clase: str) -> int:
    """Cuántas veces apareció esta causa. Sube al registrarla y al resolverla,
    así una retransmisión no crea un evento y una recaída sí."""
    return dict(enc.apariciones).get(clase, 0)


def fallo(
    enc: Encargo,
    fuente: str,
    causa: str,
    siguiente: str,
    hora: str,
    serie: str,
    clase: str = "",
    artefacto: str = "",
) -> Evento:
    """El fallo con la operación que lo produjo. `clase` es la identidad
    estable: no cambia porque el mensaje cambie de redacción. `artefacto` es el
    archivo a revalidar para acreditar la reparación, cuando no basta con una
    ruta canónica del espacio."""
    return Evento(
        "instrumento_fallo",
        {
            "fuente": fuente,
            "causa": causa,
            "clase": clase,
            "artefacto": artefacto,
            "version": enc.producto.firma if enc.producto else "",
            "siguiente": siguiente,
            "hora": hora,
        },
        f"instrumento:{enc.id}:{fuente}:{serie}",
    )


def impedimento(
    estado: Encargo, clase: str, causa: str, condicion: str, hora: str, agente: str = ""
) -> Evento | None:
    """El impedimento, o None si `estado` ya lo tiene. `agente` es el que se
    iba a lanzar si la causa depende de él (su shell)."""
    if any(
        i.clase == clase and i.causa == causa and i.condicion == condicion
        and i.agente == agente
        for i in estado.impedimentos
    ):
        return None
    return Evento(
        "impedimento",
        {
            "clase": clase,
            "causa": causa,
            "condicion": condicion,
            "verificable": es_verificable(clase),
            "agente": agente,
            "hora": hora,
        },
        f"impedimento:{estado.id}:{clase}:{apariciones(estado, clase) + 1}",
    )


def entorno(escritura: E, sonda, hora: str) -> E | None:
    """El entorno manda sobre el trabajador: si no deja trabajar, el fallo de
    instrumento no es una corrección que el trabajador pueda hacer. None si la
    sonda pasó. El impedimento nombra al agente del encargo: su shell puede ser
    la causa."""
    if sonda.ok:
        return None
    estado = escritura.estado
    evento = impedimento(
        estado, "entorno", sonda.causa, sonda.condicion, hora, estado.agente or ""
    )
    return escritura if evento is None else escritura.con(evento)


def impedimento_de_instrumento(
    escritura: E, clase: str, causa: str, hora: str
) -> tuple[E, bool]:
    """El mismo instrumento con el mismo defecto no es información nueva.

    Se mide por la clase del defecto contra el registro de fallos, no por el
    texto: un identificador de intento dentro del mensaje no reinicia la cuenta.
    Una clase distinta sí es un intento de reparación y se deja pasar.

    A la tercera falla igual, la herramienta se marca como no disponible para
    este encargo: deja de bloquear y el trabajo sigue con la evidencia que hay,
    también cuando la que falla es el juicio o la inspección: ninguna caída
    deja al encargo esperando a una persona. Responde True
    cuando el caso queda manejado (la tercera falla ya no gasta un turno de
    reparación) y False cuando el llamador debe devolver el trabajo para
    reparar el instrumento."""
    estado = escritura.estado
    corte = corte_de_instrumento(
        estado, causa, hora, f"impedimento-{clase}", str(estado.rol or "responsable")
    )
    if corte is not None:
        escritura = escritura.con(corte)
    if sum(1 for f in estado.fallos_instrumento if f[1] == clase) < 2:
        return escritura, False
    escritura = escritura.con(Evento(
        "instrumento_no_disponible",
        {"clase": clase, "causa": causa, "hora": hora},
        f"instrumento-no-disponible:{estado.id}:{clase}",
    )).con(Evento(
        "nota_pr",
        {
            "texto": f"herramienta no disponible para este encargo: {clase} ({str(causa)[:200]})",
            "hora": hora,
        },
        f"nota-herramienta:{estado.id}:{clase}",
    ))
    if clase.startswith(("juicio:", "inspeccion:")):
        escritura = escritura.con(Evento(
            "pendiente_entrega",
            {
                "texto": (
                    f"no se pudo obtener {clase} tras tres fallos iguales: "
                    f"{str(causa)[:400]}"
                ),
                "hora": hora,
            },
            f"pendiente-instrumento:{estado.id}:{clase}",
        ))
    return escritura, True


def fallo_juicio(
    escritura: E,
    enc: Encargo,
    clase: str,
    causa: str,
    siguiente: str,
    artefacto: str,
    hora: str,
    serie: str,
) -> E:
    """Único camino para registrar que el juicio no se pudo usar.

    Concentra las dos decisiones que esa condición arrastra: la identidad del
    defecto y el control de repetición. Una rama que anota y devuelve el control
    al barrido sin pasar por aquí vuelve a lanzar al revisor en cada vuelta, y
    eso ya pasó con la referencia obsoleta."""
    escritura = escritura.con(
        fallo(enc, "juicio", causa, siguiente, hora, serie, clase=clase, artefacto=artefacto)
    )
    return impedimento_de_instrumento(escritura, clase, causa, hora)[0]
