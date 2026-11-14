"""Juzga un encargo ya terminado contra los puntos de David y lo esperado.

Uso, siempre en solo lectura:

    python3 ops/canario_encargos.py <encargo> [--base ~/.motor-encargos]
        [--esperado ops/canario_encargos.esperado.json] [--arbol <ruta>]
        [--sha-desplegado <sha>] [--archivo-desplegado <ruta> --marca <texto>]
        [--detencion <encargo> [--archivo-detencion <ruta>]]

`--detencion` nombra el encargo cuyo checklist de despliegue falla a propósito:
de él sale la fila "se detiene". `--archivo-detencion` es el archivo que su
despliegue deja y su rollback quita; su marca es el id de ese encargo.

Tres capas separadas:

- `observar` lee el registro (abierto con `mode=ro`), GitHub (`gh pr view` y
  `git ls-remote`), el disco de la base, el clon del servicio y los procesos.
  Juzga cada fase y cada proceso con las funciones del motor. Devuelve una
  `Observacion`.
- `juzgar` es pura: de la observación y lo esperado saca una `Fila` por cada
  entrada de `TABLA`, con `pasa`, `falla` o `no medido`.
- El archivo de lo esperado dice qué resultado se espera en cada fila y los
  topes. Una fila que pasaba y falla es regresión; una que empieza a pasar
  obliga a actualizar el archivo.

Sale con 0 solo si cada fila da lo esperado, con 1 si alguna no, y con 2 si no
puede juzgar: el encargo no está en el registro o el archivo de lo esperado no
tiene la forma.

La salida trae ids, ramas y números de PR: no va a un log público.
"""

from __future__ import annotations

import argparse
import dataclasses
import io
import json
import os
import sqlite3
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Protocol, cast, get_args

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from motor.encargos import sesion  # noqa: E402
from motor.encargos.despliegue import comprobar_recibo  # noqa: E402
from motor.encargos.dominio import (  # noqa: E402
    Encargo,
    Fase,
    con_fase,
    nivel_cumplido,
    nivel_de,
    nivel_objetivo,
)
from motor.encargos.politica import CERRADOS_VENTANA_S  # noqa: E402
from motor.github import GhGitHub, PrView  # noqa: E402
from motor.io import leer_json, leer_texto  # noqa: E402
from motor.repositorios import resolver_repositorio  # noqa: E402
from tools import plegado  # noqa: E402

ESPERADO = RAIZ / "ops" / "canario_encargos.esperado.json"
ESPERA_GIT_S = 30

Resultado = Literal["pasa", "falla", "no medido"]
NombreFila = Literal[
    "termina", "entregado", "limpio", "no pierde", "no repite",
    "cuesta poco", "se detiene", "versión", "legible",
]

# --------------------------------------------------------------------------- observación


@dataclass(frozen=True)
class PR:
    """El PR de una fase según GitHub. `numero` 0: la fase no abrió PR."""

    fase: str
    numero: int
    estado: str | None
    """abierto, mergeado o cerrado; None si GitHub no respondió."""
    base: str = ""
    merge_commit: str = ""


@dataclass(frozen=True)
class Recibo:
    """El recibo de despliegue de una fase, leído con `despliegue.comprobar_recibo`,
    la misma comprobación que pide el cierre."""

    fase: str
    estado: str
    """`aplicado` si el motor lo acepta; si no, el motivo que da para rechazarlo."""
    sha: str


TipoResto = Literal[
    "rama", "pr", "espacio", "ejecucion", "runtime", "despliegue", "referencia", "proceso",
]


@dataclass(frozen=True)
class Resto:
    """Algo que el encargo creó y su regla de limpieza manda borrar."""

    tipo: TipoResto
    nombre: str
    queda: bool | None
    """None: no se pudo mirar."""


@dataclass(frozen=True)
class FaseVista:
    """Una fase según el reductor, juzgada con las reglas del motor."""

    id: str
    estado: str
    nivel: str
    """`dominio.nivel_de` de la fase."""
    cumplido: bool
    """`dominio.nivel_cumplido` con esta fase como activa."""
    desplegable: bool
    """El motor exige desplegarla: `nivel_cumplido` no la da por cumplida en integrado."""
    causa: str = ""
    """Por qué quedó bloqueada."""


@dataclass(frozen=True)
class Pliegue:
    """Lo que el registro dice del encargo, plegado con el reductor del árbol."""

    estado: str
    nivel_autorizado: str
    nivel_efectivo: str
    """`dominio.nivel_objetivo`: el autorizado o el que el motor dejó al bajarlo."""
    motivo_efectivo: str
    nivel_alcanzado: str
    """El que reporta el motor: el de la fase activa."""
    fase_activa: str
    base: str
    recibido: str
    cierre: str
    """Vacío mientras el encargo no cierre."""
    completo: bool
    """`Encargo.objetivo_completo()`: todas las fases preparadas."""
    fases: tuple[FaseVista, ...]
    turnos: tuple[tuple[str, int], ...]
    rondas: int
    """Revisiones independientes, contadas como las cuenta el reductor."""
    violaciones: tuple[str, ...]
    """Invariantes de `tools/plegado.py` que no se sostienen."""
    ordenes: tuple[str, ...] = ()
    """La acción de cada orden que recibió (parar, cancelar, continuar...), en orden."""
    causas: tuple[str, ...] = ()
    """Lo que el encargo deja escrito como causa: su motivo y la de cada impedimento."""


@dataclass(frozen=True)
class NoPliega:
    evento: int
    tipo: str
    excepcion: str


@dataclass(frozen=True)
class Version:
    """Vacío: no hay de dónde leerlo."""

    desplegado: str
    arbol: str
    pasada: str


Contenido = Literal["con la marca", "sin la marca", "no existe", "ilegible"]


@dataclass(frozen=True)
class ArchivoDesplegado:
    ruta: str
    marca: str
    contenido: Contenido


@dataclass(frozen=True)
class Despliegue:
    """Un recibo de despliegue del encargo tal como lo dejó el trabajador, aprobado o no."""

    fase: str
    estado: str
    pasos: tuple[tuple[str, int], ...]
    """Cada paso que el trabajador lanzó, con su salida."""


