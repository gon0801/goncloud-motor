"""El wrapper recibe por su entorno lo que el motor añade al agente.

Si esa variable falta, el intento termina con la causa escrita en su resultado.
El agente recibe lo que la variable trae y nunca la variable.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import arranque
from motor.encargos.aislamiento import ENTORNO_DEL_AGENTE
from motor.io import escribir_texto, leer_json


def _intento(raiz: Path) -> Path:
    spec = {
        "base": str(raiz / "base"),
        "encargo": "enc_inexistente",
        "token": "tok",
        "host": socket.gethostname(),
        "aislamiento": "directo",
        "cwd": str(raiz),
        "hora": "2026-11-12T00:00:00Z",
        "sesion": "",
        "rol": "responsable",
        "agente": "python3",
        "cmd": ["true"],
    }
    intento = raiz / "intento"
    intento.mkdir()
    escribir_texto(intento / "arranque.json", json.dumps(spec))
    return intento


@unittest.skipIf(os.name == "nt", "el wrapper del intento es de la rama POSIX")
class ElWrapperYSuEntorno(unittest.TestCase):
    def test_sin_la_variable_de_transporte_el_intento_termina_con_la_causa(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            intento = _intento(Path(tmp).resolve())
            sin_variable = {k: v for k, v in os.environ.items() if k != ENTORNO_DEL_AGENTE}
            with mock.patch.dict(os.environ, sin_variable, clear=True):
                rc = arranque.ejecutar(intento / "arranque.json")
            self.assertEqual(rc, 1)
            resultado = leer_json(intento / "outcome.json")
            self.assertIn(ENTORNO_DEL_AGENTE, resultado["fallo_runtime"])

    def test_el_agente_recibe_el_delta_y_no_la_variable_de_transporte(self):
        from motor.encargos.aislamiento import entorno_recibido

        mira = f"import os; print(os.environ.get({ENTORNO_DEL_AGENTE!r}), os.environ['DELTA'])"
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            with mock.patch.dict(os.environ, {ENTORNO_DEL_AGENTE: json.dumps({"DELTA": "si"})}):
                spec = {"cmd": [sys.executable, "-c", mira], "env": entorno_recibido()}
                creado = arranque.crear_agente(
                    spec, Path(tmp), {"cwd": tmp, "stdout": subprocess.PIPE, "text": True}
                )
                salida, _ = creado.proceso.communicate(timeout=30)
        self.assertEqual(salida.split(), ["None", "si"])


if __name__ == "__main__":
    unittest.main()
