"""Una sola forma de leer y escribir texto y JSON: UTF-8 con BOM tolerado y LF."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from motor.io import escribir_texto, leer_json, leer_texto


class Lectura(unittest.TestCase):
    def test_lee_json_con_bom_y_crlf(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "fases.json"
            ruta.write_bytes(
                b'\xef\xbb\xbf{"fases": [{"id": "datos"}]}\r\n'
            )
            self.assertEqual(leer_json(ruta), {"fases": [{"id": "datos"}]})

    def test_lee_texto_sin_bom_y_con_lf(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "nota.txt"
            ruta.write_bytes(b"\xef\xbb\xbfuno\r\ndos\r\n")
            self.assertEqual(leer_texto(ruta), "uno\ndos\n")

    def test_acepta_errors_replace_para_salidas_del_agente(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "salida.txt"
            ruta.write_bytes(b"ok \xff")
            self.assertEqual(leer_texto(ruta, errors="replace"), "ok \ufffd")


class Escritura(unittest.TestCase):
    def test_escribe_utf8_y_lf(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "nota.txt"
            escribir_texto(ruta, "uno\r\ndos\r")
            self.assertEqual(ruta.read_bytes(), "uno\ndos\n".encode("utf-8"))

    def test_no_agrega_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "fases.json"
            escribir_texto(ruta, '{"a": 1}\n')
            self.assertFalse(ruta.read_bytes().startswith(b"\xef\xbb\xbf"))


if __name__ == "__main__":
    unittest.main()
