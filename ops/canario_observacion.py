"""Lo que el canario de encargos observa de un encargo: la forma de los datos que
`canario_observar.observar` devuelve y `canario_juzgar.juzgar` lee."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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
