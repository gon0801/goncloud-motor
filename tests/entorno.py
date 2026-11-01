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
    "solo corre en Windows (la PC); en la Mac lo prueba ops/prueba-pc.sh"
    " y en el CI lo corre el job windows"
)


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
