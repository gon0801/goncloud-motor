"""La elección explícita del usuario acompaña al pedido, no al servicio."""

from __future__ import annotations

_AGENTE_POR_PROVEEDOR = {
    "openai": "codex",
    "xai": "grok",
    "meta": "muse",
    "kimi-code": "kimi",
}


def solicitar_modelos(
    responsable: str = "", revisor: str = ""
) -> tuple[tuple[str, str, str], ...]:
    salida = []
    for rol, referencia in (("responsable", responsable), ("revisor", revisor)):
        if not isinstance(referencia, str):
            raise TypeError(f"el modelo de {rol} debe ser proveedor/modelo")
        if not referencia:
            continue
        proveedor, separador, modelo = referencia.partition("/")
        if (
            proveedor not in _AGENTE_POR_PROVEEDOR
            or not separador
            or not modelo.strip()
        ):
            raise ValueError(f"modelo de {rol} desconocido: usa proveedor/modelo")
        salida.append((rol, _AGENTE_POR_PROVEEDOR[proveedor], referencia))
    if len(salida) == 2 and salida[0][2] == salida[1][2]:
        raise ValueError("el revisor debe usar un modelo distinto del responsable")
    return tuple(salida)
