import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos.copiar import _reintentar_sin_solo_lectura, borrar_arbol


class BorrarArbolIlegibleTest(unittest.TestCase):
    def test_archivo_de_solo_lectura_con_1920_no_se_confunde_con_directorio(self):  # no-roja-en-base: main 7c07792 borra el archivo mediante la ruta generica con chmod
        with tempfile.TemporaryDirectory() as temporal:
            archivo = Path(temporal) / "archivo"
            archivo.write_text("borrar")
            archivo.chmod(stat.S_IREAD)
            original = Path.unlink
            error = OSError(1920, "The file cannot be accessed by the system")
            error.winerror = 1920

            def unlink(ruta):
                if ruta == archivo and not ruta.stat().st_mode & stat.S_IWUSR:
                    raise PermissionError(13, "solo lectura", str(ruta))
                return original(ruta)

            with mock.patch.object(Path, "unlink", autospec=True, side_effect=unlink):
                _reintentar_sin_solo_lectura(os.unlink, archivo, error)
            self.assertFalse(archivo.exists())

            bloqueado = Path(temporal) / "bloqueado"
            bloqueado.write_text("sin permisos")
            bloqueo = PermissionError(13, "acceso denegado")
            with (mock.patch.object(Path, "unlink", side_effect=bloqueo),
                  mock.patch.object(os, "unlink", side_effect=bloqueo)):
                with self.assertRaises(PermissionError):
                    _reintentar_sin_solo_lectura(os.unlink, bloqueado, error)
            self.assertTrue(bloqueado.exists())

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
