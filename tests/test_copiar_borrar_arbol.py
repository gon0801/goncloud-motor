import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos.copiar import borrar_arbol


class BorrarArbolIlegibleTest(unittest.TestCase):
    def test_entrada_inaccesible_se_quita_y_sigue_el_resto(self):
        with tempfile.TemporaryDirectory() as temporal:
            base = Path(temporal)
            arbol = base / "espacio" / "x"
            ilegible = arbol / ".venv" / "lib64"
            ilegible.mkdir(parents=True)
            (arbol / "otro.txt").write_text("borrar")
            ajeno = base / "ajeno.txt"
            ajeno.write_text("conservar")
            real = os.scandir

            def scandir(ruta):
                if os.fspath(ruta) == os.fspath(ilegible):
                    error = OSError(1920, "The file cannot be accessed by the system", str(ruta))
                    error.winerror = 1920
                    raise error
                return real(ruta)

            with (mock.patch.object(shutil, "_use_fd_functions", False),
                  mock.patch.object(os, "scandir", side_effect=scandir)):
                borrar_arbol(arbol)

            self.assertFalse(arbol.exists())
            self.assertEqual(ajeno.read_text(), "conservar")
