"""Revision aprobada, recibos reales y nivel de entrega forman el cierre."""

from __future__ import annotations

from .comprobacion import digest, validar_juicio_legado
from .dominio import (
    Encargo, bloqueantes_abiertos, nivel_cumplido,
    revision_independiente_cubre_producto,
)


def _recibos(pedidos: list[dict], enc: Encargo) -> list[dict]:
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
    return recibos


def componer(enc: Encargo, pedidos: list[dict], evidencia: str) -> dict:
    """Cierre de una fase: revisión aprobada sobre la firma vigente,
    recibos de efectos y, si el nivel autorizado es pr o más, DeepSeek
    leído para el SHA actual y nivel alcanzado igual al autorizado."""
    ref = enc.revision_vigente
    if ref is None or ref.veredicto != "aprobado":
        raise ValueError("cierre sin revisión independiente aprobada")
    if not revision_independiente_cubre_producto(enc):
        raise ValueError("la revisión aprobada no es sobre la versión vigente")
    if bloqueantes_abiertos(enc):
        raise ValueError("cierre con bloqueantes abiertos")
    if enc.entrega.nivel != "terminado":
        if not enc.deepseek_sha or enc.deepseek_sha != enc.pr_sha:
            raise ValueError("falta leer la revisión de DeepSeek del SHA actual")
        if not nivel_cumplido(enc):
            raise ValueError("el nivel alcanzado no es el autorizado")
    recibos = _recibos(pedidos, enc)
    detalle = evidencia.strip()
    if recibos:
        detalle += " Recibos comprobados: " + "; ".join(
            f"{x['id']} {x['recibo']} {x['cuerpo']}" for x in recibos
        )
    if enc.entrega.nivel != "terminado":
        detalle += (
            f" Entrega {enc.nivel_alcanzado}: PR #{enc.pr_numero} "
            f"en {enc.entrega.repo}, SHA {enc.pr_sha}, DeepSeek leído."
        )
    return {
        "clase": "cierre_comprobado",
        "fase": enc.fase_activa,
        "firma": enc.producto.firma,
        "revision": ref.revision_id,
        "nivel_alcanzado": enc.nivel_alcanzado
        if enc.nivel_alcanzado != "ninguno"
        else "terminado",
        "efectos": recibos,
        "evidencia": detalle,
    }


def componer_legado(
    juicio: dict, observacion: dict, pedidos: list[dict], enc: Encargo
) -> dict:
    validar_juicio_legado(juicio, observacion, digest(observacion))
    recibos = _recibos(pedidos, enc)
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
