"""Comprobar una generacion antes de activarla o al recuperarla."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import NamedTuple


class Comprobacion(NamedTuple):
    """Lo que dijo el comprobador de una generacion.

    `sin_medir` trae la causa cuando la lectura no termino a tiempo: la generacion no
    queda sana ni rota, y nadie la revierte por eso.
    """

    rc: int
    comprobador: str
    sin_medir: str = ""


def _sin_medir(exc: subprocess.TimeoutExpired) -> Comprobacion:
    orden = " ".join(map(str, exc.cmd)) if isinstance(exc.cmd, list) else str(exc.cmd)
    return Comprobacion(1, orden, f"`{orden}` no respondio en {exc.timeout:g} s; no se midio")


def comprobar(gen: Path) -> Comprobacion:
    try:
        return Comprobacion(*_correr(gen))
    except subprocess.TimeoutExpired as exc:
        return _sin_medir(exc)


def _correr(gen: Path) -> tuple[int, str]:
    if (gen / "runtime.json").is_file():
        try:
            from .actualizaciones import comprobar_runtime

            comprobar_runtime(gen)
        except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError):
            return 1, "runtime de componentes"
    script = gen / "comprobar.py"
    if script.is_file():
        try:
            rc = subprocess.run(
                [sys.executable, str(script)], cwd=gen, timeout=120
            ).returncode
        except OSError:
            return 1, "comprobar.py"
        return rc, "comprobar.py"
    if (gen / "motor" / "__main__.py").is_file():
        try:
            ayuda = subprocess.run(
                [sys.executable, "-m", "motor", "--help"],
                cwd=gen,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if ayuda.returncode != 0:
                return ayuda.returncode or 1, "motor --help"
            compilado = subprocess.run(
                [sys.executable, "-m", "compileall", "-q", str(gen / "motor")],
                cwd=gen,
                capture_output=True,
                text=True,
                timeout=120,
            )
            return compilado.returncode, "motor --help y compileall"
        except OSError:
            return 1, "motor --help y compileall"
    if (gen / "pyproject.toml").is_file() or any(gen.glob("*.py")) or (gen / "motor").is_dir():
        try:
            rc = subprocess.run(
                [sys.executable, "-m", "compileall", "-q", str(gen)],
                cwd=gen,
                capture_output=True,
                text=True,
                timeout=120,
            ).returncode
        except OSError:
            return 1, "compileall"
        return rc, "compileall"
    return 1, "ninguno"


def comprobar_instalada(gen: Path) -> Comprobacion:
    """La instalada que se recupera ademas debe arrancar el motor."""
    comprobacion = comprobar(gen)
    if comprobacion.rc != 0 or not (gen / "motor" / "__main__.py").is_file():
        return comprobacion
    try:
        arranque = subprocess.run(
            [sys.executable, "-m", "motor", "--help"],
            cwd=gen,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        return _sin_medir(exc)
    except OSError:
        return Comprobacion(1, "motor --help")
    return Comprobacion(arranque.returncode, "motor --help")
