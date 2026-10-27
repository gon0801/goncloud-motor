"""Aprobacion del producto y recibos reales forman un cierre distinto del juicio."""

from __future__ import annotations

from .comprobacion import digest, validar_juicio
from .dominio import Encargo


def componer(
    juicio: dict, observacion: dict, pedidos: list[dict], enc: Encargo
) -> dict:
    validar_juicio(juicio, observacion, digest(observacion))
    recibos = []
    for pedido in pedidos:
        efecto = next((e for e in enc.efectos if e.id == pedido["id"]), None)
        if (
            efecto is None
            or efecto.estado != "aplicado"
            or not efecto.recibo
            or efecto.operacion != pedido["operacion"]
            or efecto.clave != pedido["clave"]
            or efecto.cuerpo != pedido["cuerpo"]
            or not efecto.destino
        ):
            raise ValueError(
                "falta recibo real de la identidad completa del efecto " + pedido["id"]
            )
        recibos.append(
            {
                "id": efecto.id,
                "destino": efecto.destino,
                "operacion": efecto.operacion,
                "clave": efecto.clave,
                "cuerpo": efecto.cuerpo,
                "recibo": efecto.recibo,
                "estado": efecto.estado,
            }
        )
    if juicio["clase"] == "producto_aprobado" and not recibos:
        raise ValueError("producto aprobado sin efectos comprobables")
    return {
        "clase": "cierre_comprobado",
        "firma": digest(observacion),
        "aprobacion": {k: v for k, v in juicio.items() if k != "firma"},
        "efectos": recibos,
        "evidencia": juicio["evidencia"]
        + " Recibos comprobados: "
        + "; ".join(f"{x['id']} {x['recibo']} {x['cuerpo']}" for x in recibos),
    }


def comprobar_destino(cierre: dict, destino) -> None:
    for efecto in cierre["efectos"]:
        actual = destino.consultar(efecto["clave"])
        if efecto["destino"] != str(destino.raiz.resolve()) or any(
            actual.get(k) != efecto[k]
            for k in ("estado", "recibo", "destino", "operacion", "clave", "cuerpo")
        ):
            raise ValueError(
                "el destino no confirma el recibo canonico de " + efecto["id"]
            )