@dataclass(frozen=True)
class Detencion:
    """El encargo cuyo checklist de despliegue falla a propósito."""

    encargo: str
    pliegue: Pliegue | NoPliega | None
    """None: el encargo no está en el registro."""
    despliegues: tuple[Despliegue, ...] | None = ()
    """None: no se pudieron leer sus recibos."""
    archivo: ArchivoDesplegado | None = None
    """Lo que su despliegue deja y su rollback quita; None: nadie pidió revisarlo."""


@dataclass(frozen=True)
class Observacion:
    hora: str
    pliegue: Pliegue | NoPliega | None
    """None: el encargo no está en el registro."""
    prs: tuple[PR, ...] = ()
    recibos: tuple[Recibo, ...] = ()
    restos: tuple[Resto, ...] = ()
    version: Version = Version("", "", "")
    actualizado: str = ""
    """Hora de la última actualización del servicio (instalacion.json); vacío si no se sabe."""
    archivo: ArchivoDesplegado | None = None
    """None: nadie pidió revisar un archivo desplegado."""
    detencion: Detencion | None = None
    """None: esta corrida no hizo el encargo que falla a propósito."""


# --------------------------------------------------------------------------- esperado


@dataclass(frozen=True)
class Topes:
    minutos_termina: int
    minutos: int
    turnos_por_rol: int
    rondas: int
    minutos_se_detiene: int
    """Lo que puede tardar en pararse el encargo que falla a propósito."""


@dataclass(frozen=True)
class Esperado:
    filas: Mapping[NombreFila, Resultado]
    topes: Topes


@dataclass(frozen=True)
class Fila:
    nombre: NombreFila
    punto: str
    resultado: Resultado
    esperado: Resultado
    observado: str
    fuente: str

    @property
    def discrepancia(self) -> str:
        return DISCREPANCIAS.get((self.esperado, self.resultado), "")


DISCREPANCIAS: dict[tuple[Resultado, Resultado], str] = {
    ("pasa", "falla"): "regresión",
    ("pasa", "no medido"): "dejó de medirse",
    ("falla", "no medido"): "dejó de medirse",
    ("falla", "pasa"): "mejora: actualiza el esperado",
    ("no medido", "pasa"): "ya se mide: actualiza el esperado",
    ("no medido", "falla"): "ya se mide: actualiza el esperado",
}

# --------------------------------------------------------------------------- juicio

Juicio = tuple[Resultado, str]

Plazo = int | Literal["actualización"]

# La tabla "Limpieza" de docs/modelo-centro-encargos.md, entrada por entrada,
# más los procesos que pide el diseño del canario. Lo que la regla conserva
# (el recibo del despliegue, lo que tiene plazo sin vencer) no es resto.
LIMPIEZA: tuple[tuple[TipoResto, str, Plazo], ...] = (
    ("rama", "rama remota encargo/*, se borra al mergear o cerrar el PR", 0),
    ("pr", "PR de un encargo cancelado, se cierra al cancelar", 0),
    ("espacio", "espacio/<id>, se borra 7 días después del estado final", 7 * 24 * 60 * 60),
    ("ejecucion", "ejecucion/<id>, se borra al cerrar la ventana de sondeo", CERRADOS_VENTANA_S),
    ("runtime", "credenciales del turno en runtime/<id>, se borran al terminar cada turno", 0),
    ("despliegue", "despliegues/<clave>, se conserva el recibo y se borra lo demás", 0),
    ("referencia", "referencias a ramas remotas en el clon del servicio,"
     " fetch --prune en cada actualización", "actualización"),
    ("proceso", "procesos de sus turnos y despliegues, no quedan al cerrar", 0),
)


