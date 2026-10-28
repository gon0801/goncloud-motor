"""El runtime preparado no depende del SDK privado de otro componente."""

import tempfile
import unittest
from pathlib import Path

from motor.flota import distribuidores


class NodeDeGeneracion(unittest.TestCase):
    def test_node_se_conserva_sin_el_sdk_y_rechaza_una_copia_alterada(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            original = base / "sdk-privado" / "node.exe"
            original.parent.mkdir()
            original.write_bytes(b"MZ-runtime-node-oficial-de-ensayo")
            contenido = original.read_bytes()
            herramientas = base / "generacion" / "herramientas"
            preparado = distribuidores._node_de_generacion(original, herramientas / "kimi")
            self.assertTrue(preparado.is_relative_to(herramientas))
            self.assertEqual(preparado.read_bytes(), contenido)
            self.assertEqual(
                distribuidores._node_de_generacion(original, herramientas / "grok"),
                preparado,
            )
            original.unlink()
            self.assertEqual(preparado.read_bytes(), contenido)
            original.write_bytes(contenido)
            preparado.write_bytes(b"alterado")
            with self.assertRaisesRegex(ValueError, "Node preparado"):
                distribuidores._node_de_generacion(original, herramientas / "kimi")
