"""Qué aislamiento tiene el host que corre la batería. El motor solo aísla con AppContainer
(Windows) o con sandbox-exec (macOS): en Linux `aislar` levanta RuntimeError y las pruebas
que lanzan un trabajador real no pueden correr. La jaula de bubblewrap es de WSL y la prueba
`tests.test_jaula_linux`.

`requiere_windows` marca las pruebas propias de Windows: corren en Windows, se saltan fuera
con el motivo, y con MOTOR_EXIGE_WINDOWS=1 (el job `windows` del CI) un salto es un fallo,
igual que MOTOR_EXIGE_BWRAP hace con la jaula de Linux.
"""

import functools
import os
import unittest
from pathlib import Path

HAY_AISLAMIENTO = os.name == "nt" or Path("/usr/bin/sandbox-exec").is_file()
SIN_AISLAMIENTO = "el motor solo aísla con AppContainer o sandbox-exec; este host no tiene ninguno"

EXIGE_WINDOWS = os.environ.get("MOTOR_EXIGE_WINDOWS") == "1"
SIN_WINDOWS = (
    "solo corre en Windows (la PC); lo corre el job windows del CI y, en la PC"
    " real, ops/prueba-pc.sh"
)
SIN_BREAKAWAY = (
    "este host corre dentro de un Job sin breakaway (WinError 5 al lanzar):"
    " las pruebas de procesos reales con Job propio corren en la PC con ops/prueba-pc.sh"
)

_breakaway: bool | None = None


def deja_breakaway() -> bool:
    """¿Este host deja lanzar un proceso con CREATE_BREAKAWAY_FROM_JOB?

    El runner hospedado de Windows corre dentro de un Job que no lo permite;
    la PC lo prueba con ops/prueba-pc.sh.
    """
    global _breakaway
    if _breakaway is None:
        if os.name != "nt":
            _breakaway = False
        else:
            import subprocess
            import sys

            from motor.plataforma import windows

            try:
                p = subprocess.Popen([sys.executable, "-c", "pass"], **windows.kwargs_grupo())
            except OSError:
                _breakaway = False
            else:
                p.wait()
                _breakaway = True
    return _breakaway


def _clase_que_falla(*_args):
    raise AssertionError(
        "MOTOR_EXIGE_WINDOWS=1 y este host no es Windows: las pruebas propias de"
        " Windows se habrían saltado"
    )


def requiere_windows(destino):
    if os.name == "nt":
        return destino
    if not EXIGE_WINDOWS:
        return unittest.skipUnless(os.name == "nt", SIN_WINDOWS)(destino)
    if isinstance(destino, type):
        destino.setUpClass = _clase_que_falla
        return destino

    @functools.wraps(destino)
    def envoltorio(*args, **kwargs):
        raise AssertionError(
            "MOTOR_EXIGE_WINDOWS=1 y este host no es Windows: esta prueba propia de"
            " Windows se habría saltado"
        )

    return envoltorio


def requiere_breakaway(destino):
    """Un salto por capacidad del host (Job sin breakaway) es tolerado con motivo,
    dentro y fuera del job de Windows: en la PC corren de verdad."""
    return unittest.skipUnless(os.name == "nt" and deja_breakaway(), SIN_BREAKAWAY)(destino)
