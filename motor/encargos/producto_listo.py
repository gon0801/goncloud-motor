"""El producto que ve un intento: un solo constructor y una sola anotación por ukey.

`producto:{encargo}:{fase}:{generacion}:{firma}` es el producto de esa firma en
ese intento, y lo anota la primera pasada que lo ve. Otra pasada del mismo
intento, después de la comprobación declarada o de un corte, mira un árbol que
los comandos de la comprobación pudieron tocar (`.interno`, lo que git ve): su
lectura ya no es la entrega y no reescribe la ukey. Un producto distinto trae
otra firma y es otro hecho.

Las ukeys y los datos son los de los registros ya guardados.
"""

from __future__ import annotations

from dataclasses import replace

from .dominio import Encargo, VersionProducto
from .lote import Directo
from .registro import Evento, Registro


def visto(enc: Encargo, version: VersionProducto, hora: str) -> Evento:
    return Evento(
        "producto_listo",
        {
            "version": version.firma,
            "manifiesto": [list(x) for x in version.manifiesto],
            "fase": enc.fase_activa,
            "intento": enc.intento,
            "hora": hora,
        },
        f"producto:{enc.id}:{enc.fase_activa}:{enc.generacion}:{version.firma}",
    )


def entregado(
    enc: Encargo,
    version: VersionProducto,
    sha_git: str,
    declara_comprobaciones: bool,
    interno: str,
    hora: str,
) -> Evento:
    """El producto con lo que el responsable entregó fuera de la firma: el commit
    cuando es el ya entregado, si declaró comprobaciones y la huella de `.interno`."""
    evento = visto(enc, version, hora)
    return replace(
        evento,
        data={
            **evento.data,
            "sha_git": sha_git,
            "declara_comprobaciones": declara_comprobaciones,
            "interno": interno,
        },
    )


def anotar(registro: Registro, enc: Encargo, evento: Evento) -> Encargo:
    """Anota el producto si su ukey está libre y devuelve lo que el registro guarda ahora."""
    if registro.por_ukey(evento.ukey) is None:
        Directo(registro, enc).con(evento)
    return registro.fold(enc.id) or enc
