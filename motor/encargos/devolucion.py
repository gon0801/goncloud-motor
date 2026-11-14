"""Devolver el trabajo a un rol y la escalera de una vuelta sin avance.

Arman los eventos sobre una `Escritura`: el juicio del revisor les pasa su
`Lote` y los guarda en una transacción; los demás pasos les pasan `Directo`,
que escribe cada evento al momento. Las ukeys y los datos son los de los
registros ya guardados.
"""

from __future__ import annotations

import hashlib
from typing import TypeVar

from .dominio import (
    Encargo,
    _texto_normalizado,
    bloqueos_nuevos,
    huella_bloqueo,
    siguiente_escalon,
)
from .lote import Escritura
from .registro import Evento

E = TypeVar("E", bound=Escritura)


def corte_de_instrumento(
    estado: Encargo, causa: str, hora: str, serie: str, rol: str
) -> Evento | None:
    """La misma causa de instrumento sobre el mismo producto en dos intentos
    seguidos no vuelve al responsable.

    Deja un hecho visible y pasa el rol a otro modelo autorizado; si el relevo
    repite la misma causa, el relevo se agota y el encargo entrega con
    pendientes. No es un tope de rondas: exige clase y causa idénticas y el
    mismo producto. `estado` es lo que el registro guarda ahora."""
    version = estado.producto.firma if estado.producto else ""
    fallos = [f for f in estado.fallos_instrumento if f[0] == version]
    if len(fallos) < 3:
        return None
    ultimo, previo = fallos[-1], fallos[-2]
    if ultimo[1] != previo[1]:
        return None
    if _texto_normalizado(ultimo[2]) != _texto_normalizado(previo[2]):
        return None
    clase = ultimo[1]
    clave = hashlib.sha256(
        f"{clase}|{_texto_normalizado(causa)}|{version}".encode()
    ).hexdigest()[:16]
    diagnostico = (
        f"el instrumento {clase or 'de comprobación'} falló dos veces seguidas "
        f"con la misma causa sobre el mismo producto: {str(causa)[:400]}"
    )
    return Evento(
        "sin_progreso",
        {
            "huella": f"instrumento:{clave}",
            "diagnostico": diagnostico,
            "escalar": True,
            "rol": rol or "responsable",
            "hora": hora,
        },
        f"instrumento-corte:{estado.id}:{clave}:{estado.generacion}:{serie}",
    )


def devolver(
    escritura: E,
    enc: Encargo,
    estrategia: str,
    motivo: str,
    siguiente: str,
    hora: str,
    serie: str,
    rol: str = "responsable",
) -> E:
    if estrategia == "reparar_instrumento":
        corte = corte_de_instrumento(escritura.estado, motivo, hora, serie, rol)
        if corte is not None:
            return escritura.con(corte)
    return escritura.con(Evento(
        "estrategia",
        {
            "estrategia": estrategia,
            "rol": rol,
            "motivo": motivo,
            "siguiente": siguiente,
            "hora": hora,
        },
        f"devolucion:{enc.id}:{enc.generacion}:{serie}",
    ))


def sin_avance(
    escritura: E,
    enc: Encargo,
    abierto: str,
    intentado: str,
    hora: str,
    serie: str,
    rol: str = "responsable",
) -> E:
    """Escalera de una vuelta que no cerro ningun bloqueante: la primera vuelve
    a diagnosticar, la segunda seguida releva el mismo rol a otro modelo
    autorizado. Si el relevo tampoco avanza en el mismo punto, el encargo
    entrega con los pendientes abiertos. Si ya está aprobado pero la entrega
    sigue bloqueada, pide intervención y conserva el encargo abierto.
    `enc` ya incorpora el hallazgo de la vuelta.

    Una vuelta cuenta una sola vez: si otra fuente del mismo barrido ya la
    resolvio, esta no suma ni escala. Un bloqueante que el responsable no habia
    visto es informacion nueva: la vuelta vuelve a el para corregir y la escalera
    empieza de cero. No hay tope para esa serie: mientras cada vuelta traiga
    informacion nueva, el encargo sigue hasta cerrar los bloqueantes."""
    huella = huella_bloqueo(enc)
    base = f"sin-progreso:{enc.id}:{huella}:{enc.generacion}:{serie}"
    nuevos = sorted(bloqueos_nuevos(enc))
    if not nuevos and enc.generacion and enc.vuelta_contada == enc.generacion:
        return escritura
    if nuevos:
        escritura = escritura.con(Evento(
            "sin_progreso",
            {
                "huella": huella,
                "nueva": True,
                "nuevos": nuevos,
                "escalar": False,
                "rol": rol,
                "hora": hora,
            },
            base + ":nueva",
        ))
        return devolver(
            escritura, enc, "investigar", abierto,
            "corregir los bloqueantes nuevos de esta vuelta (" + ", ".join(nuevos)
            + ") junto con los que siguen abiertos; lo aprobado que no cambio se conserva",
            hora, f"nuevos-{serie}",
        )
    siguiente = siguiente_escalon(enc, rol)
    if siguiente != "diagnostico":
        escritura = escritura.con(Evento(
            "sin_progreso",
            {
                "huella": huella,
                "diagnostico": (
                    "el mismo bloqueo sigue tras el diagnostico, sin "
                    f"informacion nueva: {abierto}. Se intento: {intentado}"
                ),
                "escalar": True,
                "rol": rol,
                "hora": hora,
            },
            base + ":escalar",
        ))
        if siguiente == "relevo":
            escritura = devolver(
                escritura, enc, enc.estrategia or "investigar", abierto,
                "el mismo bloqueo sigue: sigue con otro modelo autorizado del "
                "mismo rol; si tampoco avanza, se entrega con los pendientes abiertos",
                hora, f"relevo-{serie}", rol=rol,
            )
        return escritura
    escritura = escritura.con(Evento(
        "sin_progreso",
        {"huella": huella, "escalar": False, "rol": rol, "hora": hora},
        base + ":diagnostico",
    ))
    return devolver(
        escritura, enc, "diagnosticar_bloqueo", abierto,
        "diagnosticar por que la correccion no resolvio el bloqueo; "
        "no repetir la misma accion",
        hora, f"diagnostico-{serie}",
    )