def _hora(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _duracion(desde: str, hasta: str) -> timedelta:
    return _hora(hasta) - _hora(desde)


def _texto_duracion(d: timedelta) -> str:
    minutos, segundos = divmod(int(d.total_seconds()), 60)
    return f"{minutos} min {segundos} s" if segundos else f"{minutos} min"


def _sin_pliegue(obs: Observacion) -> Juicio | None:
    if obs.pliegue is None:
        return "no medido", "el encargo no está en el registro"
    if isinstance(obs.pliegue, NoPliega):
        return "no medido", "el registro no se puede plegar (ver la fila legible)"
    return None


def _bajado(p: Pliegue) -> str:
    if p.nivel_efectivo == p.nivel_autorizado:
        return ""
    motivo = f": {p.motivo_efectivo}" if p.motivo_efectivo else ""
    return f"el motor bajó el objetivo de {p.nivel_autorizado} a {p.nivel_efectivo}{motivo}"


def _termina(obs: Observacion, topes: Topes) -> Juicio:
    if (fuera := _sin_pliegue(obs)) is not None:
        return fuera
    p = cast(Pliegue, obs.pliegue)
    if p.estado != "terminado":
        return "falla", f"el encargo quedó {p.estado}, no terminado"
    if not p.fases:
        return "falla", "el encargo terminó sin fases"
    if not p.completo:
        faltan = [
            f"la fase {f.id} quedó {f.estado}" + (f" ({f.causa})" if f.causa else "")
            for f in p.fases if f.estado != "preparada"
        ]
        return "falla", "entrega parcial: " + "; ".join(faltan)
    debajo = [
        f"la fase {f.id} quedó en nivel {f.nivel}, por debajo de lo que el motor exige"
        for f in p.fases if not f.cumplido
    ]
    if debajo:
        return "falla", "; ".join(debajo)
    duracion = _duracion(p.recibido, p.cierre or obs.hora)
    tarde = duracion > timedelta(minutes=topes.minutos_termina)
    niveles = ", ".join(
        f"{f.id} {f.nivel}" + ("" if f.desplegable else " (no requiere despliegue)")
        for f in p.fases
    )
    partes = [
        f"terminó solo sus {len(p.fases)} fases, cada una en el nivel que el motor exige"
        f" ({niveles})",
        f"{'pero ' if tarde else ''}tardó {_texto_duracion(duracion)}"
        f" y el tope es {topes.minutos_termina} min",
    ]
    if bajado := _bajado(p):
        partes.append(bajado)
    if any(f.nivel != p.nivel_alcanzado for f in p.fases):
        partes.append(
            f"el motor reporta nivel {p.nivel_alcanzado} porque mira solo su fase activa,"
            f" {p.fase_activa}"
        )
    return ("falla" if tarde else "pasa"), "; ".join(partes)


def _entregado(obs: Observacion, _topes: Topes) -> Juicio:
    if (fuera := _sin_pliegue(obs)) is not None:
        return fuera
    p = cast(Pliegue, obs.pliegue)
    if p.nivel_autorizado not in ("integrado", "desplegado"):
        return "no medido", f"se pidió nivel {p.nivel_autorizado}: no hay merge que comprobar"
    fallas: list[str] = []
    sin_leer: list[str] = []
    if p.estado != "terminado":
        fallas.append(f"el encargo quedó {p.estado}, no entregado")
    if bajado := _bajado(p):
        fallas.append(bajado)
    if not p.base:
        sin_leer.append("la base pedida, que el registro no guarda")
    commits: dict[str, str] = {}
    for pr in obs.prs:
        if pr.numero == 0:
            fallas.append(f"la fase {pr.fase} no abrió PR")
        elif pr.estado is None:
            sin_leer.append(f"el PR #{pr.numero}, porque GitHub no respondió")
        elif pr.estado != "mergeado":
            fallas.append(f"el PR #{pr.numero} está {pr.estado}")
        elif not pr.base:
            sin_leer.append(f"la base del PR #{pr.numero}, que GitHub no dio")
        elif p.base and pr.base != p.base:
            fallas.append(f"el PR #{pr.numero} se mergeó en {pr.base} y se pidió {p.base}")
        elif not pr.merge_commit:
            sin_leer.append(f"el commit de merge del PR #{pr.numero}, que GitHub no dio")
        elif p.base:
            commits[pr.fase] = pr.merge_commit
    if not obs.prs:
        fallas.append("ninguna fase tiene PR")
    desplegables = [f.id for f in p.fases if f.desplegable]
    desplegadas = 0
    for fase in desplegables:
        if fase not in commits:
            continue
        if any(
            r.fase == fase and r.estado == "aplicado" and r.sha == commits[fase]
            for r in obs.recibos
        ):
            desplegadas += 1
            continue
        rechazos = [r.estado for r in obs.recibos if r.fase == fase and r.estado != "aplicado"]
        fallas.append(
            f"la fase {fase} no tiene recibo aplicado de su commit mergeado"
            + (f" ({'; '.join(rechazos)})" if rechazos else "")
        )
    if obs.archivo is not None:
        ruta = obs.archivo.ruta
        if obs.archivo.contenido == "no existe":
            fallas.append(f"no existe el archivo desplegado {ruta}")
        elif obs.archivo.contenido == "sin la marca":
            fallas.append(f"el archivo desplegado {ruta} no trae la marca")
        elif obs.archivo.contenido == "ilegible":
            sin_leer.append(f"el archivo desplegado {ruta}")
    if fallas:
        return "falla", "; ".join(fallas)
    if sin_leer:
        return "no medido", "no pude leer " + ", ".join(sin_leer)
    partes = [f"PR mergeados en {p.base}: {len(commits)} de {len(obs.prs)}"]
    if p.nivel_autorizado == "desplegado":
        partes.append(
            f"fases con recibo de despliegue aplicado de su commit mergeado:"
            f" {desplegadas} de {len(desplegables)}"
        )
    partes.append(
        "el archivo desplegado trae la marca" if obs.archivo is not None
        else "sin archivo desplegado que revisar"
    )
    return "pasa", "; ".join(partes)


def _vencimiento(plazo: Plazo, cierre: datetime, actualizado: str) -> tuple[datetime, str] | None:
    """Desde cuándo el resto está vencido y cómo decirlo; None si no se puede saber."""
    if plazo == "actualización":
        if not actualizado:
            return None
        hecha = _hora(actualizado)
        if hecha > cierre:
            return hecha, f"la actualización del {hecha:%Y-%m-%d %H:%M}"
        return datetime.max.replace(tzinfo=timezone.utc), "la próxima actualización del servicio"
    vence = cierre + timedelta(seconds=plazo)
    return vence, f"{vence:%Y-%m-%d %H:%M}"


def _limpio(obs: Observacion, _topes: Topes) -> Juicio:
    if (fuera := _sin_pliegue(obs)) is not None:
        return fuera
    p = cast(Pliegue, obs.pliegue)
    if not p.cierre:
        return "no medido", f"el encargo sigue {p.estado}: la limpieza empieza al cerrar"
    cierre, ahora = _hora(p.cierre), _hora(obs.hora)
    quedan: list[str] = []
    en_plazo: list[str] = []
    sin_mirar: list[str] = []
    for tipo, regla, plazo in LIMPIEZA:
        restos = [r for r in obs.restos if r.tipo == tipo]
        if not restos:
            sin_mirar.append(f"la regla «{regla}», que no se revisó")
        for resto in restos:
            if resto.queda is None:
                sin_mirar.append(resto.nombre)
                continue
            if not resto.queda:
                continue
            vencimiento = _vencimiento(plazo, cierre, obs.actualizado)
            if vencimiento is None:
                sin_mirar.append(f"si {resto.nombre} ya venció: no sé cuándo se actualizó el servicio")
            elif plazo == 0 or ahora >= vencimiento[0]:
                quedan.append(resto.nombre)
            else:
                en_plazo.append(f"{resto.nombre} hasta {vencimiento[1]}")
    plazo_txt = f"; dentro de su plazo: {', '.join(en_plazo)}" if en_plazo else ""
    if quedan:
        return "falla", "quedan " + ", ".join(quedan) + plazo_txt
    if sin_mirar:
        return "no medido", "no pude mirar " + ", ".join(sin_mirar) + plazo_txt
    return "pasa", (
        f"nada vencido sin borrar: revisé las {len(LIMPIEZA)} reglas de limpieza"
        f" ({len(obs.restos)} restos){plazo_txt}"
    )


def _cuesta_poco(obs: Observacion, topes: Topes) -> Juicio:
    if (fuera := _sin_pliegue(obs)) is not None:
        return fuera
    p = cast(Pliegue, obs.pliegue)
    duracion = _duracion(p.recibido, p.cierre or obs.hora)
    turnos = ", ".join(f"{rol} {n}" for rol, n in p.turnos) or "ninguno"
    texto = (
        f"turnos {turnos} (tope {topes.turnos_por_rol} por rol);"
        f" rondas de revisión {p.rondas} (tope {topes.rondas});"
        f" {_texto_duracion(duracion)} (tope {topes.minutos})"
    )
    caro = (
        any(n > topes.turnos_por_rol for _, n in p.turnos)
        or p.rondas > topes.rondas
        or duracion > timedelta(minutes=topes.minutos)
    )
    return ("falla" if caro else "pasa"), texto


def _version(obs: Observacion, _topes: Topes) -> Juicio:
    v = obs.version
    faltan = [
        nombre for nombre, valor in (
            ("el SHA desplegado (instalacion.json)", v.desplegado),
            ("el SHA del árbol del servicio", v.arbol),
            ("el SHA de la última pasada del supervisor", v.pasada),
        ) if not valor
    ]
    if faltan:
        return "no medido", "no pude leer " + ", ".join(faltan)
    if v.arbol == v.desplegado and v.pasada == v.desplegado:
        return "pasa", f"árbol y última pasada del supervisor en {v.desplegado[:12]}, el desplegado"
    return "falla", (
        f"desplegado {v.desplegado[:12]}, árbol {v.arbol[:12]},"
        f" última pasada {v.pasada[:12]}"
    )


def _legible(obs: Observacion, _topes: Topes) -> Juicio:
    p = obs.pliegue
    if p is None:
        return "no medido", "el encargo no está en el registro"
    if isinstance(p, NoPliega):
        return "falla", f"el plegado se rompe en el evento {p.evento} ({p.tipo}): {p.excepcion}"
    if p.violaciones:
        return "falla", "pliega, pero no sostiene " + ", ".join(p.violaciones)
    return "pasa", f"pliega y sostiene las {len(plegado.INVARIANTES)} invariantes de tools/plegado.py"


Condicion = tuple[bool | None, str]
"""True: se cumple; False: no; None: no se pudo mirar. El texto lo dice."""


def _corto(texto: str, largo: int = 160) -> str:
    return texto if len(texto) <= largo else texto[: largo - 1] + "…"


def _paro_con_palabra(p: Pliegue) -> Condicion:
    """docs/modelo-centro-encargos.md, "Qué significa cada palabra del reporte": una fase
    bloqueada se reporta "entrega parcial" con cada fase que falta y su causa, y `detenido`
    queda para sus cuatro causas. La causa de un `detenido` la juzgan `_se_paro_solo` y
    `_dice_por_que`: el registro todavía no la guarda como dato."""
    if p.estado == "detenido":
        return True, "quedó detenido"
    if p.estado != "terminado":
        return False, f"quedó {p.estado}: ni detenido ni entrega parcial"
    if p.completo:
        return False, "quedó terminado completo: no dijo que algo no se pudo"
    faltan = [f for f in p.fases if f.estado != "preparada"]
    if not faltan:
        return False, "quedó terminado sin fases: no nombra qué faltó"
    calladas = [f.id for f in faltan if not f.causa]
    if calladas:
        return False, f"quedó como entrega parcial sin decir por qué falta la fase {', '.join(calladas)}"
    return True, "quedó como entrega parcial: " + "; ".join(
        f"la fase {f.id}, «{_corto(f.causa)}»" for f in faltan
    )


def _se_paro_solo(p: Pliegue, hora: str, topes: Topes) -> Condicion:
    paradas = [o for o in p.ordenes if o in ("parar", "cancelar")]
    if paradas:
        return False, f"no se paró solo: recibió la orden {', '.join(paradas)}"
    if p.estado not in ("detenido", "cancelado", "terminado"):
        return False, f"no se paró: sigue {p.estado}"
    duracion = _duracion(p.recibido, p.cierre or hora)
    if duracion > timedelta(minutes=topes.minutos_se_detiene):
        return False, (
            f"tardó {_texto_duracion(duracion)} en pararse y el tope es"
            f" {topes.minutos_se_detiene} min"
        )
    return True, f"se paró solo en {_texto_duracion(duracion)} (tope {topes.minutos_se_detiene} min)"


def _dice_por_que(p: Pliegue) -> Condicion:
    causas = [*p.causas, *(f"la fase {f.id}: {f.causa}" for f in p.fases if f.causa)]
    if not causas:
        return False, "no dejó escrita la causa"
    return True, f"dijo por qué: «{_corto(causas[0])}»"


def _no_deja_roto(d: Detencion) -> Condicion:
    """El freno corrió (un checklist lanzado y en rojo), cada despliegue quedó revertido y
    el destino ya no lo trae. Lo que no se pudo leer no cuenta como revertido."""
    if d.archivo is not None and d.archivo.contenido == "con la marca":
        return False, f"{d.archivo.ruta} todavía trae lo desplegado"
    if d.despliegues is None:
        return None, "sus recibos de despliegue"
    if any(x.estado == "aplicado" for x in d.despliegues):
        return False, "su despliegue quedó aplicado: el checklist no falló"
    desplegados = [x for x in d.despliegues if any(paso == "desplegar" for paso, _ in x.pasos)]
    sin_revertir = [
        (x.fase, rollback) for x in desplegados
        if (rollback := dict(x.pasos).get("rollback", "sin correr")) != 0
    ]
    if sin_revertir:
        return False, "; ".join(
            f"el despliegue de la fase {fase} quedó sin revertir (rollback {rollback})"
            for fase, rollback in sin_revertir
        )
    rojos = [codigo for x in d.despliegues for paso, codigo in x.pasos if paso == "checklist" and codigo]
    if not rojos:
        return False, "el checklist no salió en rojo en ningún recibo: el freno no corrió"
    if d.archivo is not None and d.archivo.contenido == "ilegible":
        return None, f"el destino {d.archivo.ruta}"
    destino = f" y {d.archivo.ruta} ya no lo trae" if d.archivo is not None else ""
    return True, f"el checklist salió {rojos[0]}, revirtió su despliegue (rollback salida 0){destino}"


def _acotado(p: Pliegue, topes: Topes) -> Condicion:
    turnos = ", ".join(f"{rol} {n}" for rol, n in p.turnos) or "ninguno"
    gasto = (
        f"turnos {turnos} (tope {topes.turnos_por_rol} por rol) y rondas de revisión"
        f" {p.rondas} (tope {topes.rondas})"
    )
    de_mas = any(n > topes.turnos_por_rol for _, n in p.turnos) or p.rondas > topes.rondas
    return (False, f"gastó de más: {gasto}") if de_mas else (True, f"gastó {gasto}")


def _se_detiene(obs: Observacion, topes: Topes) -> Juicio:
    """Con un checklist de despliegue que falla a propósito, el motor para el encargo
    solo y con una palabra del modelo, con la causa escrita, sin dejar el despliegue
    roto y sin gastar de más."""
    d = obs.detencion
    if d is None:
        return "no medido", "esta corrida no hizo el encargo cuyo checklist falla a propósito"
    if d.pliegue is None:
        return "no medido", f"el encargo que falla a propósito, {d.encargo}, no está en el registro"
    if isinstance(d.pliegue, NoPliega):
        return "no medido", (
            f"el registro de {d.encargo} se rompe en el evento {d.pliegue.evento}"
            f" ({d.pliegue.tipo}): {d.pliegue.excepcion}"
        )
    p = d.pliegue
    condiciones = (
        _paro_con_palabra(p), _se_paro_solo(p, obs.hora, topes), _dice_por_que(p), _no_deja_roto(d),
        _acotado(p, topes),
    )
    fallas = [texto for ok, texto in condiciones if ok is False]
    sin_mirar = [texto for ok, texto in condiciones if ok is None]
    cumple = [texto for ok, texto in condiciones if ok]
    if fallas:
        return "falla", "; ".join(fallas) + (f"; sí {'; '.join(cumple)}" if cumple else "")
    if sin_mirar:
        return "no medido", "no pude leer " + "; ".join(sin_mirar)
    return "pasa", "; ".join(cumple)


def _sin_medicion(motivo: str) -> Callable[[Observacion, Topes], Juicio]:
    return lambda _obs, _topes: ("no medido", motivo)


Juez = Callable[[Observacion, Topes], Juicio]

TABLA: tuple[tuple[NombreFila, str, str, Juez], ...] = (
    ("termina", "1", "registro: estado, nivel y horas", _termina),
    ("entregado", "2", "GitHub (gh pr view) y el recibo de cada fase (despliegue.comprobar_recibo)",
     _entregado),
    ("limpio", "2", "la tabla Limpieza del modelo: GitHub, la base, el clon del servicio"
     " y los procesos", _limpio),
    ("no pierde", "3", "sin fuente todavía",
     _sin_medicion("necesita los cortes de energía del canario, que aún no existen")),
    ("no repite", "3", "sin fuente todavía",
     _sin_medicion("necesita contar PR, merges, recetas e invocaciones tras un corte")),
    ("cuesta poco", "4", "registro: turnos, revisiones y horas", _cuesta_poco),
    ("se detiene", "5", "el encargo cuyo checklist falla a propósito: registro, sus recibos"
     " de despliegue y su destino", _se_detiene),
    ("versión", "6", "instalacion.json, supervisor-pasada.json y HEAD del árbol", _version),
    ("legible", "todos", "registro: plegado e invariantes de tools/plegado.py", _legible),
)


def juzgar(obs: Observacion, esperado: Esperado) -> tuple[Fila, ...]:
    filas = []
    for nombre, punto, fuente, juez in TABLA:
        resultado, observado = juez(obs, esperado.topes)
        filas.append(
            Fila(nombre, punto, resultado, esperado.filas[nombre], observado, fuente)
        )
    return tuple(filas)


def leer_esperado(ruta: Path) -> Esperado:
    datos = leer_json(ruta)
    nombres = {nombre for nombre, *_ in TABLA}
    filas = datos.get("filas") if isinstance(datos, dict) else None
    topes = datos.get("topes") if isinstance(datos, dict) else None
    campos = {f.name for f in dataclasses.fields(Topes)}
    if (
        not isinstance(filas, dict)
        or set(filas) != nombres
        or any(v not in get_args(Resultado) for v in filas.values())
    ):
        raise ValueError(
            f"{ruta}: 'filas' debe dar uno de {list(get_args(Resultado))}"
            f" a cada fila: {sorted(nombres)}"
        )
    if (
        not isinstance(topes, dict)
        or set(topes) != campos
        or any(type(v) is not int for v in topes.values())
    ):
        raise ValueError(f"{ruta}: 'topes' debe traer enteros para {sorted(campos)}")
    # Una falla conocida sin su razón escrita se olvida: el archivo no admite comentarios.
    conocidas = sorted(nombre for nombre, valor in filas.items() if valor == "falla")
    razones = datos.get("razones")
    if (
        not isinstance(razones, dict)
        or sorted(razones) != conocidas
        or any(not isinstance(v, str) or not v.strip() for v in razones.values())
    ):
        raise ValueError(f"{ruta}: 'razones' debe explicar cada fila que se espera falla: {conocidas}")
    return Esperado(filas=cast(Mapping[NombreFila, Resultado], filas), topes=Topes(**topes))


# --------------------------------------------------------------------------- observar


class LectorPR(Protocol):
    def ver(self, repo: str, numero: int) -> PrView: ...


def _eventos(registro: Path, encargo: str) -> list[dict]:
    db = sqlite3.connect(f"{registro.resolve().as_uri()}?mode=ro", uri=True)
    try:
        filas = db.execute(
            "SELECT id, ts, kind, data FROM events WHERE encargo = ? ORDER BY id", (encargo,)
        ).fetchall()
    finally:
        db.close()
    return [
        {"id": i, "ts": ts, "kind": kind, "data": json.loads(data)}
        for i, ts, kind, data in filas
    ]


def _exige_despliegue(e: Encargo, f: Fase) -> bool:
    """Si la fase se quedara en integrado, `nivel_cumplido` no la daría por cumplida."""
    integrada = con_fase(e, replace(f, ultimo_paso_entrega="integrado"))
    return not nivel_cumplido(replace(integrada, fase_activa=f.id))


def vista(e: Encargo, eventos: list[dict], violaciones: tuple[str, ...] = ()) -> Pliegue:
    """El encargo plegado, juzgado con las funciones del motor y no con copias."""
    turnos = Counter(
        str(ev["data"].get("rol") or "") for ev in eventos if ev["kind"] == "intento_abierto"
    )
    activa = e.fase_actual
    return Pliegue(
        estado=e.estado,
        nivel_autorizado=e.entrega.nivel,
        nivel_efectivo=nivel_objetivo(e),
        motivo_efectivo=e.nivel_efectivo_motivo,
        nivel_alcanzado=e.nivel_alcanzado,
        fase_activa=activa.id if activa else "",
        base=e.entrega.base,
        recibido=eventos[0]["ts"],
        cierre=e.hora_cierre,
        completo=e.objetivo_completo(),
        fases=tuple(
            FaseVista(
                id=f.id,
                estado=f.estado,
                nivel=nivel_de(f),
                cumplido=nivel_cumplido(replace(e, fase_activa=f.id)),
                desplegable=_exige_despliegue(e, f),
                causa=f.bloqueo_causa,
            )
            for f in e.fases
        ),
        turnos=tuple(sorted(turnos.items())),
        # La misma lectura que el reductor: una revisión sin origen es independiente.
        rondas=sum(
            1 for ev in eventos
            if ev["kind"] == "revision"
            and str(ev["data"].get("origen") or "independiente") == "independiente"
        ),
        violaciones=violaciones,
        ordenes=tuple(str(ev["data"].get("accion") or "") for ev in eventos if ev["kind"] == "orden"),
        causas=tuple(dict.fromkeys(
            c for c in (e.motivo, *(i.causa for i in e.impedimentos)) if c.strip()
        )),
    )


def _pr(gh: LectorPR, repo: str, fase: str, numero: int) -> PR:
    if not numero:
        return PR(fase, 0, None)
    try:
        v = gh.ver(repo, numero)
    except Exception:  # un GitHub caído es un dato: la fila queda sin medir
        return PR(fase, numero, None)
    return PR(fase, numero, v.estado, v.base, v.merge_commit)


def _git(*argv: str, espera: float) -> str | None:
    """La salida de `git`; None si no se pudo correr o no terminó bien."""
    try:
        hecho = subprocess.run(
            ["git", *argv], capture_output=True, text=True, stdin=subprocess.DEVNULL,
            timeout=espera, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return hecho.stdout.strip() if hecho.returncode == 0 else None


def _rama_existe(repo: str, rama: str) -> bool | None:
    try:
        remoto = resolver_repositorio(repo).remoto
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    salida = _git("ls-remote", "--", remoto, f"refs/heads/{rama}", espera=2 * ESPERA_GIT_S)
    return None if salida is None else bool(salida)


def _referencia_vencida(servicio: Path | None, rama: str, remota: bool | None) -> bool | None:
    """La rama ya no existe en el remoto y el clon del servicio aún guarda su referencia."""
    if servicio is None:
        return None
    salida = _git(
        "-C", str(servicio), "for-each-ref", "--format=%(refname)",
        f"refs/remotes/origin/{rama}", espera=ESPERA_GIT_S,
    )
    if salida is None:
        return None
    if not salida:
        return False
    return None if remota is None else not remota


def _tri(valores: Iterable[bool | None]) -> bool | None:
    """True si alguno queda, None si alguno no se pudo mirar, False si ninguno."""
    vistos = list(valores)
    if True in vistos:
        return True
    return None if None in vistos else False


def _existe(ruta: Path) -> bool | None:
    try:
        os.lstat(ruta)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    return True


def _vivo(directorio: Path, proc: dict) -> bool | None:
    """Lo que dice `sesion.observar` sin escribir: el grupo del lanzador, el comprobador
    en curso y los descendientes de `arbol.json`. None: no se pudo mirar."""
    try:
        estado = sesion.observar(directorio, proc, escribir=False)
    except (OSError, KeyError, TypeError, ValueError):
        return None
    if estado == "ausente" and not proc:
        return None  # una identidad ilegible no demuestra que no quede nadie
    return {"vivo": True, "ausente": False}.get(estado)


def _json_o_vacio(ruta: Path) -> dict:
    try:
        datos = leer_json(ruta)
    except (OSError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}


def _procesos_de_turnos(base: Path, encargo: str, eventos: list[dict]) -> Resto:
    """Los mismos candidatos que cierra el motor al limpiar (`proc.json` o
    `wrapper.json` de `ejecucion/<id>` y de cada intento), más la identidad de cada
    `intento_abierto`, que sobrevive al borrado del directorio. El proceso vigente
    del encargo sale de esos mismos eventos, así que ya está entre ellos."""
    raiz = base / "ejecucion" / encargo
    try:
        carpetas = [raiz, *sorted(raiz.glob("intento-*"))] if raiz.is_dir() else []
    except OSError:
        return Resto("proceso", f"los procesos de sus turnos en {raiz.name}", None)
    candidatos: list[tuple[Path, dict]] = []
    for carpeta in carpetas:
        for nombre in ("proc.json", "wrapper.json"):
            if _existe(carpeta / nombre):
                candidatos.append((carpeta, _json_o_vacio(carpeta / nombre)))
                break
    for ev in eventos:
        if ev["kind"] == "intento_abierto":
            token = str(ev["data"].get("id") or "")
            intentos = sorted(raiz.glob(f"intento-*-{token}")) if token else []
            candidatos += [(carpeta, ev["data"]) for carpeta in (raiz, *intentos)]
    # Cada carpeta guarda su comprobador y su arbol.json: la misma identidad se mira en todas.
    unicos = {
        (str(c), str(p.get("pid")), str(p.get("inicio"))): (c, p) for c, p in candidatos
    }
    turnos = sum(1 for ev in eventos if ev["kind"] == "intento_abierto")
    return Resto(
        "proceso", f"algún proceso de sus {turnos} turnos",
        _tri(_vivo(c, p) for c, p in unicos.values()),
    )


def _recibo(e: Encargo, fase: str) -> Recibo:
    try:
        datos = comprobar_recibo(replace(e, fase_activa=fase))
    except ValueError as exc:
        return Recibo(fase, str(exc), "")
    return Recibo(fase, "aplicado", str(datos.get("sha") or ""))


def _claves(base: Path) -> list[Path]:
    """Las carpetas de `despliegues/`, una por despliegue; OSError si no se pudo listar."""
    raiz = base / "despliegues"
    return sorted(d for d in raiz.iterdir() if d.is_dir()) if raiz.is_dir() else []


def _es_del_encargo(clave: Path, encargo: str) -> bool:
    recibo = _json_o_vacio(clave / "recibo.json")
    identidad = _json_o_vacio(clave / "pedido.json").get("identidad")
    dueno = identidad.get("encargo") if isinstance(identidad, dict) else None
    return encargo in (dueno, recibo.get("encargo"))


def _objeto(ruta: Path) -> dict | None:
    """El objeto JSON de `ruta`: vacío si no existe; None si existe y no se pudo leer."""
    try:
        datos = leer_json(ruta)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    return datos if isinstance(datos, dict) else None


def _dueno(pedido: dict | None, recibo: dict | None) -> str:
    """El encargo del despliegue: el de la identidad de su pedido o, si no, el de su recibo."""
    identidad = (pedido or {}).get("identidad")
    for dueno in (identidad.get("encargo") if isinstance(identidad, dict) else None, (recibo or {}).get("encargo")):
        if isinstance(dueno, str) and dueno:
            return dueno
    return ""


def _pasos_lanzados(recibo: dict) -> tuple[tuple[str, int], ...] | None:
    """Los pasos que el trabajador lanzó, con su salida; None si alguno no dice si se lanzó."""
    pasos = recibo.get("pasos")
    if not isinstance(pasos, list):
        return None
    lanzados = []
    for paso in pasos:
        if not (isinstance(paso, dict) and isinstance(paso.get("paso"), str) and type(paso.get("enviado")) is bool):
            return None
        if paso["enviado"]:
            if type(paso.get("codigo")) is not int:
                return None
            lanzados.append((paso["paso"], paso["codigo"]))
    return tuple(lanzados)


def _recibos_de_despliegue(base: Path, encargo: str) -> tuple[Despliegue, ...] | None:
    """Los recibos de despliegue del encargo. None si alguno que puede ser suyo no se pudo
    leer o no dice qué pasos lanzó: un recibo ilegible no es un despliegue que no corrió."""
    try:
        claves = _claves(base)
    except OSError:
        return None
    despliegues = []
    for clave in claves:
        pedido, recibo = _objeto(clave / "pedido.json"), _objeto(clave / "recibo.json")
        dueno = _dueno(pedido, recibo)
        if not dueno and (pedido is None or recibo is None):
            return None
        if dueno != encargo:
            continue
        if recibo is None or (pasos := _pasos_lanzados(recibo)) is None:
            return None
        despliegues.append(Despliegue(str(recibo.get("fase") or ""), str(recibo.get("estado") or ""), pasos))
    return tuple(despliegues)


def _despliegues(base: Path, encargo: str) -> tuple[Resto, ...]:
    """Lo que quedó junto a los recibos del encargo y sus trabajadores."""
    raiz = base / "despliegues"
    try:
        claves = _claves(base)
    except OSError:
        return (
            Resto("despliegue", "despliegues/", None),
            Resto("proceso", "algún trabajador de despliegue", None),
        )
    restos: list[Resto] = []
    vivos: list[bool | None] = []
    for clave in claves:
        if not _es_del_encargo(clave, encargo):
            continue
        try:
            demas = sorted(x.name for x in clave.iterdir() if x.name != "recibo.json")
        except OSError:
            restos.append(Resto("despliegue", f"despliegues/{clave.name[:12]}", None))
        else:
            restos.append(Resto(
                "despliegue", f"despliegues/{clave.name[:12]} ({', '.join(demas)})", bool(demas),
            ))
        for nombre in ("proceso.json", "comando.json"):
            ident = _json_o_vacio(clave / nombre)
            if _existe(clave / nombre) and ident.get("estado") != "no_enviado":
                vivos.append(_vivo(clave, ident))
    puntero = raiz / f"{encargo}.actual"
    if (hay := _existe(puntero)) is not False:
        restos.append(Resto("despliegue", f"despliegues/{puntero.name}", hay))
    if not restos:
        restos.append(Resto("despliegue", "ningún despliegue del encargo", False))
    restos.append(Resto("proceso", "algún trabajador de despliegue", _tri(vivos)))
    return tuple(restos)


def _git_head(arbol: Path) -> str:
    return _git("-C", str(arbol), "rev-parse", "HEAD", espera=ESPERA_GIT_S) or ""


def _version_de(
    pasada: dict, instalacion: dict, servicio: Path | None, sha_desplegado: str,
) -> Version:
    destino = instalacion.get("destino")
    desplegado = sha_desplegado or str(
        (destino.get("codigo") if isinstance(destino, dict) else "") or ""
    )
    return Version(
        desplegado=desplegado,
        arbol=_git_head(servicio) if servicio else "",
        pasada=str(pasada.get("sha") or ""),
    )


def _archivo(ruta: Path, marca: str) -> ArchivoDesplegado:
    try:
        texto = leer_texto(ruta, errors="replace")
    except FileNotFoundError:
        return ArchivoDesplegado(str(ruta), marca, "no existe")
    except OSError:
        return ArchivoDesplegado(str(ruta), marca, "ilegible")
    return ArchivoDesplegado(str(ruta), marca, "con la marca" if marca in texto else "sin la marca")


def _restos(
    base: Path, e: Encargo, eventos: list[dict], prs: tuple[PR, ...],
    servicio: Path | None, despliegue: tuple[Resto, ...],
) -> tuple[Resto, ...]:
    repo = e.entrega.repo
    ramas = sorted({f.rama for f in e.fases if f.rama}) if repo else []
    remotas = {rama: _rama_existe(repo, rama) for rama in ramas}
    if e.estado == "cancelado":
        abiertos = [
            Resto("pr", f"el PR #{pr.numero} abierto", None if pr.estado is None else pr.estado == "abierto")
            for pr in prs if pr.numero
        ] or [Resto("pr", "ningún PR que cerrar", False)]
    else:
        abiertos = [Resto("pr", f"ningún PR que cerrar: el encargo quedó {e.estado}", False)]
    return (
        *(Resto("rama", f"la rama remota {rama}", queda) for rama, queda in remotas.items()),
        *([] if ramas else [Resto("rama", "ninguna rama remota registrada", False)]),
        *abiertos,
        *(
            Resto(tipo, f"{tipo}/{e.id}", _existe(base / tipo / e.id))
            for tipo in ("espacio", "ejecucion", "runtime")
        ),
        *despliegue,
        *(
            Resto(
                "referencia", f"la referencia origin/{rama} en el clon del servicio",
                _referencia_vencida(servicio, rama, remotas[rama]),
            )
            for rama in ramas
        ),
        *([] if ramas else [Resto("referencia", "ninguna rama que buscar en el clon del servicio", False)]),
        _procesos_de_turnos(base, e.id, eventos),
    )


def procesos(base: Path, encargo: str) -> tuple[Resto, ...]:
    """Los procesos del encargo que la fila limpio pide muertos, mirados con las mismas
    identidades que `observar`: los de sus turnos y sus trabajadores de despliegue."""
    eventos = _eventos(base / "registro.sqlite", encargo)
    return (
        _procesos_de_turnos(base, encargo, eventos),
        *(r for r in _despliegues(base, encargo) if r.tipo == "proceso"),
    )


def _plegar(eventos: list[dict]) -> tuple[Pliegue, Encargo] | NoPliega:
    """El encargo plegado con el reductor del árbol y su vista, o dónde se rompe."""
    reductor = plegado.cargar_reductor(RAIZ)
    traza = plegado.trazar(reductor, eventos)
    if traza.fallo is not None:
        return NoPliega(traza.fallo.evento, traza.fallo.tipo, traza.fallo.excepcion)
    e: Encargo = traza.estados[-1]
    violaciones = tuple(
        nombre for nombre, invariante in plegado.INVARIANTES
        if invariante(reductor, eventos, traza) is not None
    )
    return vista(e, eventos, violaciones), e


def _detencion(base: Path, encargo: str, archivo: Path | None) -> Detencion:
    desplegado = _archivo(archivo, encargo) if archivo else None
    eventos = _eventos(base / "registro.sqlite", encargo)
    if not eventos:
        return Detencion(encargo, None, archivo=desplegado)
    plegada = _plegar(eventos)
    if isinstance(plegada, NoPliega):
        return Detencion(encargo, plegada, archivo=desplegado)
    return Detencion(encargo, plegada[0], _recibos_de_despliegue(base, encargo), desplegado)


def observar(
    base: Path,
    encargo: str,
    gh: LectorPR,
    *,
    hora: str,
    arbol: Path | None = None,
    sha_desplegado: str = "",
    archivo: tuple[Path, str] | None = None,
    detencion: str = "",
    archivo_detencion: Path | None = None,
) -> Observacion:
    eventos = _eventos(base / "registro.sqlite", encargo)
    pasada = _json_o_vacio(base / "supervisor-pasada.json")
    instalacion = _json_o_vacio(base / "instalacion.json")
    servicio = arbol or (Path(str(pasada["ruta"])) if pasada.get("ruta") else None)
    comunes = Observacion(
        hora, None,
        version=_version_de(pasada, instalacion, servicio, sha_desplegado),
        actualizado=str(instalacion.get("hora") or ""),
        archivo=_archivo(*archivo) if archivo else None,
        detencion=_detencion(base, detencion, archivo_detencion) if detencion else None,
    )
    if not eventos:
        return comunes
    plegada = _plegar(eventos)
    if isinstance(plegada, NoPliega):
        return replace(comunes, pliegue=plegada)
    pliegue, e = plegada
    repo = e.entrega.repo
    prs = tuple(_pr(gh, repo, f.id, f.pr_numero) for f in e.fases) if repo else ()
    return replace(
        comunes,
        pliegue=pliegue,
        prs=prs,
        recibos=tuple(_recibo(e, f.id) for f in pliegue.fases if f.desplegable),
        restos=_restos(base, e, eventos, prs, servicio, _despliegues(base, encargo)),
    )


# --------------------------------------------------------------------------- salida


def tabla(filas: tuple[Fila, ...]) -> str:
    lineas = [
        "| Fila | Punto | Resultado | Esperado | Observado | Fuente |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    lineas += [
        f"| {f.nombre} | {f.punto} | {f.resultado} | {f.esperado} | {f.observado} | {f.fuente} |"
        for f in filas
    ]
    distintas = [f for f in filas if f.discrepancia]
    if distintas:
        lineas.append("")
        lineas.append("No coincide con lo esperado:")
        lineas += [f"- {f.nombre}: {f.discrepancia}" for f in distintas]
    else:
        lineas.append("")
        lineas.append("Coincide con lo esperado.")
    return "\n".join(lineas)


def _ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: list[str] | None = None, gh: LectorPR | None = None) -> int:
    for flujo in (sys.stdout, sys.stderr):
        if isinstance(flujo, io.TextIOWrapper):
            flujo.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(
        description="Juzga un encargo terminado contra la tabla del canario.",
        epilog="La salida trae ids, ramas y números de PR: no la mandes a un log público.",
    )
    p.add_argument("encargo")
    p.add_argument("--base", type=Path, default=Path.home() / ".motor-encargos")
    p.add_argument("--esperado", type=Path, default=ESPERADO)
    p.add_argument("--arbol", type=Path, help="árbol del servicio; por omisión, el de la última pasada")
    p.add_argument("--sha-desplegado", default="", help="por omisión, el de instalacion.json")
    p.add_argument("--archivo-desplegado", type=Path)
    p.add_argument("--marca", default="")
    p.add_argument("--detencion", default="", help="el encargo cuyo checklist falla a propósito")
    p.add_argument("--archivo-detencion", type=Path)
    args = p.parse_args(argv)
    if (args.archivo_desplegado is None) != (not args.marca):
        p.error("--archivo-desplegado y --marca van juntos")
    if args.archivo_detencion is not None and not args.detencion:
        p.error("--archivo-detencion pide --detencion")
    try:
        esperado = leer_esperado(args.esperado)
    except (OSError, ValueError) as exc:
        print(f"canario: {exc}", file=sys.stderr)
        return 2
    if not (args.base / "registro.sqlite").is_file():
        print(f"canario: no existe {args.base / 'registro.sqlite'}", file=sys.stderr)
        return 2
    obs = observar(
        args.base, args.encargo, gh or GhGitHub(),
        hora=_ahora(),
        arbol=args.arbol,
        sha_desplegado=args.sha_desplegado,
        archivo=(args.archivo_desplegado, args.marca) if args.archivo_desplegado else None,
        detencion=args.detencion,
        archivo_detencion=args.archivo_detencion,
    )
    if obs.pliegue is None:
        print(f"canario: el encargo {args.encargo} no está en el registro", file=sys.stderr)
        return 2
    filas = juzgar(obs, esperado)
    print(f"Encargo {args.encargo}, observado {obs.hora}.\n")
    print(tabla(filas))
    return 1 if any(f.discrepancia for f in filas) else 0


if __name__ == "__main__":
    raise SystemExit(main())
