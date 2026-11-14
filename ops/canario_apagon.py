"""El encargo al que el canario de encargos le corta su propio supervisor.

El lanzador lo encarga junto a los otros dos, le deja su escenario
(`escenario_apagon`) y su carpeta `canario-apagon/<id>/` (`agente_ensayo.APAGON`),
fuera de su espacio. Ahí anotan su agente y su receta cada vez que corren. Su
responsable, ya escrito el producto, espera la compuerta del turno; su receta de
despliegue, la de la receta. Cuando una espera, `cortar_lo_que_espera` mata el
supervisor, mira si el proceso que esperaba sigue vivo, lanza otro supervisor
igual y suelta la compuerta. Cada corte queda en `cortes.json`. Las filas "no
pierde" y "no repite" salen de esa carpeta, del registro y de GitHub.

El corte mata solo al supervisor: el turno y el trabajador de despliegue que lanzó
viven en su propia sesión y siguen. Mide una caída del supervisor con el proceso en
curso vivo, no un corte de energía, que mataría también a esos dos. Reiniciar la
máquina mataría encargos reales.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import get_args

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from motor import plataforma  # noqa: E402
from motor.io import escribir_texto, leer_json, leer_texto  # noqa: E402
from ops.agente_ensayo import (  # noqa: E402
    APAGON,
    CORTES,
    ESPERA_COMPUERTA_S,
    INVOCACIONES,
    Escenario,
    esperando,
    suelta,
)
from ops.canario_observacion import Corte, Momento  # noqa: E402

ENTORNO = "MOTOR_CANARIO_APAGON"
"""La carpeta del encargo con cortes, para su receta: la lanza el trabajador de despliegue,
que no sabe de qué encargo es."""
DESTINO = "MOTOR_CANARIO_DESPLIEGUE"
CARPETA = "apagon"
"""Donde viven su producto en el repo del canario (`canario/apagon/`) y su despliegue."""
TEXTO = "Canario: escribe el id de esta corrida en canario/apagon/<máquina>.txt mientras el canario corta su supervisor."
EN_WINDOWS = (
    "en Windows el corte no es el mismo: plataforma.matar_proc cierra el árbol del supervisor"
    " por ppid (windows._matar_arbol_confirmado), y con él al turno y al trabajador de despliegue"
    " que lanzó, que en POSIX viven en su propia sesión; sin medir en la PC"
)


def por_que_no_corta(pedido: bool, sistema: str) -> str:
    """Por qué la corrida no hace el encargo con cortes; vacío si lo hace."""
    if sistema == "nt":
        return EN_WINDOWS
    return "" if pedido else "la corrida se lanzó con --sin-apagon"


def carpeta(base: Path, encargo: str) -> Path:
    return base / APAGON / encargo


def por_que_no_vuelve_a_cortar(corrida: str, carpeta_: Path, cerrado: bool) -> str:
    """Por qué la corrida repetida no corta otra vez el encargo con cortes que retoma; vacío
    si es la primera vez. Lo que dejó la anterior (compuertas que esperan y ya se soltaron,
    sus cortes y sus invocaciones) no se puede volver a medir como un corte nuevo."""
    if cerrado:
        que = "ya cerró"
    elif carpeta_.is_dir() and any(carpeta_.iterdir()):
        que = "su carpeta ya trae lo que anotó"
    else:
        return ""
    return (
        f"la corrida {corrida} ya hizo el encargo con cortes {carpeta_.name}, y {que}:"
        " no se corta dos veces; lanza otra --corrida"
    )


def _receta(*, anota: str, cuerpo: str) -> str:
    variable = f'"${{{ENTORNO}:?el lanzador del canario la define}}"'
    destino = f'"${{{DESTINO}:?el lanzador del canario la define}}/{CARPETA}"'
    return (
        f"set -euo pipefail\ncarpeta={variable}\ndestino={destino}\n"
        f'echo {anota} >> "$carpeta/{INVOCACIONES}"\n{cuerpo}'
    )


def escenario_apagon(maquina: str, encargo: str) -> Escenario:
    """Una fase de nivel desplegado con su propia receta, como la del que termina: el
    despliegue copia `<máquina>.txt` a `$MOTOR_CANARIO_DESPLIEGUE/apagon`. Antes de copiar
    espera la compuerta de la receta.

    Los guiones no cambian entre máquinas ni corridas, para que dos máquinas a la vez no
    choquen al mergear: la carpeta del encargo les llega por `MOTOR_CANARIO_APAGON`."""
    producto = f"canario/{CARPETA}"
    marca, liberada = esperando("receta"), suelta("receta")
    espera = (
        f'echo $$ > "$carpeta/.{marca}"\nmv "$carpeta/.{marca}" "$carpeta/{marca}"\n'
        f'i=0\nwhile [ ! -e "$carpeta/{liberada}" ] && [ "$i" -lt {ESPERA_COMPUERTA_S} ]; do'
        ' sleep 1; i=$((i + 1)); done\n'
    )
    return {
        "fases": [{
            "id": "uno",
            "resultado": f"{producto}/{maquina}.txt con el id de la corrida, desplegado mientras el"
                         " canario corta su supervisor",
            "requiere_despliegue": True,
            "despliegue": {
                "desplegar": f"{producto}/despliega.sh",
                "checklist": f"{producto}/checklist.sh",
                "rollback": f"{producto}/rollback.sh",
            },
        }],
        "producto": {"uno": {
            f"{producto}/{maquina}.txt": f"{encargo}\n",
            f"{producto}/despliega.sh": _receta(anota="desplegar", cuerpo=(
                espera + 'mkdir -p "$destino"\ncp "$(dirname "$0")"/*.txt "$destino/"\n'
            )),
            f"{producto}/checklist.sh": _receta(anota="checklist", cuerpo='test -d "$destino"\n'),
            f"{producto}/rollback.sh": _receta(anota="rollback", cuerpo='rm -rf "$destino"\n'),
        }},
    }


def _esperando(carpeta_: Path, momento: Momento) -> int | None:
    """El pid de quien espera la compuerta de `momento`; None si nadie la espera todavía."""
    try:
        return int(leer_texto(carpeta_ / esperando(momento)).strip())
    except (OSError, ValueError):
        return None


def _anotar(carpeta_: Path, corte: Corte) -> None:
    ruta = carpeta_ / CORTES
    try:
        previos = leer_json(ruta)
    except FileNotFoundError:
        previos = []
    temporal = ruta.with_name(f".{CORTES}")
    escribir_texto(temporal, json.dumps([*previos, {"momento": corte.momento, "sobrevivio": corte.sobrevivio}]))
    os.replace(temporal, ruta)


def cortar_lo_que_espera(
    carpeta_: Path, cortados: set[Momento], detener: Callable[[], str], relanzar: Callable[[], None],
) -> tuple[Corte, ...]:
    """Un corte por cada compuerta que ya espera y no se cortó: `detener` mata el supervisor
    y dice qué quedó de su grupo, se mira si el que esperaba sigue vivo, `relanzar` lanza
    otro igual y la compuerta se suelta. Se suelta aunque el corte se caiga: si no, quien
    espera se quedaría parado hasta su tope."""
    cortes = []
    for momento in get_args(Momento):
        if momento in cortados or (pid := _esperando(carpeta_, momento)) is None:
            continue
        cortados.add(momento)
        ident = {"pid": pid, "inicio": plataforma.hora_inicio(pid), "boot_id": plataforma.boot_id()}
        try:
            presencia = detener()
            if presencia != "ausente":
                raise RuntimeError(f"el supervisor no murió al cortar en el {momento}: su grupo quedó {presencia}")
            sobrevivio = plataforma.estado_proc(ident) == "vivo" if ident["inicio"] else None
            relanzar()
        finally:
            (carpeta_ / suelta(momento)).touch()
        corte = Corte(momento, sobrevivio)
        _anotar(carpeta_, corte)
        cortes.append(corte)
    return tuple(cortes)
