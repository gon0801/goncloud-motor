"""La elección explícita del usuario acompaña al pedido, no al servicio."""

from __future__ import annotations

from .catalogo_agentes import agente_del_modelo, identidad_modelo, modelo_canonico


def solicitar_modelos(
    responsable: str = "", revisor: str = ""
) -> tuple[tuple[str, str, str], ...]:
    salida = []
    for rol, referencia in (("responsable", responsable), ("revisor", revisor)):
        if not isinstance(referencia, str):
            raise TypeError(f"el modelo de {rol} debe ser proveedor/modelo")
        if not referencia:
            continue
        referencia = modelo_canonico(referencia)
        _proveedor, separador, modelo = referencia.partition("/")
        agente = agente_del_modelo(referencia)
        if not agente or not separador or not modelo.strip():
            raise ValueError(f"modelo de {rol} desconocido: usa proveedor/modelo")
        salida.append((rol, agente, referencia))
    if len(salida) == 2 and identidad_modelo(salida[0][2]) == identidad_modelo(
        salida[1][2]
    ):
        raise ValueError("el revisor debe usar un modelo distinto del responsable")
    return tuple(salida)
