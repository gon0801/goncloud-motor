"""Guarda de la batería: ninguna prueba escribe en los homes reales del motor.

`tests/__init__.py` (unittest) y `conftest.py` (pytest) llaman a `instalar()`.
Redirige `MOTOR_HOME` a un temporal de la sesión y deja un gancho de auditoría
que falla si una prueba intenta escribir en `~/.motor` o en el estado real de
`~/.motor-encargos` fuera del árbol del repositorio.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[1]
_HOME_REAL = Path.home()
_MOTOR_REAL = (_HOME_REAL / ".motor").resolve()
_ENCARGOS_REAL = (_HOME_REAL / ".motor-encargos").resolve()
_TEMP = Path(tempfile.gettempdir()).resolve()
_INSTALADO = False
_REENTRADA = False
_HOME_SESION: Path | None = None


def home_sesion() -> Path:
    """El home del motor de esta corrida de pruebas."""
    return _HOME_SESION or _TEMP


def _bajo(ruta: Path, raiz: Path) -> bool:
    return ruta == raiz or raiz in ruta.parents


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
    if isinstance(ruta, int):
        return False
    try:
        p = Path(os.fspath(ruta)).expanduser().resolve()
    except (TypeError, ValueError, OSError):
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


def instalar() -> Path:
    """Redirige el home del motor y activa la guarda; idempotente."""
    global _INSTALADO, _HOME_SESION
    if _INSTALADO:
        return home_sesion()
    if _HOME_SESION is None:
        _HOME_SESION = Path(tempfile.mkdtemp(prefix="motor-home-pruebas-"))
        atexit.register(shutil.rmtree, _HOME_SESION, True)
    os.environ["MOTOR_HOME"] = str(_HOME_SESION)
    os.environ.setdefault("MOTOR_ENCARGOS_HOME", str(_HOME_SESION / "encargos"))
    sys.addaudithook(_auditar)
    _INSTALADO = True
    return _HOME_SESION
