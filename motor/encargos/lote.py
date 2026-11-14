"""El lote de un paso del barrido: se decide entero y se guarda en una transacción.

Un paso observa, arma un `Lote` con funciones puras y lo guarda con `guardar`,
que hace un solo `Registro.append_many`, así que un apagón deja el lote entero
o nada. `Lote.con` decide con `Registro.proyectar`, la misma cuenta que hace la
escritura. Un cambio de otro escritor después de la proyección solo rechaza el
lote si ocupa con otro hecho una de sus ukeys o vuelve ilegal uno de sus
eventos; si no choca con el lote, se acepta.

Los pasos que escriben evento por evento pasan `Directo` a los mismos
constructores, así que cada evento tiene una sola forma. Sus ukeys y datos son
los de los registros ya guardados: un registro viejo a medias da `igual` donde
ya tiene un evento y pliega igual. Cambiar uno rompe esa igualdad.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from functools import partial
from typing import Generic, Protocol, Self, TypeVar

from .dominio import Encargo
from .registro import (
    ClaveOcupada,
    Evento,
    EventoIlegal,
    Rechazo,
    Registro,
    _campos_distintos,
    contar_omitidos,
)

R = TypeVar("R")

# (huella, línea anclada, lo que la sigue) de la región que un hallazgo ancla en un SHA.
Region = tuple[str, str, list[str] | None]


@dataclass(frozen=True)
class Paso(Generic[R]):
    """Lo que el paso guarda en una transacción, lo que deja fuera y lo que responde.

    `omitidos` son los eventos que el registro rechazaría. El paso sigue sin
    ellos, como sigue un llamador de `anotar`, que devuelve el rechazo sin
    lanzar: meterlos al lote rechazaría el paso entero."""

    eventos: tuple[Evento, ...]
    omitidos: tuple[tuple[Evento, Rechazo], ...]
    respuesta: R


@dataclass(frozen=True)
class Lote:
    """Los eventos del paso y el estado que dejan si se guardan ahora.

    `con` responde lo que respondería `Registro.anotar` después de guardar los
    anteriores: la ukey que el lote ya lleva con el mismo hecho no cambia nada,
    y la que lleva otro hecho, o el evento que el reductor rechaza, queda
    fuera."""

    proyectar: Callable[[Sequence[Evento]], Encargo]
    estado: Encargo
    eventos: tuple[Evento, ...] = ()
    omitidos: tuple[tuple[Evento, Rechazo], ...] = ()

    def con(self, evento: Evento) -> Lote:
        previo = next(
            (i for i, e in enumerate(self.eventos) if e.ukey == evento.ukey), None
        )
        if previo is not None:
            guardado = self.eventos[previo]
            # `_cotejar` compara lo guardado, que ya pasó por JSON, con lo nuevo.
            campos = _campos_distintos(
                ("", guardado.kind, json.loads(json.dumps(dict(guardado.data)))),
                ("", evento.kind, evento.data),
            )
            if not campos:
                return self
            ocupada = ClaveOcupada(previo, evento.ukey, campos)
            return replace(self, omitidos=(*self.omitidos, (evento, ocupada)))
        try:
            estado = self.proyectar([*self.eventos, evento])
        except Rechazo as rechazo:
            return replace(self, omitidos=(*self.omitidos, (evento, rechazo)))
        return replace(self, estado=estado, eventos=(*self.eventos, evento))

    def paso(self, respuesta: R) -> Paso[R]:
        return Paso(self.eventos, self.omitidos, respuesta)

    def seguir(self, paso: Paso) -> Lote:
        """Este lote con lo que ya decidió `paso`, para que el mismo paso agregue más.

        Cada evento entra por `con`: el que otro escritor volvió inaceptable
        después de decidir queda en `omitidos`, como en cualquier otro paso."""
        lote = replace(self, eventos=(), omitidos=paso.omitidos)
        for evento in paso.eventos:
            lote = lote.con(evento)
        return lote


def sobre(registro: Registro, enc: Encargo) -> Lote:
    """Un lote vacío sobre lo que el registro guarda ahora del encargo."""
    return Lote(partial(registro.proyectar, enc.id), registro.fold(enc.id) or enc)


def guardar(registro: Registro, encargo: str, paso: Paso) -> str:
    """Guarda el lote del paso en una transacción; devuelve "" o el motivo del rechazo.

    Quien escribe sin tomar `turno` puede cambiar el registro después de la
    última proyección. `append_many` rechaza entonces el lote entero, sin
    guardar nada, solo si ese cambio ocupa con otro hecho una ukey del lote o
    vuelve ilegal uno de sus eventos. Un cambio que no choca se acepta."""
    contar_omitidos(paso.omitidos)
    try:
        registro.append_many(encargo, paso.eventos)
    except (ClaveOcupada, EventoIlegal) as rechazo:
        evento = paso.eventos[rechazo.indice]
        return f"el registro cambió antes de guardar {evento.kind} {evento.ukey}: {rechazo}"
    return ""


def guardar_incorporado(registro: Registro, enc: Encargo, lote: Lote, hora: str) -> Encargo:
    """Guarda el lote de una salida del paso con su `incorporado` al final y
    devuelve lo que el registro guarda ahora. Si el registro rechaza el lote,
    el intento queda sin `incorporado` y la vuelta siguiente lo decide otra vez."""
    guardar(registro, enc.id, lote.con(incorporado(enc, hora)).paso(None))
    return registro.fold(enc.id) or enc


class Directo:
    """Escribe cada evento al momento con `Registro.anotar`, que calla y cuenta
    el rechazo. `estado` es lo que el registro guarda ahora."""

    def __init__(self, registro: Registro, enc: Encargo) -> None:
        self._registro = registro
        self._enc = enc

    @property
    def estado(self) -> Encargo:
        return self._registro.fold(self._enc.id) or self._enc

    def con(self, evento: Evento) -> Directo:
        self._registro.anotar(
            self._enc.id, {"kind": evento.kind, "data": dict(evento.data)}, evento.ukey
        )
        return self


class Escritura(Protocol):
    """Donde un constructor deja sus eventos: un `Lote` o `Directo`."""

    @property
    def estado(self) -> Encargo: ...

    def con(self, evento: Evento) -> Self: ...


def incorporado(enc: Encargo, hora: str) -> Evento:
    """El resultado de este intento ya se incorporó. La generación identifica
    el intento y la marca vive en el registro, no en su directorio."""
    return Evento(
        "incorporado",
        {"generacion": enc.generacion, "hora": hora},
        f"incorporado:{enc.id}:{enc.generacion}",
    )


def ya_incorporado(registro: Registro, enc: Encargo) -> bool:
    """El registro ya guarda un hecho con la ukey de `incorporado` de este intento.

    Casi siempre es el propio `incorporado`. Si es otro hecho, ningún lote
    puede guardar la marca: se cuenta por stderr como `ajena` y se toma por
    puesta, para que el barrido cierre el intento en vez de decidirlo en cada
    vuelta."""
    marca = incorporado(enc, "")
    guardado = registro.por_ukey(marca.ukey)
    if guardado is None:
        return False
    campos = _campos_distintos(
        ("", guardado["kind"], guardado["data"]), ("", marca.kind, marca.data)
    )
    if campos:
        contar_omitidos(((marca, ClaveOcupada(0, marca.ukey, campos)),))
    return True


def excepcion(
    tipo: str, sha: str, hallazgo: str, motivo: str, hora: str, clave: str,
    ancla: str = "", region: str = "", region_ancla: str = "",
    region_contexto: list[str] | None = None,
) -> Evento:
    """La excepción del check de merge, estructurada y con su SHA.

    El check solo acepta esta fuente; el texto del PR no autoriza nada. El
    ancla, la huella de región, la línea anclada y lo que la sigue son
    opcionales: los descartes del revisor los llevan para que el motor pueda
    comprobar en SHAs posteriores si el código que el hallazgo señala sigue
    en el archivo, sin depender de la posición de la línea.
    """
    datos: dict[str, object] = {
        "tipo": tipo,
        "sha": str(sha or ""),
        "id": str(hallazgo or ""),
        "motivo": " ".join(str(motivo or "").split()),
        "hora": hora,
    }
    if ancla:
        datos["ancla"] = str(ancla)
    if region:
        datos["region"] = str(region)
    if region_ancla:
        datos["region_ancla"] = str(region_ancla)
    if region_contexto is not None:
        datos["region_contexto"] = [str(x) for x in region_contexto]
    return Evento("excepcion", datos, clave)
