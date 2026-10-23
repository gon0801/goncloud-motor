"""Tareas programadas que apuntan a carpetas que el motor borra.

Una receta de despliegue que deja un plist, un crontab o una tarea de Windows apuntando a su copia
temporal rompe esa tarea el día que `limpia` borra la copia. Aquí se lee lo que hay programado y se
busca una ruta como texto; `effects.deploy` y `motor programadas` deciden qué hacer con lo que sale.
"""

from __future__ import annotations

import glob
import os
import plistlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.parsers.expat import ExpatError

import tomllib

from . import plataforma
from .plataforma.windows import argv_shell

TOPE_FUENTE_S = 30.0
ARCHIVO_EXTRAS = "programadas.toml"
CLAVES_EXTRAS = ("archivos", "comandos")

TMP, WT, INTENTOS, PSTACK = "tmp", "wt", "intentos", "pstack"
# Lo que `limpia` y `purga_registros` borran dentro del home del motor.
CARPETAS_DEL_MOTOR = (TMP, WT, INTENTOS, PSTACK)

SCHTASKS = "schtasks /query /fo csv /v"
CRONTAB = "crontab -l"

Lectura = tuple[dict[str, str], list[str], dict[str, str], dict[str, str]]


# --------------------------------------------------------------------------- contiene


def _home_texto(home: str | Path) -> str:
    return re.sub(r"\\+", "/", str(home)).rstrip("/")


def normaliza(s: str, home: str | Path) -> str:
    """Una ruta o un texto en una sola forma: sin variables, con `/` y sin separadores repetidos."""
    h = _home_texto(home)
    local = f"{h}/AppData/Local"
    s = re.sub(r"%USERPROFILE%", lambda _: h, s, flags=re.IGNORECASE)
    s = re.sub(r"%LOCALAPPDATA%", lambda _: local, s, flags=re.IGNORECASE)
    s = re.sub(r"\$\{HOME\}|\$HOME(?![\w])", lambda _: h, s)
    s = re.sub(r"(?<![\w~])~(?=[/\\]|$|[\s\"'])", lambda _: h, s)
    s = re.sub(r"\\+", "/", s)
    s = re.sub(r"(?<![\w/])/{1,2}([A-Za-z])/", lambda mo: f"{mo.group(1)}:/", s)
    return re.sub(r"/{2,}", "/", s)


def contiene(texto: str, ruta: str | Path, home: str | Path) -> bool:
    """¿`texto` nombra `ruta`? Pura: no toca el disco. La ruta debe empezar y acabar en un límite de
    nombre, para que `despliega-otro-A` no cuente como `despliega-A`."""
    r = normaliza(str(ruta), home).rstrip("/")
    if not r:
        return False
    t = normaliza(texto, home)
    if re.match(r"[A-Za-z]:/", r):
        r, t = r.casefold(), t.casefold()
    patron = rf"(?<![\w.\-]){re.escape(r)}(?![\w.\-])"
    return re.search(patron, t) is not None


# --------------------------------------------------------------------------- fuentes


def _cadenas(x: object) -> list[str]:
    if isinstance(x, str):
        return [x]
    if isinstance(x, dict):
        return [s for v in x.values() for s in _cadenas(v)]
    if isinstance(x, (list, tuple)):
        return [s for v in x for s in _cadenas(v)]
    return []


def _texto_plist(datos: bytes) -> str:
    try:
        return "\n".join(_cadenas(plistlib.loads(datos)))
    except (
        plistlib.InvalidFileException,
        ExpatError,
        ValueError,
        OSError,
        SyntaxError,
    ):
        return datos.decode("utf-8", errors="replace")


def _texto_archivo(p: Path) -> str:
    datos = p.read_bytes()
    if p.suffix == ".plist":
        return _texto_plist(datos)
    return datos.decode("utf-8", errors="replace")


def ejecuta(
    cmd: str, bash: str = "", tope: float | None = None
) -> subprocess.CompletedProcess:
    """Corre un comando con tope. Lanza `subprocess.TimeoutExpired` si se pasa y `OSError` si no arranca."""
    tope = TOPE_FUENTE_S if tope is None else tope
    if os.name == "nt":
        argv, shell, exe = argv_shell(cmd, bash)
    else:
        argv, shell, exe = cmd, True, "/bin/bash"
    with tempfile.TemporaryFile() as o, tempfile.TemporaryFile() as e:
        p = subprocess.Popen(
            argv,
            shell=shell,
            executable=exe,
            stdin=subprocess.DEVNULL,
            stdout=o,
            stderr=e,
            **plataforma.kwargs_grupo(),
        )
        try:
            p.wait(timeout=tope)
        except subprocess.TimeoutExpired:
            plataforma.matar_grupo(p)
            p.wait(timeout=5)
            raise
        o.seek(0)
        e.seek(0)
        return subprocess.CompletedProcess(
            cmd,
            p.returncode,
            o.read().decode("utf-8", errors="replace"),
            e.read().decode("utf-8", errors="replace"),
        )


