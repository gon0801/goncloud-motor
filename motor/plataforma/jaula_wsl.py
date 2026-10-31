"""Lanzar un agente dentro de WSL con la jaula de bubblewrap, desde el supervisor en Windows.

Este lado arma los argv y habla con `wsl.exe`; el trabajo dentro de la distro lo hace el archivo
`jaula_linux.py`, que viaja entero como fuente (`python3 -I -S -c <fuente> <orden> <json>`) porque
en la distro no está el motor. Cada orden es un argv de `wsl.exe --exec`: nada pasa por un shell.

- `guardian`: lee la credencial de Claude de la distro, arma la jaula, la lanza y la cierra entera
  si el padre muere, llega una señal o vence el plazo. Deja `jaula-wsl.json` en el directorio de
  control con las identidades del guardián y de bwrap;
- `cerrar` / `estado`: matan o consultan lo que describe ese archivo. Es el cierre explícito de
  `parar` y `cancelar`: no depende de que el guardián note que `wsl.exe` murió;
- `disponible`: vacío si la distro puede correr la jaula y el agente; si no, la causa.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from . import jaula_linux
from .rutas_wsl import a_wsl, argv_a_wsl, texto_a_wsl, valores_a_wsl

DISTRO_VAR = "MOTOR_WSL_DISTRO"
ARCHIVO_CONTROL = jaula_linux.ARCHIVO_CONTROL
MECANISMO = "wsl-bwrap"
_PERMITIDAS = (
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP",
    "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "PROGRAMFILES",
    "PROGRAMFILES(X86)", "PROGRAMW6432",
)
_CACHE_DISPONIBLE: dict = {}
VIGENCIA_DISPONIBLE_S = 60.0


def distro() -> str:
    """La distro donde corren los agentes. Vacía es la distro por omisión de WSL."""
    return os.environ.get(DISTRO_VAR, "").strip()


def fuente() -> str:
    """El código de la distro, en ASCII: va en la línea de comandos de `wsl.exe` y no se
    arriesga a que un acento cambie de codificación por el camino. Cada carácter no ASCII
    queda como su escape, que dentro de un literal es el mismo carácter."""
    texto = Path(jaula_linux.__file__).read_text(encoding="utf-8")
    return texto.encode("ascii", "backslashreplace").decode("ascii")


def argv_orden(distribucion: str, orden: str, *args: str) -> list:
    base = ["wsl.exe", *(["-d", distribucion] if distribucion else [])]
    return [
        *base, "--exec", "/usr/bin/python3", "-I", "-S", "-c", fuente(), orden, *args,
    ]


def entorno_windows(entorno: dict | None = None) -> dict:
    """Lo que necesita `wsl.exe` y nada más: el agente no recibe este entorno, el suyo se lo
    arma el guardián dentro de la distro."""
    entorno = os.environ if entorno is None else entorno
    return {k: v for k, v in entorno.items() if k.upper() in _PERMITIDAS}


def jaula_de(
    espacio: Path,
    runtime: Path,
    *,
    lectura: bool = False,
    lecturas: tuple = (),
    agente: str = "",
    red: str = jaula_linux.RED_ABIERTA,
) -> dict:
    """La concesión de un encargo, en rutas de la distro y lista para ir en JSON."""
    return {
        "espacio": a_wsl(str(espacio)),
        "runtime": a_wsl(str(runtime)),
        "lectura": bool(lectura),
        "lecturas": [a_wsl(str(p)) for p in lecturas],
        "red": red,
        "credencial": "claude" if agente == "claude" else "",
        "distro": distro(),
    }


def cmd_a_wsl(cmd: list) -> list:
    """El comando del agente con sus rutas de Windows en rutas de la distro, también las que
    van dentro de una frase (`Lee el pedido en C:\\...\\prompt.md`)."""
    return [
        traducido if traducido != x else texto_a_wsl(x)
        for x, traducido in zip(cmd, argv_a_wsl(cmd))
    ]


def spec_guardian(
    jaula: dict, cmd: list, env: dict, *, control: Path | None = None,
    cwd: Path | None = None, plazo: float | None = None,
) -> dict:
    spec = {
        "jaula": {
            k: jaula[k] for k in ("espacio", "runtime", "lectura", "lecturas", "red")
        },
        "credencial": jaula.get("credencial", ""),
        "cmd": cmd_a_wsl(cmd),
        "env": valores_a_wsl({k: v for k, v in env.items() if k.startswith("MOTOR_")}),
        "cwd": a_wsl(str(cwd)) if cwd else None,
        "control": a_wsl(str(control)) if control else None,
        "plazo": plazo,
    }
    return spec


def argv_guardian(jaula: dict, cmd: list, env: dict, **opciones) -> list:
    spec = spec_guardian(jaula, cmd, env, **opciones)
    return argv_orden(jaula.get("distro", ""), "guardian", json.dumps(spec))


def control_de(directorio: Path) -> dict | None:
    try:
        return json.loads((Path(directorio) / ARCHIVO_CONTROL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _orden(distribucion: str, orden: str, *args: str, correr=subprocess.run, plazo=30) -> dict | None:
    banderas = {"creationflags": 0x08000000} if os.name == "nt" else {}
    try:
        hecho = correr(
            argv_orden(distribucion, orden, *args), stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=plazo, env=entorno_windows(), **banderas,
        )
        return json.loads((hecho.stdout or "").strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def estado_en_distro(directorio: Path, distribucion: str = "", *, correr=subprocess.run) -> str:
    """`vivo`, `ausente` o `incierto` para lo que la jaula de este intento dejó en la distro.
    Un archivo de control cerrado o sin escribir es `ausente`: no hay nada que preguntar."""
    control = control_de(directorio)
    if not control or control.get("estado") == "cerrado":
        return "ausente"
    respuesta = _orden(distribucion, "estado", json.dumps(control), correr=correr)
    return _resultado(directorio, control, respuesta)


def _resultado(directorio: Path, control: dict, respuesta: dict | None) -> str:
    if respuesta is None:
        return "incierto"
    if respuesta.get("vivo"):
        return "vivo"
    try:
        # Ya no hay nada: que la siguiente vuelta del supervisor no vuelva a preguntarle a WSL.
        ruta = Path(directorio) / ARCHIVO_CONTROL
        tmp = ruta.with_name(ruta.name + ".tmp")
        tmp.write_text(json.dumps({**control, "estado": "cerrado"}), encoding="utf-8")
        os.replace(tmp, ruta)
    except OSError:
        pass
    return "ausente"


def cerrar_en_distro(directorio: Path, distribucion: str = "", *, correr=subprocess.run) -> str:
    """Mata dentro de la distro la jaula y el guardián del intento. Devuelve el estado que
    queda: `ausente` si no hay nada vivo, `vivo` si algo resistió, `incierto` si no se pudo
    preguntar. Se puede repetir: sobre lo ya cerrado no hace nada."""
    control = control_de(directorio)
    if not control or control.get("estado") == "cerrado":
        return "ausente"
    respuesta = _orden(distribucion, "cerrar", json.dumps(control), correr=correr)
    return _resultado(directorio, control, respuesta)


def causa_no_disponible(
    agente: str, distribucion: str | None = None, *, correr=subprocess.run,
    ahora=time.monotonic,
) -> str:
    """Vacío si la distro corre la jaula y el agente. La respuesta se recuerda un minuto: el
    supervisor la pregunta en cada vuelta y arrancar `wsl.exe` no es gratis."""
    distribucion = distro() if distribucion is None else distribucion
    clave = (distribucion, agente)
    previa = _CACHE_DISPONIBLE.get(clave)
    if previa and ahora() - previa[0] < VIGENCIA_DISPONIBLE_S:
        return previa[1]
    respuesta = _orden(distribucion, "disponible", agente, correr=correr, plazo=60)
    causa = (
        "no se pudo consultar a WSL (wsl.exe no respondio)"
        if respuesta is None
        else str(respuesta.get("causa") or "")
    )
    _CACHE_DISPONIBLE[clave] = (ahora(), causa)
    return causa
