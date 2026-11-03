"""Guarda de la batería: ninguna prueba escribe en los homes reales del motor.

`tests/__init__.py` (unittest) y `conftest.py` (pytest) llaman a `instalar()`.
Redirige `MOTOR_HOME` a un temporal de la sesión y deja un gancho de auditoría
que falla si una prueba intenta escribir en `~/.motor` o en el estado real de
`~/.motor-encargos` fuera del árbol del repositorio.

La guarda usa `os.path`, no `pathlib`: varias pruebas parchean `os.name` a
`nt` para simular Windows, y en ese momento `pathlib.Path` intenta construir un
`WindowsPath` que no existe en Linux ni en macOS.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile

_RAIZ = os.path.realpath(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_HOME_REAL = os.path.expanduser("~")
_MOTOR_REAL = os.path.realpath(os.path.join(_HOME_REAL, ".motor"))
_ENCARGOS_REAL = os.path.realpath(os.path.join(_HOME_REAL, ".motor-encargos"))
_TEMP = os.path.realpath(tempfile.gettempdir())
_INSTALADO = False
_REENTRADA = False
_HOME_SESION = ""


def home_sesion() -> str:
    """El home del motor de esta corrida de pruebas."""
    return _HOME_SESION or _TEMP


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
    global _REENTRADA
    if _REENTRADA:
        return
    _REENTRADA = True
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
        _REENTRADA = False


def instalar() -> str:
    """Redirige el home del motor y activa la guarda; idempotente."""
    global _INSTALADO, _HOME_SESION
    if _INSTALADO:
        return home_sesion()
    if not _HOME_SESION:
        _HOME_SESION = tempfile.mkdtemp(prefix="motor-home-pruebas-")
        atexit.register(shutil.rmtree, _HOME_SESION, True)
    os.environ["MOTOR_HOME"] = _HOME_SESION
    os.environ.setdefault(
        "MOTOR_ENCARGOS_HOME", os.path.join(_HOME_SESION, "encargos")
    )
    sys.addaudithook(_auditar)
    _INSTALADO = True
    return _HOME_SESION
