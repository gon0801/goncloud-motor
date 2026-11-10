"""Guarda de la batería: ninguna prueba escribe en los homes reales del motor.

`tests/__init__.py` (unittest) y `conftest.py` (pytest) llaman a `instalar()`.
Redirige `MOTOR_HOME` a un temporal de la sesión y deja un gancho de auditoría
que falla si una prueba intenta escribir en `~/.motor` o en el estado real de
`~/.motor-encargos` fuera del árbol del repositorio. El gancho solo ve los
eventos de este proceso: un subproceso que una prueba lance contra el home real
escapa de la auditoría; lo que lo aparta del home real es `MOTOR_HOME` y los
argumentos que la prueba misma le pase.

La guarda usa `os.path`, no `pathlib`: varias pruebas parchean `os.name` a
`nt` para simular Windows, y en ese momento `pathlib.Path` intenta construir un
`WindowsPath` que no existe en Linux ni en macOS.

Además, `instalar()` pone delante del PATH un centinela por cada CLI de agente
que exista en la máquina. El centinela falla si una prueba normal intenta
ejecutarlo: agy y los demás clientes de suscripción abren el navegador cuando
no encuentran sesión, y la batería no debe tener ese efecto. Un ensayo vivo
declara su permiso con `MOTOR_PROBAR_<CLIENTE>_VIVO=1` y el centinela delega en
el binario real.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
import threading

_RAIZ = os.path.realpath(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_HOME_REAL = os.path.expanduser("~")
_MOTOR_REAL = os.path.realpath(os.path.join(_HOME_REAL, ".motor"))
_ENCARGOS_REAL = os.path.realpath(os.path.join(_HOME_REAL, ".motor-encargos"))
_TEMP = os.path.realpath(tempfile.gettempdir())
_INSTALADO = False
_LOCAL = threading.local()
_HOME_SESION = ""
_BIN_CENTINELAS = ""
_LOG_CENTINELAS = ""

# Clientes del catálogo de agentes; en POSIX el nombre es el ejecutable.
CLIENTES_CENTINELA = (
    "agy",
    "claude",
    "codex",
    "grok",
    "kimi",
    "muse",
    "opencode",
    "qwen",
)


def home_sesion() -> str:
    """El home del motor de esta corrida de pruebas."""
    return _HOME_SESION or _TEMP


def bin_centinelas() -> str:
    """La carpeta con los centinelas de los CLIs de agente, o "" sin guarda."""
    return _BIN_CENTINELAS


def llamadas_centinela() -> str:
    """El archivo donde los centinelas anotan los intentos de ejecución."""
    return _LOG_CENTINELAS


def _permiso(cliente: str) -> str:
    return "MOTOR_PROBAR_" + cliente.upper().replace("-", "_") + "_VIVO"


def _instalar_centinelas() -> None:
    """Un shim por cliente instalado, delante del PATH de la sesión."""
    global _BIN_CENTINELAS, _LOG_CENTINELAS
    if os.name == "nt" or _BIN_CENTINELAS:
        return
    carpeta = os.path.join(_HOME_SESION, "bin-centinelas")
    os.makedirs(carpeta, exist_ok=True)
    log = os.path.join(_HOME_SESION, "clientes-reales.log")
    for cliente in CLIENTES_CENTINELA:
        real = shutil.which(cliente)
        if not real:
            continue
        destino = os.path.join(carpeta, cliente)
        guion = (
            "#!/bin/sh\n"
            f"# Centinela de la batería: {cliente} real no se ejecuta en la suite.\n"
            f'if [ "${{{_permiso(cliente)}:-}}" = "1" ]; then exec "{real}" "$@"; fi\n'
            f'printf \'%s %s\\n\' "$(date -u +%FT%TZ)" "$0 $*" >> "{log}"\n'
            f'printf \'%s\\n\' "centinela: la batería normal no debe ejecutar'
            f' {cliente}; activa {_permiso(cliente)}=1 para el ensayo vivo" >&2\n'
            "exit 86\n"
        )
        with open(destino, "w", encoding="utf-8") as archivo:
            archivo.write(guion)
        os.chmod(destino, 0o755)
    _BIN_CENTINELAS = carpeta
    _LOG_CENTINELAS = log
    os.environ["PATH"] = carpeta + os.pathsep + os.environ.get("PATH", "")


def _bajo(ruta: str, raiz: str) -> bool:
    return ruta == raiz or ruta.startswith(raiz + os.sep)


def _normal(ruta) -> str | None:
    if isinstance(ruta, int):
        return None
    try:
        texto = os.fsdecode(os.fspath(ruta))
    except (TypeError, ValueError):
        return None
    if not texto:
        return None
    try:
        return os.path.realpath(os.path.abspath(os.path.expanduser(texto)))
    except (OSError, ValueError):
        return None


def _es_escritura(modo, flags) -> bool:
    if flags is not None:
        try:
            valor = int(flags)
        except (TypeError, ValueError):
            valor = 0
        if valor & (
            os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        ):
            return True
    if isinstance(modo, str):
        return any(c in modo for c in "wax+")
    return False


def _prohibida(ruta) -> bool:
    p = _normal(ruta)
    if p is None:
        return False
    if _bajo(p, _MOTOR_REAL):
        return True
    if _bajo(p, _ENCARGOS_REAL):
        return not (_bajo(p, _RAIZ) or _bajo(p, _TEMP) or _bajo(p, home_sesion()))
    return False


def _fallar(ruta) -> None:
    raise RuntimeError(
        "la prueba escribe fuera del home temporal del motor: " + str(ruta)
    )


def _auditar(evento, args):
    # Por hilo: un gancho ocupado en otro hilo no silencia los eventos de este.
    if getattr(_LOCAL, "reentrada", False):
        return
    _LOCAL.reentrada = True
    try:
        if evento == "open":
            ruta, modo, flags = args
            if not _es_escritura(modo, flags):
                return
        elif evento == "os.rename":
            if _prohibida(args[0]):
                _fallar(args[0])
            if _prohibida(args[1]):
                _fallar(args[1])
            return
        elif evento in ("os.mkdir", "os.rmdir", "os.remove", "os.truncate"):
            ruta = args[0]
        elif evento in ("os.symlink", "os.link"):
            ruta = args[1]
        else:
            return
        if _prohibida(ruta):
            _fallar(ruta)
    finally:
        _LOCAL.reentrada = False


def instalar() -> str:
    """Redirige el home del motor y activa la guarda; idempotente."""
    global _INSTALADO, _HOME_SESION
    if _INSTALADO:
        return home_sesion()
    if not _HOME_SESION:
        _HOME_SESION = tempfile.mkdtemp(prefix="motor-home-pruebas-")
        atexit.register(shutil.rmtree, _HOME_SESION, True)
    os.environ["MOTOR_HOME"] = _HOME_SESION
    _instalar_centinelas()
    sys.addaudithook(_auditar)
    _INSTALADO = True
    return _HOME_SESION
