import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import aislamiento

ES_POSIX = os.name != "nt"
SANDBOX = Path("/usr/bin/sandbox-exec")


@unittest.skipUnless(ES_POSIX, "el aislamiento POSIX no existe en Windows")
class SinAislamientoTest(unittest.TestCase):
    """Sin sandbox-exec (Linux) el motor no aísla: `aislar` se niega en vez de lanzar sin
    jaula. Por eso las pruebas que lanzan un trabajador real se saltan ahí
    (`tests.entorno.HAY_AISLAMIENTO`) y la batería completa corre en ubuntu-latest."""

    def test_aislar_se_niega_sin_sandbox_exec(self):
        es_archivo = Path.is_file
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            Path, "is_file", lambda p: False if p == SANDBOX else es_archivo(p)
        ):
            base = Path(tmp)
            espacio = base / "espacio" / "enc_1"
            espacio.mkdir(parents=True)
            with self.assertRaises(RuntimeError) as visto:
                aislamiento.aislar(["true"], espacio, base / "runtime" / "enc_1")
        self.assertIn("sin aislamiento efectivo", str(visto.exception))

    def test_la_guarda_de_las_pruebas_sigue_a_sandbox_exec(self):
        from tests.entorno import HAY_AISLAMIENTO

        self.assertEqual(HAY_AISLAMIENTO, SANDBOX.is_file())
