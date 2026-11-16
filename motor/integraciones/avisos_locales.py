"""Correcciones de avisos que aun no salieron del canal local."""

from __future__ import annotations

from pathlib import Path

from motor.io import escribir_texto, leer_texto


def actualizar_palabra_pendiente(
    guardado: dict, aviso: dict, cuerpo: str, entrega: Path,
) -> None:
    if (guardado.get("estado") != "local-pendiente"
        or guardado.get("alcance") != "local"
        or guardado.get("intentos")
        or guardado.get("palabra") == aviso.get("palabra")):
        return
    if entrega.exists():
        temporal = entrega.with_suffix(".tmp")
        escribir_texto(temporal, cuerpo)
        temporal.replace(entrega)
    guardado["palabra"] = aviso.get("palabra")


def conciliar_cuerpo_local(entrega: Path, cuerpo: str) -> bool:
    if not entrega.exists():
        entrega.parent.mkdir(parents=True, exist_ok=True)
        temporal = entrega.with_suffix(".tmp")
        escribir_texto(temporal, cuerpo)
        temporal.replace(entrega)
    return leer_texto(entrega) == cuerpo
