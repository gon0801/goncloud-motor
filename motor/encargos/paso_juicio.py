"""El juicio del revisor independiente: se decide el lote entero antes de escribir.

`ciclo._cerrar_con_juicio` observa el plegado, las disputas y el pedido de
inspección, y guarda con `lote.guardar` lo que devuelve `decidir`: los
descartes, la revisión, la respuesta al veredicto e `incorporado`, en una
transacción. Después corre lo que `decidir` deja para el final: el cierre de
un aprobado o la liga de la inspección de un no concluyente. `decidir` no
escribe: lee el registro solo con el lote y git solo con `region`.

Las dos lecturas que la escritura cambiaba, el plegado de antes de la revisión
y las disputas pendientes, salen de la foto. Como el lote entra entero o nada,
el reintento tras un apagón las toma del mismo registro y decide lo mismo. Si
se corta el cierre, `incorporado` ya está guardado y el barrido lo termina por
`_revision_aprobada_vigente`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from .comprobacion import JuicioValidado
from .devolucion import devolver, sin_avance
from .dominio import (
    TOPE_NO_CONCLUYENTES,
    Encargo,
    Hallazgo,
    bloqueantes_abiertos,
    hubo_avance,
)
from .lote import Lote, Paso, Region, excepcion, incorporado
from .registro import Evento

# Lo que corre después de guardar el lote.
Siguiente = Literal["nada", "cerrar", "ligar_inspeccion"]


@dataclass(frozen=True)
class Foto:
    """Lo que el ciclo observó antes de decidir."""

    enc: Encargo
    juicio: JuicioValidado
    hora: str
    # Hay disputas con evidencia que ningún revisor independiente decidió todavía.
    disputa_previa: bool
    # Lo que el juicio pide al buzón y el estado del recurso que leería.
    pedido: dict


def _descartar(
    lote: Lote,
    enc: Encargo,
    juicio: JuicioValidado,
    hora: str,
    region: Callable[[str, Hallazgo], Region],
) -> tuple[Lote, tuple[Hallazgo, ...]]:
    """Los hallazgos de DeepSeek que el revisor descarta o deja fuera del alcance,
    con sus notas y excepciones, y la lista que guarda la revisión."""
    abiertos_ds = {h.id: h for h in bloqueantes_abiertos(enc, "deepseek")}
    hallazgos = tuple(juicio.hallazgos)
    ya_resueltos = {h.id for h in hallazgos if h.estado == "resuelto"}
    ids_ds = [
        hid for hid in juicio.resueltos if hid in abiertos_ds and hid not in ya_resueltos
    ]
    marca = juicio.evidencia or "descartado por el revisor independiente"
    for hid in ids_ds:
        h = replace(abiertos_ds[hid], estado="resuelto")
        hallazgos += (replace(h, cierre_evidencia=marca),)
        lote = lote.con(Evento(
            "nota_pr",
            {
                "texto": (
                    f"hallazgo de DeepSeek descartado por el revisor "
                    f"independiente: {h.id} «{h.titulo}» ({marca[:400]})"
                ),
                "hora": hora,
            },
            f"disputa-resuelta:{enc.id}:{h.id}",
        ))
        huella_region, texto_region, siguiente_region = region(enc.pr_sha, h)
        lote = lote.con(excepcion(
            "descarte", enc.pr_sha, h.id, marca, hora,
            f"excepcion:{enc.id}:descarte:{enc.pr_sha}:{h.id}",
            ancla=h.ancla,
            region=huella_region,
            region_ancla=texto_region,
            region_contexto=siguiente_region,
        ))
    estado = lote.estado
    for hid, razon in juicio.fuera_de_alcance:
        if hid not in abiertos_ds or hid in ya_resueltos or hid in ids_ds:
            continue
        h = abiertos_ds[hid]
        hallazgos += (
            replace(h, gravedad="observacion", estado="resuelto", cierre_evidencia=razon),
        )
        lote = lote.con(Evento(
            "hallazgo_fuera_de_alcance",
            {"id": hid, "titulo": h.titulo, "razon": razon, "hora": hora},
            f"fuera-alcance:{enc.id}:{estado.fase_activa}:{hid}",
        ))
        lote = lote.con(excepcion(
            "fuera_alcance", estado.pr_sha, hid, razon, hora,
            f"excepcion:{enc.id}:fuera:{estado.pr_sha}:{hid}",
        ))
    return lote, hallazgos


def _revision(foto: Foto, estado: Encargo, hallazgos: tuple[Hallazgo, ...]) -> Evento:
    enc, juicio = foto.enc, foto.juicio
    revisor = next((modelo for rol, _agente, modelo in reversed(enc.modelos) if rol == "revisor"), "")
    return Evento(
        "revision",
        {
            "revision_id": "rev_" + hashlib.sha256(
                f"{enc.id}:{enc.generacion}:{juicio.version}".encode()
            ).hexdigest()[:12],
            "version": juicio.version,
            "base_version": juicio.base_version,
            "alcance": juicio.alcance,
            "veredicto": juicio.veredicto,
            "revisor": revisor or estado.agente,
            "origen": "independiente",
            "cubre": list(juicio.cubre),
            "hallazgos": [h.como_dict() for h in hallazgos],
            "aprobadas": [list(x) for x in juicio.aprobadas],
            "evidencia": juicio.evidencia,
            "efectos_pendientes": list(juicio.efectos_pendientes),
            "inspeccion_faltante": juicio.inspeccion_faltante,
            "pedido": foto.pedido,
            "fase": estado.fase_activa,
            "intento": estado.intento,
            "hora": foto.hora,
        },
        f"revision-dato:{enc.id}:{estado.fase_activa}:{estado.generacion}:{juicio.version}",
    )


def _cambios(foto: Foto, lote: Lote, revisado: Encargo) -> Lote:
    """Un bloqueante cerrado devuelve el trabajo; ninguno sube la escalera."""
    enc, juicio, hora = foto.enc, foto.juicio, foto.hora
    abiertos = bloqueantes_abiertos(revisado, "independiente")
    motivo = abiertos[0].titulo if abiertos else (juicio.nota or "hay bloqueantes")
    if not hubo_avance(enc, revisado):
        version_previa = enc.revision_vigente.version if enc.revision_vigente else ""
        return sin_avance(
            lote, revisado,
            "; ".join(f"{h.id} {h.titulo}" for h in abiertos) or motivo,
            (
                "cambio archivos del producto"
                if juicio.version != version_previa
                else "no cambio el producto"
            ) + " y el revisor independiente sigue viendo el bloqueante abierto",
            hora, "cambios",
        )
    lote = lote.con(Evento(
        "procedimiento",
        {"nota": juicio.nota or motivo, "hora": hora},
        f"reparacion:{enc.id}:{revisado.generacion}",
    ))
    return devolver(
        lote, revisado, "investigar", motivo,
        "corregir " + ", ".join(h.id for h in abiertos)
        + "; lo aprobado que no cambio se conserva",
        hora, "cambios",
    )


def decidir(
    foto: Foto, lote: Lote, region: Callable[[str, Hallazgo], Region]
) -> Paso[Siguiente]:
    """El lote del juicio y lo que corre después de guardarlo."""
    enc, juicio, hora = foto.enc, foto.juicio, foto.hora
    lote, hallazgos = _descartar(lote, enc, juicio, hora, region)
    lote = lote.con(_revision(foto, lote.estado, hallazgos))
    revisado = lote.estado
    abiertos_ds = bloqueantes_abiertos(revisado, "deepseek")
    if juicio.veredicto == "aprobado" and foto.disputa_previa and abiertos_ds:
        lote = lote.con(Evento(
            "nota_pr",
            {
                "texto": (
                    "el revisor independiente no descartó la disputa; "
                    "el hallazgo vuelve al responsable: "
                    + "; ".join(f"{h.id} {h.titulo}" for h in abiertos_ds)
                ),
                "hora": hora,
            },
            f"disputa-rechazada:{enc.id}:{revisado.generacion}",
        ))
        lote = devolver(
            lote, revisado, "investigar", abiertos_ds[0].titulo,
            "corregir " + ", ".join(h.id for h in abiertos_ds)
            + "; la disputa con evidencia no fue aceptada por el revisor",
            hora, f"disputa-rechazada-{revisado.generacion}",
        )
        return lote.con(incorporado(revisado, hora)).paso("nada")
    if juicio.veredicto == "cambios":
        return _cambios(foto, lote, revisado).con(incorporado(revisado, hora)).paso("nada")
    if juicio.veredicto != "no_concluyente":
        return lote.con(incorporado(revisado, hora)).paso("cerrar")
    if revisado.no_concluyentes >= TOPE_NO_CONCLUYENTES:
        # El mismo pedido sin avance N veces seguidas: la escalera ya relevó
        # o dejó la entrega con pendientes. No se vuelve a ligar la misma
        # inspección ni se gasta otro turno con el mismo pedido.
        return lote.con(incorporado(revisado, hora)).paso("nada")
    if juicio.inspeccion_faltante:
        # La liga escribe por su cuenta: `incorporado` va después de
        # ella para que un corte entre las dos no deje el turno por hecho y la
        # inspección sin pedir.
        return devolver(
            lote, revisado, "inspeccionar",
            juicio.inspeccion_faltante,
            "obtener por el buzon la inspeccion faltante y continuar "
            "esta misma revision; el producto aprobado no se reabre",
            hora, "inspeccion", rol="revisor",
        ).paso("ligar_inspeccion")
    # Salida propia de las no_concluyente: el pedido del revisor vuelve al
    # responsable y la repetición la cuenta y la corta el freno
    # (`TOPE_NO_CONCLUYENTES`), no la escalera de los bloqueos.
    return devolver(
        lote, revisado, "investigar",
        juicio.nota or "evidencia insuficiente para aprobar",
        juicio.nota or "aportar la evidencia que falta sin rebajar el pedido",
        hora, "no-concluyente",
    ).con(incorporado(revisado, hora)).paso("nada")
