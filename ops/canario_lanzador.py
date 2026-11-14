"""Corre el canario de encargos en esta máquina y termina con su tabla.

Uso, con el intérprete del servicio de la máquina (los procesos que lanza usan
el mismo), desde el árbol del motor que se prueba:

    <python del servicio> <árbol>/ops/canario_lanzador.py --repo <repo del canario>
        [--base ~/.motor-canario] [--base-viva ~/.motor-encargos]
        [--despliegue ~/motor-canario-despliegue] [--corrida <nombre>] [--maquina <nombre>]
        [--tope-min <n>] [--tope-cierre-s <n>] [--intervalo-s <n>]
        [--esperado <árbol>/ops/canario_encargos.esperado.json] [--sin-se-detiene]

El árbol es siempre el de este archivo: el supervisor, el agente de ensayo y el
reductor con el que se mide salen de él. Las rutas relativas se leen desde donde
se corre la orden.

Una corrida lleva dos encargos al mismo supervisor: el que termina, que mide casi
toda la tabla, y el que falla a propósito, que mide la fila "se detiene". Con
`--sin-se-detiene` solo corre el primero, y esa fila queda sin medir.

Una corrida da estos pasos, en este orden (`Paso`):

1. preparar: deja la base aparte con lo que el motor lee de la base viva, cada cosa
   como dice `COMPARTIDOS`, y sin `PROHIBIDOS`.
2. encargar: recibe un encargo de nivel desplegado contra el repo del canario,
   con la clave canario-<versión>-<máquina>-<corrida>, y el que falla con la misma
   clave más -se-detiene. La misma corrida retoma los mismos encargos. Al que
   falla le deja en `canario-escenarios/<id>.json` su escenario
   (`escenario_se_detiene`): su fase trae su propia receta de despliegue, con un
   checklist que siempre sale rojo.
3. lanzar: un supervisor propio con `--agente ensayo` desde el árbol, con
   `MOTOR_CANARIO_ARBOL`, que es de donde el `trabajo.sh` del repo del canario
   toma el agente de ensayo, y `MOTOR_CANARIO_DESPLIEGUE`, la carpeta del
   despliegue, de donde la receta del que falla toma su destino.
4. esperar: `motor ver` de los dos encargos a la vez, hasta que cada uno cierra,
   vence su tope o el supervisor muere. Cada tope cuenta desde que se recibió su
   encargo, y el del que falla es más corto, para que un bucle del motor no
   alargue la corrida. Al que vence lo para con `motor parar`, la orden del
   canario, sin tocar el supervisor que el otro necesita. No escribe otro evento.
5. cerrar: mata el grupo de su supervisor y, por cada encargo, lo para si no cerró
   y espera, hasta `--tope-cierre-s`, a que no quede vivo ningún proceso de sus
   turnos ni de sus trabajadores de despliegue, que el motor lanza en sesión
   propia. Pasa siempre que el encargo se recibió, aunque un paso anterior falle,
   y cada uno de sus pasos se intenta aunque otro falle o lo corte un Ctrl-C. Lo
   que falló queda en `Cierre.fallas`; un Ctrl-C se vuelve a lanzar al terminar
   el cierre.
6. medir: `canario_observar.observar` y `canario_juzgar.juzgar`.
7. limpiar: borra de cada encargo su rama remota encargo/<id>, espacio/<id>,
   ejecucion/<id>, trabajos/<id>.json de la flota y su escenario propio. Con algo
   que no es un id no borra nada, ni tampoco por una carpeta de la base que lleve
   fuera de ella. Después recoge lo mismo de los encargos cerrados de corridas
   anteriores (`recoger`).

Solo se mide y se borra con el cierre de cada encargo listo (`Cierre.listo`): el
encargo cerrado, el grupo del supervisor ausente, ningún proceso del encargo vivo
o sin poder mirarse y ningún paso del cierre caído. Si no, la orden no borra nada
y nombra lo que quedó.

Por stderr van los avances y la tabla. Por stdout, un solo objeto JSON: el
`Informe` una vez recibido el encargo, o `{"corrida", "paso", "error", "codigo": 2}`
si no se llegó a recibir (`paso` vacío ante un error que ningún paso esperaba). El
mismo objeto queda en `<base>/canario-informe.json`. Sale con 0 si cada fila da lo
esperado, el cierre quedó listo y la limpieza y la recogida terminaron; con 1 si
no; con 2 si un paso no pudo correr.

La corrida sigue aunque se pierda la sesión que la lanzó: escribir en ella es de
mejor esfuerzo y SIGHUP no la corta. Por ssh, al caerse la sesión, escribir falla
con EPIPE en la Mac y la Mini y con EINVAL en la PC. En la PC, OpenSSH mete la
sesión en un Job que mata a sus procesos cuando sale el lanzador; el supervisor y
los trabajadores del motor salen de ese Job al nacer.

La salida trae ids, ramas y el repo: no va a un log público.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import traceback
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TextIO, TypeVar, cast

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from motor import plataforma  # noqa: E402
from motor.encargos.aislamiento import TOKEN_DE_AGENTES  # noqa: E402
from motor.encargos.copiar import _quitar as quitar  # noqa: E402
from motor.encargos.registro import Registro  # noqa: E402
from motor.github import GhGitHub  # noqa: E402
from motor.io import escribir_texto, leer_texto  # noqa: E402
from motor.repositorios import resolver_repositorio  # noqa: E402
from ops import canario_juzgar, canario_observar  # noqa: E402
from ops.canario_encargos import tabla  # noqa: E402
from ops.agente_ensayo import ESCENARIOS, Escenario  # noqa: E402

Paso = Literal["preparar", "encargar", "lanzar", "esperar", "cerrar", "medir", "limpiar"]
Fin = Literal["terminado", "detenido", "cancelado", "tope", "supervisor_muerto"]
CERRADOS: dict[str, Fin] = {"terminado": "terminado", "detenido": "detenido", "cancelado": "cancelado"}
Presencia = Literal["ausente", "vivo", "incierto"]
"""Lo que queda del grupo del supervisor, con las palabras de `plataforma.presencia_grupo`."""

Forma = Literal["enlace duro", "copia"]


@dataclass(frozen=True)
class Compartido:
    """Algo de la base viva que el motor lee, y cómo lo recibe la base del canario."""

    ruta: Path
    forma: Forma
    porque: str
    obligatorio: bool = True
    """False: si la base viva no lo tiene, el canario lo quita de su base y sigue."""


_SOLO_LO_LEE = (
    "el motor solo lo lee: el enlace duro no pide el privilegio que Windows pide para uno"
    " simbólico, y el canario lee el mismo archivo que la base viva"
)
COMPARTIDOS = (
    Compartido(Path("maquina.toml"), "enlace duro", _SOLO_LO_LEE),
    Compartido(Path("github.json"), "enlace duro", _SOLO_LO_LEE),
    Compartido(Path("capacidades.json"), "enlace duro", _SOLO_LO_LEE),
    Compartido(
        Path(TOKEN_DE_AGENTES), "enlace duro",
        _SOLO_LO_LEE + "; sin él, el agente del canario solo ejercería el caso sin token",
        obligatorio=False,
    ),
    Compartido(
        Path("flota-home.json"), "copia",
        "servicio.vincular_base lo escribe en sitio: por un enlace, esa escritura caería en la"
        " base viva",
    ),
    Compartido(
        Path("politica") / "poteto", "copia",
        "una carpeta no admite enlace duro, y por un enlace lo que escribiera el canario caería"
        " en la base viva",
        obligatorio=False,
    ),
)
PROHIBIDOS = ("secretos.env", "canal.json")
"""secretos.env trae la URL de Kuma y canal.json el canal: con ellos el supervisor
del canario hablaría en nombre del servicio vivo."""
REGISTRO = ("registro.sqlite", "registro.sqlite-wal", "registro.sqlite-shm")
"""Lo que SQLite escribe del registro: si uno fuera el de la base viva, el canario escribiría en ella."""
INFORME = "canario-informe.json"
TEXTO = "Canario: escribe el id de esta corrida en corridas/<máquina>.txt."
SE_DETIENE = "se-detiene"
TEXTO_SE_DETIENE = (
    f"Canario: escribe el id de esta corrida en canario/{SE_DETIENE}/<máquina>.txt;"
    " su checklist de despliegue falla a propósito."
)
DESTINO = "MOTOR_CANARIO_DESPLIEGUE"
ID = re.compile(r"enc_[0-9a-f]+")
CORRIDA = re.compile(r"[A-Za-z0-9._-]+")
ESPERA_GRUPO_S = 10.0
T = TypeVar("T")


class NoSePuede(Exception):
    paso: Paso

    def __init__(self, paso: Paso, causa: str):
        super().__init__(causa)
        self.paso = paso
        self.causa = causa


@dataclass(frozen=True)
class Pedido:
    repo: str
    base: Path
    base_viva: Path
    despliegue: Path
    """La carpeta donde la receta del repo del canario deja `corridas/<máquina>.txt`."""
    corrida: str
    maquina: str
    """Vacío: el nombre de maquina.toml."""
    tope_s: float | None
    """None: los minutos de `termina` en lo esperado."""
    intervalo_s: float
    esperado: Path
    tope_cierre_s: float
    """Cuánto espera el cierre a que mueran los procesos del encargo."""
    se_detiene: bool = True
    """Si corre también el encargo cuyo checklist falla a propósito."""


@dataclass(frozen=True)
class Fallo:
    paso: Paso | None
    """None: un error que ningún paso esperaba."""
    causa: str


@dataclass(frozen=True)
class Espera:
    """Cómo terminó la espera de un encargo."""

    fin: Fin
    estado: str
    """El del encargo según `motor ver` cuando terminó su espera."""
    parada: str = ""
    """Lo que dejó `motor parar` al vencer su tope; vacío si no venció."""


@dataclass(frozen=True)
class Cierre:
    """Cómo quedó lo de la corrida antes de medir y borrar."""

    estado: str
    """El del encargo según `motor ver` al terminar el cierre; vacío si no se pudo leer."""
    parada: str
    """Vacío si nadie lo paró; si no, lo que dejó el último `motor parar`, el de la espera
    al vencer su tope o el del cierre."""
    supervisor: Presencia
    vivos: tuple[str, ...]
    """Los procesos del encargo que al vencer el tope seguían vivos o no se pudieron mirar."""
    fallas: tuple[str, ...] = ()
    """Los pasos del cierre que se cayeron, con su causa."""

    @property
    def listo(self) -> bool:
        return (
            self.estado in CERRADOS and self.supervisor == "ausente" and not self.vivos
            and not self.fallas
        )


@dataclass(frozen=True)
class Borrado:
    resto: str
    resultado: Literal["borrado", "no estaba", "falló"]
    causa: str = ""


@dataclass(frozen=True)
class Seguimiento:
    """El encargo cuyo checklist falla a propósito: cómo terminó su espera y su cierre."""

    clave: str
    encargo: str
    fin: Fin | None
    """None: la espera no llegó a terminar."""
    estado: str
    cierre: Cierre


@dataclass(frozen=True)
class Informe:
    maquina: str
    version: str
    corrida: str
    clave: str
    encargo: str
    fin: Fin | None
    """None: la espera no llegó a terminar."""
    estado: str
    """El estado del encargo al terminar la espera, según `motor ver`."""
    segundos: int
    """Desde que se pidió el encargo hasta que terminó la espera."""
    cierre: Cierre
    filas: tuple[canario_juzgar.Fila, ...]
    """Vacío si no se midió: un cierre no quedó listo o un paso anterior falló."""
    limpieza: tuple[Borrado, ...]
    """Lo que se borró de los dos encargos; vacío si no se borró: un cierre no quedó listo."""
    fallo: Fallo | None = None
    recogida: tuple[Borrado, ...] = ()
    """Lo que `recoger` borró, o no pudo borrar, de las corridas anteriores."""
    detencion: Seguimiento | None = None
    """None: la corrida no llegó a recibir el encargo que falla a propósito."""

    @property
    def coincide(self) -> bool:
        return bool(self.filas) and not any(f.discrepancia for f in self.filas)

    @property
    def codigo(self) -> int:
        if self.fallo is not None:
            return 2
        cierres = (self.cierre, *((self.detencion.cierre,) if self.detencion else ()))
        limpio = all(c.listo for c in cierres) and all(
            b.resultado != "falló" for b in (*self.limpieza, *self.recogida)
        )
        return 0 if self.coincide and limpio else 1

    def json(self) -> dict:
        return {
            **{k: v for k, v in asdict(self).items() if k not in ("filas", "limpieza", "fallo")},
            "paso": (self.fallo.paso or "") if self.fallo else "",
            "error": self.fallo.causa if self.fallo else "",
            "filas": [
                {
                    "nombre": f.nombre, "resultado": f.resultado, "esperado": f.esperado,
                    "discrepancia": f.discrepancia, "observado": f.observado,
                }
                for f in self.filas
            ],
            "limpieza": [asdict(b) for b in self.limpieza],
            "coincide": self.coincide,
            "codigo": self.codigo,
            "tabla": tabla(self.filas) if self.filas else "",
        }


def _decir(texto: str, flujo: TextIO) -> None:
    """Escribe en la sesión si sigue ahí: sin ella, la corrida termina igual."""
    try:
        print(texto, file=flujo, flush=True)
    except (OSError, ValueError):
        # Lo que quedó en el búfer falla otra vez al salir, y Python sale entonces con 120.
        with contextlib.suppress(OSError, ValueError):
            nulo = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(nulo, flujo.fileno())
            finally:
                os.close(nulo)


def _avance(paso: Paso | None, texto: str) -> None:
    _decir(f"canario [{paso or '?'}] {texto}", sys.stderr)


@contextlib.contextmanager
def _sin_colgar() -> Iterator[None]:
    """SIGHUP no corta la corrida. Un manejador, y no SIG_IGN, para que no lo hereden
    los procesos que lanza."""
    if not hasattr(signal, "SIGHUP") or threading.current_thread() is not threading.main_thread():
        yield
        return
    antes = signal.signal(signal.SIGHUP, lambda *_: None)
    try:
        yield
    finally:
        signal.signal(signal.SIGHUP, antes)


def _entorno(p: Pedido) -> dict[str, str]:
    # Sin .pyc: el árbol puede ser el del servicio vivo y el canario no escribe en él.
    return {
        **os.environ, "PYTHONDONTWRITEBYTECODE": "1", "MOTOR_CANARIO_ARBOL": str(RAIZ),
        DESTINO: p.despliegue.as_posix(),
    }


def _motor(p: Pedido, *argv: str) -> dict:
    """La salida JSON de `python -m motor <argv>` desde el árbol; RuntimeError si no la hay."""
    try:
        hecho = subprocess.run(
            [sys.executable, "-B", "-m", "motor", *argv], cwd=RAIZ, env=_entorno(p),
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"motor {argv[0]}: {exc}") from exc
    try:
        datos = json.loads(hecho.stdout)
    except ValueError:
        datos = None
    if not isinstance(datos, dict):
        raise RuntimeError(f"motor {argv[0]} salió {hecho.returncode}: {hecho.stderr.strip()[-400:]}")
    return datos


def _estado(p: Pedido, encargo: str, paso: Paso) -> str:
    """El estado del encargo según `motor ver`; vacío si no se pudo leer."""
    try:
        return str(_motor(p, "ver", encargo, "--base", str(p.base)).get("estado") or "")
    except RuntimeError as exc:
        _avance(paso, f"no pude leer el encargo: {exc}")
        return ""


def _aparte(base: Path, viva: Path) -> bool:
    base, viva = base.expanduser().resolve(), viva.expanduser().resolve()
    return not (base == viva or viva in base.parents or base in viva.parents)


def _sale_de(base: Path, ruta: Path) -> bool:
    """Si las carpetas de `ruta` llevan fuera de `base`: rmtree y la copia siguen un enlace
    que no sea el último tramo."""
    return not ruta.parent.resolve().is_relative_to(base.resolve())


def _enlazar(fuente: Path, enlace: Path) -> None:
    if enlace.exists() and enlace.samefile(fuente):
        return
    if os.path.lexists(enlace) and not enlace.is_symlink():
        raise NoSePuede(
            "preparar",
            f"{enlace} existe y no es el archivo de {fuente}; si es un enlace viejo, bórralo",
        )
    enlace.unlink(missing_ok=True)
    os.link(fuente, enlace)


def _soltar(propio: Path) -> None:
    # Un archivo se desenlaza sin tocar sus permisos: puede ser un enlace duro al vivo.
    if propio.is_dir() and not propio.is_symlink():
        quitar(propio)
    elif os.path.lexists(propio):
        propio.unlink()


def _copiar(fuente: Path, copia: Path) -> None:
    _soltar(copia)
    if fuente.is_dir():
        shutil.copytree(fuente, copia)
    elif fuente.is_file():
        shutil.copyfile(fuente, copia)


def preparar(p: Pedido) -> tuple[str, canario_juzgar.Esperado]:
    if not _aparte(p.base, p.base_viva):
        raise NoSePuede("preparar", f"{p.base} no está aparte de la base viva {p.base_viva}")
    base, viva = p.base.expanduser().resolve(), p.base_viva.expanduser().resolve()
    sobran = [n for n in PROHIBIDOS if os.path.lexists(base / n)]
    if sobran:
        raise NoSePuede("preparar", f"{p.base} trae {', '.join(sobran)}")
    for nombre in REGISTRO:
        propio, vivo = base / nombre, viva / nombre
        if propio.is_symlink() or (propio.exists() and vivo.exists() and propio.samefile(vivo)):
            raise NoSePuede("preparar", f"{propio} es un enlace o el de la base viva")
    base.mkdir(parents=True, exist_ok=True)
    for c in COMPARTIDOS:
        fuente, propio = viva / c.ruta, base / c.ruta
        if _sale_de(base, propio):
            raise NoSePuede("preparar", f"{propio} lleva fuera de {p.base} por un enlace")
        if c.obligatorio and not fuente.is_file():
            raise NoSePuede("preparar", f"falta {fuente}")
        try:
            if not os.path.lexists(fuente):
                _soltar(propio)
            elif c.forma == "enlace duro":
                _enlazar(fuente, propio)
            else:
                _copiar(fuente, propio)
        except OSError as exc:
            raise NoSePuede("preparar", f"no pude dejar {propio} como {c.forma}: {exc}") from exc
    try:
        nombre = str(tomllib.loads(leer_texto(base / "maquina.toml")).get("nombre") or "")
        esperado = canario_juzgar.leer_esperado(p.esperado)
    except (OSError, ValueError) as exc:
        raise NoSePuede("preparar", str(exc)) from exc
    if not nombre:
        raise NoSePuede("preparar", "maquina.toml no trae nombre")
    if p.maquina and p.maquina != nombre:
        raise NoSePuede(
            "preparar",
            f"--maquina {p.maquina} no es {nombre}, el nombre de maquina.toml con el que"
            " el repo del canario nombra su archivo",
        )
    return nombre, esperado


def encargar(p: Pedido, clave: str, texto: str = TEXTO) -> str:
    try:
        datos = _motor(
            p, "encargar", "--base", str(p.base), "--clave", clave, "--repo", p.repo,
            "--entrega", "desplegado", "--texto", texto,
        )
    except RuntimeError as exc:
        raise NoSePuede("encargar", str(exc)) from exc
    if datos.get("conflicto"):
        raise NoSePuede("encargar", f"la clave {clave} ya existe con otro pedido")
    encargo = str(datos.get("encargo") or "")
    if not ID.fullmatch(encargo):
        raise NoSePuede("encargar", f"motor encargar no dio un id de encargo: {datos}")
    return encargo


def escenario_se_detiene(maquina: str, encargo: str) -> Escenario:
    """Una fase de nivel desplegado con su propia receta: el despliegue deja
    `<máquina>.txt` en `$MOTOR_CANARIO_DESPLIEGUE/se-detiene`, el checklist sale
    siempre rojo y el rollback borra esa carpeta.

    La receta es de la fase, no del `.motor.toml` del repo del canario, para que el
    encargo que termina siga con la suya. Es el camino de producción de una receta
    declarada en `.interno/fases.json` (`politica_despliegue.resolver`). Los guiones
    no cambian entre máquinas ni corridas: solo cambia `<máquina>.txt`, así que dos
    máquinas a la vez no chocan al mergear."""
    carpeta = f"canario/{SE_DETIENE}"
    destino = f'"${{{DESTINO}:?el lanzador del canario la define}}/{SE_DETIENE}"'
    return {
        "fases": [{
            "id": "uno",
            "resultado": f"{carpeta}/{maquina}.txt con el id de la corrida, desplegado con un"
                         " checklist que falla a propósito",
            "requiere_despliegue": True,
            "despliegue": {
                "desplegar": f"{carpeta}/despliega.sh",
                "checklist": f"{carpeta}/checklist.sh",
                "rollback": f"{carpeta}/rollback.sh",
            },
        }],
        "producto": {"uno": {
            f"{carpeta}/{maquina}.txt": f"{encargo}\n",
            f"{carpeta}/despliega.sh": (
                f'set -euo pipefail\ndestino={destino}\nmkdir -p "$destino"\n'
                'cp "$(dirname "$0")"/*.txt "$destino/"\n'
            ),
            f"{carpeta}/checklist.sh": (
                'echo "el canario de encargos hace fallar este checklist a propósito" >&2\nexit 1\n'
            ),
            f"{carpeta}/rollback.sh": f"set -euo pipefail\nrm -rf {destino}\n",
        }},
    }


def dejar_escenario(p: Pedido, maquina: str, encargo: str) -> None:
    ruta = p.base / ESCENARIOS / f"{encargo}.json"
    if _sale_de(p.base, ruta):
        raise NoSePuede("encargar", f"{ruta.parent} lleva fuera de {p.base} por un enlace")
    try:
        ruta.parent.mkdir(exist_ok=True)
        escribir_texto(ruta, json.dumps(escenario_se_detiene(maquina, encargo), ensure_ascii=False, indent=1))
    except OSError as exc:
        raise NoSePuede("encargar", f"no pude dejar el escenario de {encargo}: {exc}") from exc


def lanzar(p: Pedido, argv: Sequence[str]) -> dict:
    try:
        return plataforma.lanzar_separado(list(argv), RAIZ, _entorno(p), p.base / "canario-supervisor.log")
    except OSError as exc:
        raise NoSePuede("lanzar", str(exc)) from exc


def esperar(p: Pedido, supervisor: dict, vencen: Mapping[str, float]) -> dict[str, Espera]:
    """Mira a la vez cada encargo de `vencen`, con su tope en `time.monotonic()`, hasta que
    cierra, vence su tope o el supervisor muere. Al que vence lo para en ese momento, para
    que no siga corriendo mientras se espera al otro."""
    estados = dict.fromkeys(vencen, "")
    esperas: dict[str, Espera] = {}
    while len(esperas) < len(vencen):
        vivo = plataforma.estado_proc(supervisor) == "vivo"
        for encargo, vence in vencen.items():
            if encargo in esperas:
                continue
            visto = _estado(p, encargo, "esperar") or estados[encargo]
            if visto != estados[encargo]:
                _avance("esperar", f"{encargo} {visto}")
            estados[encargo] = visto
            if visto in CERRADOS:
                espera = Espera(CERRADOS[visto], visto)
            elif not vivo:
                espera = Espera("supervisor_muerto", visto)
            elif time.monotonic() >= vence:
                espera = Espera("tope", visto, _parar(p, encargo))
            else:
                continue
            esperas[encargo] = espera
            _avance("esperar", f"{encargo} terminó por {espera.fin}" + (
                f"; el canario lo paró: {espera.parada}" if espera.parada else ""
            ))
        if pendientes := [vence for encargo, vence in vencen.items() if encargo not in esperas]:
            time.sleep(max(0.0, min(p.intervalo_s, min(pendientes) - time.monotonic())))
    return esperas


def detener(supervisor: dict) -> Presencia:
    pid, inicio = int(supervisor["pid"]), str(supervisor.get("inicio") or "")
    job = str(supervisor.get("job") or "")
    plataforma.matar_proc(pid, inicio)
    plataforma.cerrar_huerfanos(pid, inicio, job)
    vence = time.monotonic() + ESPERA_GRUPO_S
    # En Windows el Job responde «incierto» mientras se cierra.
    while (presencia := plataforma.presencia_grupo(pid, inicio, job)) != "ausente":
        if time.monotonic() >= vence:
            break
        time.sleep(0.1)
    return cast(Presencia, presencia)


def _parar(p: Pedido, encargo: str) -> str:
    """El estado que deja `motor parar`, o por qué no se pudo."""
    # Repetir una clave ya usada es una retransmisión, y una retransmisión no barre.
    clave = f"{encargo}-cierre-canario-{time.time_ns()}"
    try:
        return str(_motor(p, "parar", encargo, "--base", str(p.base), "--clave", clave).get("estado") or "")
    except RuntimeError as exc:
        return f"no se pudo parar: {exc}"


def _vivos(base: Path, encargo: str) -> tuple[str, ...]:
    try:
        restos = canario_observar.procesos(base, encargo)
    except (OSError, ValueError, sqlite3.Error) as exc:
        return (f"los procesos del encargo, que no pude mirar ({type(exc).__name__}: {exc})",)
    return tuple(
        f"{r.nombre} ({'vivo' if r.queda else 'no se pudo mirar'})"
        for r in restos if r.queda is not False
    )


def _esperar_procesos(p: Pedido, encargo: str) -> tuple[str, ...]:
    vence = time.monotonic() + p.tope_cierre_s
    while (vivos := _vivos(p.base, encargo)) and time.monotonic() < vence:
        time.sleep(min(p.intervalo_s, 1.0))
    return vivos


def cerrar(p: Pedido, paradas: Mapping[str, str], supervisor: dict | None) -> tuple[Cierre, ...]:
    """Un cierre por encargo de `paradas`, en su orden, cada uno con lo que dejó la parada
    de su espera (vacío si no lo paró); el supervisor se detiene una vez."""
    cortes: list[BaseException] = []

    def intentar(fallas: list[str], que: str, hacer: Callable[[], T], si_falla: T) -> T:
        try:
            return hacer()
        except BaseException as exc:  # también un Ctrl-C: ningún encargo puede quedar vivo
            fallas.append(f"{que}: {type(exc).__name__}: {exc}")
            if not isinstance(exc, Exception):
                cortes.append(exc)
            return si_falla

    del_supervisor: list[str] = []
    presencia: Presencia = (
        intentar(del_supervisor, "detener el supervisor", lambda: detener(supervisor), "incierto")
        if supervisor else "ausente"
    )

    def cerrar_uno(encargo: str, parada: str) -> Cierre:
        fallas = list(del_supervisor)

        def estado_actual() -> str:
            return intentar(fallas, "leer el encargo", lambda: _estado(p, encargo, "cerrar"), "")

        def parar(antes: str) -> str:
            return intentar(fallas, "parar el encargo", lambda: _parar(p, encargo), antes)

        if estado_actual() not in CERRADOS:
            parada = parar(parada)
        sin_mirar = ("los procesos del encargo, que no pude esperar",)
        vivos = intentar(fallas, "esperar sus procesos", lambda: _esperar_procesos(p, encargo), sin_mirar)
        estado = estado_actual()
        if estado not in CERRADOS and not vivos:
            # Una parada no cierra mientras viva el trabajador de despliegue, y cuando él
            # termina ya no queda supervisor que barra: otra parada es ese barrido.
            parada = parar(parada)
            estado = estado_actual()
        cierre = Cierre(estado, parada, presencia, vivos, tuple(fallas))
        _avance("cerrar", (
            f"{encargo} quedó {estado or 'sin leer'}; el grupo del supervisor, {presencia}"
            + (f"; siguen {', '.join(vivos)}" if vivos else "")
            + (f"; fallaron {'; '.join(fallas)}" if fallas else "")
            + ("" if cierre.listo else ": no mido ni borro nada")
        ))
        return cierre

    cierres = tuple(cerrar_uno(encargo, parada) for encargo, parada in paradas.items())
    if cortes:
        raise cortes[0]
    return cierres


def _sha_desplegado(viva: Path) -> str:
    destino = canario_observar._json_o_vacio(viva / "instalacion.json").get("destino")
    return str(destino.get("codigo") or "") if isinstance(destino, dict) else ""


def medir(
    p: Pedido, encargo: str, detencion: str, maquina: str, gh: canario_observar.LectorPR,
    esperado: canario_juzgar.Esperado,
) -> tuple[canario_juzgar.Fila, ...]:
    obs = canario_observar.observar(
        p.base, encargo, gh,
        hora=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        arbol=RAIZ,
        sha_desplegado=_sha_desplegado(p.base_viva),
        archivo=(p.despliegue / "corridas" / f"{maquina}.txt", encargo),
        detencion=detencion,
        archivo_detencion=p.despliegue / SE_DETIENE / f"{maquina}.txt" if detencion else None,
    )
    if obs.pliegue is None:
        raise NoSePuede("medir", f"el encargo {encargo} no está en el registro")
    return canario_juzgar.juzgar(obs, esperado)


def _borrar_rama(repo: str, rama: str) -> Borrado:
    nombre = f"la rama remota {rama}"
    try:
        if canario_observar._rama_existe(repo, rama) is False:
            return Borrado(nombre, "no estaba")
        remoto = resolver_repositorio(repo).remoto
        with tempfile.TemporaryDirectory() as vacio:
            # `git push` pide un repositorio; uno vacío no aporta nada al borrado.
            for argv in (
                ["git", "init", "-q", "--bare", vacio],
                ["git", "--git-dir", vacio, "push", "-q", remoto, "--delete", rama],
            ):
                hecho = subprocess.run(
                    argv, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                    timeout=2 * canario_observar.ESPERA_GIT_S, check=False,
                )
                if hecho.returncode:
                    return Borrado(nombre, "falló", hecho.stderr.strip()[-300:])
        queda = canario_observar._rama_existe(repo, rama)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return Borrado(nombre, "falló", f"{type(exc).__name__}: {exc}")
    if queda is None:
        return Borrado(nombre, "falló", "git push --delete salió bien y no pude confirmar que ya no está")
    if queda:
        return Borrado(nombre, "falló", "git push --delete salió bien y la rama sigue")
    return Borrado(nombre, "borrado")


def _borrar_ruta(ruta: Path, nombre: str) -> Borrado:
    if not os.path.lexists(ruta):
        return Borrado(nombre, "no estaba")
    try:
        quitar(ruta)
    except OSError as exc:
        return Borrado(nombre, "falló", str(exc))
    return Borrado(nombre, "borrado")


def _ramas(base: Path, encargo: str) -> list[str]:
    reg = Registro(base)
    try:
        e = reg.fold(encargo)
    finally:
        reg.close()
    return sorted({f.rama for f in e.fases if f.rama}) if e is not None else []


def _flota(base: Path) -> Path | None:
    home = str(canario_observar._json_o_vacio(base / "flota-home.json").get("home") or "")
    return Path(home) if home else None


def _cerrados(base: Path) -> list[str]:
    reg = Registro(base)
    try:
        return [i for i in reg.ids() if (e := reg.fold(i)) is not None and e.estado in CERRADOS]
    finally:
        reg.close()


def _ramas_remotas(repo: str) -> frozenset[str] | None:
    """Las ramas encargo/* del repo del canario; None si no se pudieron leer."""
    try:
        remoto = resolver_repositorio(repo).remoto
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    salida = canario_observar._git(
        "ls-remote", "--heads", "--", remoto, "refs/heads/encargo/*", espera=2 * canario_observar.ESPERA_GIT_S,
    )
    if salida is None:
        return None
    return frozenset(
        linea.split("\t", 1)[1].removeprefix("refs/heads/")
        for linea in salida.splitlines() if "\t" in linea
    )


def recoger(p: Pedido, salvo: Sequence[str] = ()) -> tuple[Borrado, ...]:
    """Limpia lo que dejaron las corridas anteriores: cada encargo de la base del canario,
    salvo los de `salvo`, que ya cerró, que aún deja algo y del que no queda ningún proceso vivo.

    Corre después del cierre y no al preparar: el supervisor de esta corrida vuelve a
    crear `espacio/<id>` vacío al barrer un encargo cerrado de la anterior."""
    if not (p.base / "registro.sqlite").is_file():
        return ()
    try:
        cerrados = [i for i in _cerrados(p.base) if i not in salvo]
    except (OSError, ValueError, sqlite3.Error) as exc:
        return (Borrado(
            "los restos de las corridas anteriores", "falló",
            f"no pude leer el registro ({type(exc).__name__}: {exc})",
        ),)
    remotas, flota = _ramas_remotas(p.repo), _flota(p.base)
    borrados: list[Borrado] = []
    if remotas is None and cerrados:
        borrados.append(Borrado(
            "las ramas remotas de las corridas anteriores", "falló", "no pude leerlas del repo del canario",
        ))
    for encargo in cerrados:
        locales = [
            p.base / "espacio" / encargo, p.base / "ejecucion" / encargo,
            p.base / ESCENARIOS / f"{encargo}.json",
        ]
        if flota:
            locales.append(flota / "trabajos" / f"{encargo}.json")
        if f"encargo/{encargo}" not in (remotas or ()) and not any(os.path.lexists(r) for r in locales):
            continue
        if vivos := _vivos(p.base, encargo):
            borrados.append(Borrado(f"los restos de {encargo}", "falló", f"siguen {', '.join(vivos)}"))
            continue
        _avance("limpiar", f"recojo lo que dejó {encargo}, cerrado en una corrida anterior")
        borrados += limpiar(p, encargo)
    return tuple(borrados)


def limpiar(p: Pedido, encargo: str) -> tuple[Borrado, ...]:
    if not ID.fullmatch(encargo):
        borrados = [Borrado(f"los restos de {encargo!r}", "falló", "no es un id de encargo: no borro nada")]
    else:
        # El nombre con que el motor crea la rama de un encargo que no pide otra.
        suya = f"encargo/{encargo}"
        try:
            ramas = _ramas(p.base, encargo)
        except (OSError, ValueError, sqlite3.Error) as exc:
            borrados = [Borrado(
                "las ramas remotas del encargo", "falló",
                f"no pude leerlas del registro ({type(exc).__name__}: {exc})",
            )]
        else:
            borrados = [
                _borrar_rama(p.repo, rama) if rama == suya
                else Borrado(f"la rama remota {rama}", "falló", f"no es {suya}: no la borro")
                for rama in ramas
            ]
        escenario = p.base / ESCENARIOS / f"{encargo}.json"
        propios = [(p.base / d / encargo, d, f"{d}/{encargo}") for d in ("espacio", "ejecucion")]
        if os.path.lexists(escenario):
            propios.append((escenario, ESCENARIOS, f"{ESCENARIOS}/{encargo}.json"))
        borrados += [
            Borrado(nombre, "falló", f"{p.base / d} lleva fuera de la base: no lo borro")
            if _sale_de(p.base, ruta) else _borrar_ruta(ruta, nombre)
            for ruta, d, nombre in propios
        ]
        flota = _flota(p.base)
        trabajo = f"trabajos/{encargo}.json de la flota"
        borrados.append(
            _borrar_ruta(flota / "trabajos" / f"{encargo}.json", trabajo) if flota
            else Borrado(trabajo, "falló", "flota-home.json no dice dónde está la flota")
        )
    for b in borrados:
        _avance("limpiar", f"{b.resto}: {b.resultado}" + (f" ({b.causa})" if b.causa else ""))
    return tuple(borrados)


def _traza() -> None:
    _decir(traceback.format_exc().rstrip(), sys.stderr)


def _fallo(exc: Exception) -> Fallo:
    if isinstance(exc, NoSePuede):
        fallo = Fallo(exc.paso, exc.causa)
    else:
        _traza()
        fallo = Fallo(None, f"{type(exc).__name__}: {exc}")
    _avance(fallo.paso, f"no pude seguir: {fallo.causa}")
    return fallo


def correr(p: Pedido, gh: canario_observar.LectorPR, supervisor: Sequence[str] | None = None) -> Informe:
    maquina, esperado = preparar(p)
    version = canario_observar._git_head(RAIZ)
    if not version:
        raise NoSePuede("encargar", f"no pude leer el SHA de {RAIZ}")
    clave = f"canario-{version[:12]}-{maquina}-{p.corrida}"
    _avance("preparar", f"base {p.base} lista; {maquina} en {version[:12]}")

    def vence(arranque: float, minutos: int) -> float:
        return arranque + (p.tope_s if p.tope_s is not None else minutos * 60.0)

    inicio = time.monotonic()
    encargo = encargar(p, clave)
    vencen = {encargo: vence(inicio, esperado.topes.minutos_termina)}
    clave_detencion = f"{clave}-{SE_DETIENE}"
    detencion = ""
    proceso: dict | None = None
    esperas: dict[str, Espera] = {}
    fallo: Fallo | None = None
    try:
        _avance("encargar", f"{encargo} con la clave {clave}")
        if p.se_detiene:
            arranque = time.monotonic()
            detencion = encargar(p, clave_detencion, TEXTO_SE_DETIENE)
            vencen[detencion] = vence(arranque, esperado.topes.minutos_se_detiene)
            dejar_escenario(p, maquina, detencion)
            _avance("encargar", f"{detencion} con la clave {clave_detencion}: su checklist falla a propósito")
        proceso = lanzar(p, supervisor or [
            sys.executable, "-B", "-m", "motor", "supervisor", "--base", str(p.base), "--agente", "ensayo",
        ])
        _avance("lanzar", f"supervisor {proceso['pid']}")
        esperas = esperar(p, proceso, vencen)
    except Exception as exc:
        fallo = _fallo(exc)
    finally:
        segundos = int(time.monotonic() - inicio)
        paradas = {e: esperas[e].parada if e in esperas else "" for e in vencen}
        cierre, *otros = cerrar(p, paradas, proceso)
    cierres = (cierre, *otros)
    espera, espera_detencion = esperas.get(encargo), esperas.get(detencion)
    fallas = tuple(dict.fromkeys(f for c in cierres for f in c.fallas))
    if fallo is None and fallas:
        fallo = Fallo("cerrar", "; ".join(fallas))
    filas: tuple[canario_juzgar.Fila, ...] = ()
    limpieza: tuple[Borrado, ...] = ()
    recogida: tuple[Borrado, ...] = ()
    if all(c.listo for c in cierres):
        if fallo is None:
            try:
                filas = medir(p, encargo, detencion, maquina, gh, esperado)
            except Exception as exc:
                fallo = _fallo(exc)
        limpieza = tuple(b for e in (encargo, detencion) if e for b in limpiar(p, e))
        recogida = recoger(p, salvo=(encargo, detencion))
    seguimiento = (
        Seguimiento(
            clave_detencion, detencion, espera_detencion.fin if espera_detencion else None,
            espera_detencion.estado if espera_detencion else "", otros[0],
        )
        if detencion else None
    )
    return Informe(
        maquina, version, p.corrida, clave, encargo, espera.fin if espera else None,
        espera.estado if espera else "", segundos, cierre, filas, limpieza, fallo, recogida,
        seguimiento,
    )


def _guardar(p: Pedido, salida: dict) -> None:
    """Deja la salida en la base del canario, para leerla aunque se haya perdido la sesión."""
    if not _aparte(p.base, p.base_viva) or not p.base.is_dir():
        return
    destino = p.base / INFORME
    try:
        # Un nombre nuevo y exclusivo: uno fijo podría ser ya un enlace a la base viva.
        fd, nombre = tempfile.mkstemp(prefix=f"{INFORME}.", dir=p.base)
        os.close(fd)
        temporal = Path(nombre)
        try:
            escribir_texto(temporal, json.dumps(salida, ensure_ascii=False, indent=1))
            os.replace(temporal, destino)
        finally:
            temporal.unlink(missing_ok=True)
    except OSError as exc:
        _avance(None, f"no pude dejar el informe en {destino}: {exc}")


def _ruta(texto: str) -> Path:
    return Path(texto).expanduser().resolve()


def _positivo(texto: str) -> float:
    valor = float(texto)
    if not valor > 0:
        raise argparse.ArgumentTypeError(f"{texto} no es un número mayor que 0")
    return valor


def _no_negativo(texto: str) -> float:
    valor = float(texto)
    if not valor >= 0:
        raise argparse.ArgumentTypeError(f"{texto} no es un número mayor o igual que 0")
    return valor


def main(
    argv: list[str] | None = None, gh: canario_observar.LectorPR | None = None,
    supervisor: Sequence[str] | None = None,
) -> int:
    for flujo in (sys.stdout, sys.stderr):
        if isinstance(flujo, io.TextIOWrapper):
            flujo.reconfigure(encoding="utf-8")
    casa = Path.home()
    a = argparse.ArgumentParser(
        description="Corre el canario de encargos en esta máquina y termina con su tabla.",
        epilog="La salida trae ids, ramas y el repo: no la mandes a un log público.",
    )
    a.add_argument("--repo", required=True, help="el repo del canario")
    a.add_argument("--base", type=_ruta, default=str(casa / ".motor-canario"))
    a.add_argument("--base-viva", type=_ruta, default=str(casa / ".motor-encargos"))
    a.add_argument("--despliegue", type=_ruta, default=str(casa / "motor-canario-despliegue"))
    a.add_argument("--corrida", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    a.add_argument("--maquina", default="")
    a.add_argument("--tope-min", type=_no_negativo)
    a.add_argument("--tope-cierre-s", type=_no_negativo, default=120.0)
    a.add_argument("--intervalo-s", type=_positivo, default=60.0)
    a.add_argument("--esperado", type=_ruta, default=str(canario_juzgar.ESPERADO))
    a.add_argument(
        "--sin-se-detiene", action="store_true",
        help="no corre el encargo cuyo checklist falla a propósito; la fila se detiene queda sin medir",
    )
    args = a.parse_args(argv)
    if not CORRIDA.fullmatch(args.corrida):
        a.error("--corrida solo lleva letras, dígitos, punto, guion y guion bajo")
    p = Pedido(
        repo=args.repo, base=args.base, base_viva=args.base_viva, despliegue=args.despliegue,
        corrida=args.corrida, maquina=args.maquina,
        tope_s=None if args.tope_min is None else args.tope_min * 60.0,
        intervalo_s=args.intervalo_s, esperado=args.esperado, tope_cierre_s=args.tope_cierre_s,
        se_detiene=not args.sin_se_detiene,
    )
    with _sin_colgar():
        try:
            informe = correr(p, gh or GhGitHub(), supervisor)
            salida = informe.json()
            if informe.filas:
                _decir(tabla(informe.filas), sys.stderr)
        except NoSePuede as exc:
            _avance(exc.paso, f"no pude seguir: {exc.causa}")
            salida = {"corrida": p.corrida, "paso": exc.paso, "error": exc.causa, "codigo": 2}
        except Exception as exc:  # el 1 queda para una tabla que no coincide, siempre con su informe
            _traza()
            error = f"{type(exc).__name__}: {exc}"
            salida = {"corrida": p.corrida, "paso": "", "error": error, "codigo": 2}
        _guardar(p, salida)
        _decir(json.dumps(salida, ensure_ascii=False), sys.stdout)
        return int(salida["codigo"])


if __name__ == "__main__":
    raise SystemExit(main())