def _carpeta_plist(d: Path, tareas, negados) -> None:
    for p in sorted(d.glob("*.plist")):
        try:
            tareas[str(p)] = _texto_plist(p.read_bytes())
        except PermissionError as e:
            negados[str(p)] = f"sin permiso de lectura: {e.strerror or e}"
        except OSError as e:
            negados[str(p)] = str(e)


def _dirs_mac(home: Path) -> list[Path]:
    return [
        home / "Library" / "LaunchAgents",
        Path("/Library/LaunchAgents"),
        Path("/Library/LaunchDaemons"),
    ]


def _lee_carpeta(d: Path, tareas, leidas, sin_leer, negados) -> None:
    nombre = str(d)
    try:
        os.listdir(d)
    except FileNotFoundError:
        leidas.append(nombre)  # una carpeta que no existe no guarda tareas
        return
    except OSError as e:
        sin_leer[nombre] = f"no pude listar la carpeta: {e.strerror or e}"
        return
    leidas.append(nombre)
    _carpeta_plist(d, tareas, negados)


def _lee_crontab(tareas, leidas, sin_leer, bash) -> None:
    try:
        p = ejecuta(CRONTAB, bash)
    except subprocess.TimeoutExpired:
        sin_leer[CRONTAB] = f"pasó de {TOPE_FUENTE_S:g} s"
        return
    except OSError as e:
        sin_leer[CRONTAB] = f"no pude correrlo: {e}"
        return
    salida = p.stdout + p.stderr
    if p.returncode == 0:
        leidas.append(CRONTAB)
        lineas = [
            x
            for x in p.stdout.splitlines()
            if x.strip() and not x.lstrip().startswith("#")
        ]
        for i, linea in enumerate(lineas, 1):
            tareas[f"crontab:{i}"] = linea
    elif "no crontab for" in salida:
        leidas.append(CRONTAB)
    else:
        sin_leer[CRONTAB] = f"salió {p.returncode}: {salida.strip()[-200:]}"


_FILA_CSV = re.compile(r'^"[^"]*","([^"]*)"')


def filas_schtasks(salida: str) -> list[str]:
    """Las filas de `schtasks /fo csv /v`, una por tarea. No se usa `csv`: la columna «Task To Run»
    trae comillas sin duplicar y descuadra las columnas; cada fila empieza con `"<equipo>","\\<tarea>"`."""
    filas: list[str] = []
    for linea in salida.splitlines():
        if _FILA_CSV.match(linea):
            if not linea.startswith('"HostName"'):
                filas.append(linea)
        elif filas and linea.strip() and not linea.lstrip().upper().startswith("ERROR"):
            filas[-1] += "\n" + linea
    return filas


def _lee_schtasks(tareas, leidas, sin_leer, negados, bash) -> None:
    try:
        p = ejecuta(SCHTASKS, bash)
    except subprocess.TimeoutExpired:
        sin_leer["schtasks"] = f"pasó de {TOPE_FUENTE_S:g} s"
        return
    except OSError as e:
        sin_leer["schtasks"] = f"no pude correrlo: {e}"
        return
    filas = filas_schtasks(p.stdout)
    if not filas and "no scheduled tasks" in (p.stdout + p.stderr).lower():
        leidas.append("schtasks")
        return
    if not filas:
        sin_leer["schtasks"] = f"salió {p.returncode} sin filas CSV"
        return
    leidas.append("schtasks")
    for fila in filas:
        nombre = _FILA_CSV.match(fila).group(1)
        if "access is denied" in fila.lower():
            negados[nombre] = "Access is denied"
        else:
            tareas[nombre] = fila
    for linea in (p.stdout + p.stderr).splitlines():
        if "access is denied" in linea.lower() and not _FILA_CSV.match(linea):
            negados.setdefault(linea.strip(), "Access is denied")


def nombres_del_sistema(home: Path, plataforma_: str | None = None) -> list[str]:
    """Las fuentes que se leen siempre, por el nombre con que aparecen en leídas y sin leer."""
    p = plataforma_ or sys.platform
    if p == "darwin":
        return [str(d) for d in _dirs_mac(home)] + [CRONTAB]
    if p.startswith("win"):
        return ["schtasks"]
    return [CRONTAB]


def _lee_sistema(home, plataforma_, bash, tareas, leidas, sin_leer, negados) -> None:
    if plataforma_ == "darwin":
        for d in _dirs_mac(home):
            _lee_carpeta(d, tareas, leidas, sin_leer, negados)
        _lee_crontab(tareas, leidas, sin_leer, bash)
    elif plataforma_.startswith("win"):
        _lee_schtasks(tareas, leidas, sin_leer, negados, bash)
    else:
        _lee_crontab(tareas, leidas, sin_leer, bash)


