"""El `python3` de cada sesión es el intérprete que corre el supervisor.

El primer `python3` del PATH puede ser el del sistema (el 3.9 en la Mini, sin
`tomllib`): la sesión pide `interprete=True` y `aislar` antepone en el PATH un
`python3` del runtime que ejecuta el intérprete del supervisor.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from motor.encargos import sesion
from motor.encargos.aislamiento import ComandoAislado, path_con_interprete

ROLES = ("responsable", "revisor")
PRUEBA = "import sys, tomllib, encodings; print(sys.version)"


def _encargo(rol: str) -> SimpleNamespace:
    return SimpleNamespace(id="enc_x", rol=rol, conductor="token", adjuntos=())


def _preparar(tmp: Path, rol: str, cmd: list[str]) -> dict:
    espacio = tmp / "base" / "espacio" / "enc_x"
    espacio.mkdir(parents=True)
    directorio = tmp / "base" / "ejecucion" / "enc_x" / "intento-1"
    directorio.mkdir(parents=True)
    sesion.preparar_administrado(
        cmd, espacio, directorio, _encargo(rol), "s1", "2026-11-01T00:00:00Z", tmp / "runtime"
    )
    return json.loads((directorio / "arranque.json").read_text(encoding="utf-8"))


class ElPythonQueVeLaSesion(unittest.TestCase):
    def test_python3_ejecuta_el_interprete_del_supervisor(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            carpeta = Path(tmp) / "runtime"
            path = path_con_interprete("/usr/bin:/bin", carpeta)
            wrapper = carpeta / "bin" / "python3"
            self.assertEqual(path.split(os.pathsep)[0], str(carpeta / "bin"))
            self.assertTrue(os.access(wrapper, os.X_OK))
            resultado = subprocess.run(
                [str(wrapper), "-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertEqual(resultado.stdout.strip(), sys.executable)

    def test_los_shims_del_motor_siguen_al_frente(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            shims = Path(tmp) / "shims"
            shims.mkdir()
            (shims / "llamada_guard.py").write_text("")
            runtime = Path(tmp) / "runtime"
            path = path_con_interprete(os.pathsep.join([str(shims), "/usr/bin"]), runtime)
        self.assertEqual(
            path.split(os.pathsep), [str(shims), str(runtime / "bin"), "/usr/bin"]
        )

    def test_repetir_deja_el_mismo_python3(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            runtime = Path(tmp) / "runtime"
            primero = path_con_interprete("/usr/bin", runtime)
            wrapper = runtime / "bin" / "python3"
            contenido = wrapper.read_text(encoding="utf-8")
            segundo = path_con_interprete("/usr/bin", runtime)
            self.assertEqual(primero, segundo)
            self.assertEqual(wrapper.read_text(encoding="utf-8"), contenido)
            self.assertEqual(os.listdir(runtime / "bin"), ["python3"])


class LaSesionPideElInterprete(unittest.TestCase):
    def test_responsable_y_revisor_lanzan_con_el_interprete(self):
        for rol in ROLES:
            with self.subTest(rol=rol), tempfile.TemporaryDirectory(
                ignore_cleanup_errors=True
            ) as tmp:
                pedidos = []

                def falso(cmd, espacio, runtime, **kw):
                    pedidos.append(kw)
                    return ComandoAislado(list(cmd), {}, "directo")

                with mock.patch("motor.encargos.aislamiento.aislar", falso):
                    _preparar(Path(tmp).resolve(), rol, ["python3", "-c", "pass"])
                self.assertEqual(len(pedidos), 1)
                self.assertIs(pedidos[0]["interprete"], True)
                self.assertIs(pedidos[0]["lectura"], rol == "revisor")


if __name__ == "__main__":
    unittest.main()