def _extras(maquina: str, conf_dir: Path | None) -> tuple[list[str], list[str], str]:
    """(archivos, comandos, error) de la sección de esta máquina en programadas.toml."""
    if conf_dir is None:
        return [], [], ""
    f = Path(conf_dir) / ARCHIVO_EXTRAS
    if not f.exists():
        return [], [], ""
    try:
        sec = tomllib.loads(f.read_text(encoding="utf-8")).get(maquina, {})
    except (OSError, tomllib.TOMLDecodeError) as e:
        return [], [], f"no pude leer {ARCHIVO_EXTRAS}: {e}"
    archivos, comandos = sec.get("archivos", []), sec.get("comandos", [])
    if not (
        isinstance(archivos, list)
        and isinstance(comandos, list)
        and all(isinstance(x, str) for x in [*archivos, *comandos])
    ):
        return (
            [],
            [],
            f"{ARCHIVO_EXTRAS} [{maquina}]: archivos y comandos son listas de texto",
        )
    return archivos, comandos, ""


def _lee_archivo_extra(p: Path, tareas, negados, sin_leer) -> None:
    try:
        tareas[str(p)] = _texto_archivo(p)
    except PermissionError as e:
        negados[str(p)] = f"sin permiso de lectura: {e.strerror or e}"
    except OSError as e:
        sin_leer[str(p)] = str(e)


def _lee_extras(
    maquina, conf_dir, home, bash, tareas, leidas, sin_leer, negados
) -> None:
    archivos, comandos, error = _extras(maquina, conf_dir)
    if error:
        sin_leer[ARCHIVO_EXTRAS] = error
    for patron in archivos:
        patron = str(Path(patron).expanduser())
        hallados = sorted(glob.glob(patron))
        if not hallados:
            sin_leer[patron] = "el patrón no encontró ningún archivo"
            continue
        leidas.append(patron)
        for h in map(Path, hallados):
            if h.is_dir():
                _carpeta_plist(h, tareas, negados)
            else:
                _lee_archivo_extra(h, tareas, negados, sin_leer)
    for cmd in comandos:
        try:
            p = ejecuta(cmd, bash)
        except subprocess.TimeoutExpired:
            sin_leer[cmd] = f"pasó de {TOPE_FUENTE_S:g} s"
            continue
        except OSError as e:
            sin_leer[cmd] = f"no pude correrlo: {e}"
            continue
        if p.returncode != 0:
            sin_leer[cmd] = f"salió {p.returncode}"
            continue
        leidas.append(cmd)
        tareas[cmd] = p.stdout + p.stderr


def lee_tareas(
    maquina: str,
    conf_dir: Path | None,
    sistema: bool = True,
    *,
    home: Path | None = None,
    plataforma_: str | None = None,
    bash: str = "",
) -> Lectura:
    """Lo que hay programado en esta máquina: (tareas, leídas, sin leer, negados).

    `tareas` es {nombre: texto entero de la tarea}. `leídas` y `sin leer` hablan de fuentes (una
    carpeta, un archivo o un comando); `negados` de archivos sueltos de una fuente leída que el
    sistema no dejó abrir. `sistema=False` salta las fuentes del sistema (el banco de pruebas)."""
    home = home or Path.home()
    tareas: dict[str, str] = {}
    leidas: list[str] = []
    sin_leer: dict[str, str] = {}
    negados: dict[str, str] = {}
    if sistema:
        _lee_sistema(
            home, plataforma_ or sys.platform, bash, tareas, leidas, sin_leer, negados
        )
    _lee_extras(maquina, conf_dir, home, bash, tareas, leidas, sin_leer, negados)
    return tareas, leidas, sin_leer, negados


def tareas_con(
    tareas: dict[str, str], ruta: str | Path, home: Path | None = None
) -> list[str]:
    home = home or Path.home()
    return [n for n, t in tareas.items() if contiene(t, ruta, home)]


def valida_programadas(raw: dict, maquinas: set[str] | None = None) -> list[str]:
    """Esquema cerrado de programadas.toml: secciones con nombre de máquina; dentro, `archivos` y
    `comandos`, listas de texto."""
    errores = []
    for sec, cuerpo in raw.items():
        if maquinas is not None and sec not in maquinas:
            errores.append(f"{ARCHIVO_EXTRAS}: [{sec}] no es el nombre de una máquina")
            continue
        if not isinstance(cuerpo, dict):
            errores.append(f"{ARCHIVO_EXTRAS}: [{sec}] debe ser una sección")
            continue
        for k, v in cuerpo.items():
            if k not in CLAVES_EXTRAS:
                errores.append(
                    f"{ARCHIVO_EXTRAS} [{sec}]: clave desconocida {k!r}; solo archivos y comandos"
                )
            elif not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
                errores.append(f"{ARCHIVO_EXTRAS} [{sec}]: {k} es una lista de texto")
    return errores
