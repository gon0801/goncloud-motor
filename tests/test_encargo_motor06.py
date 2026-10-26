"""Regresión de MOTOR 06: el grep inválido no es un defecto del producto."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from motor.encargos.ciclo import ahora_iso, recibir, reparar_motor06
from motor.encargos.frontera import DestinoEnsayo, despachar
from motor.encargos.registro import Registro

TOML = """
[modelos.k3]
nombre = "kimi-code/k3"
provider = "managed:kimi-code"

[modelos.k31]
nombre = "kimi-code/k3.1-pro"
provider = "managed:kimi-code"
"""

SALIDA = "grep: Invalid range end\n"


class Motor06(unittest.TestCase):
    def test_el_grep_no_cambia_el_producto_ni_repite_el_efecto(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            producto = Path(tmp) / "kimi-modelos.toml"
            producto.write_text(TOML, encoding="utf-8")
            antes = producto.read_bytes()
            reg = Registro(base)
            hora = ahora_iso()
            enc = recibir(reg, "deja las dos definiciones de Kimi", "origen-06", hora)["encargo"]
            destino = DestinoEnsayo(base / "destino")
            primero = despachar(
                reg,
                enc,
                destino,
                "pr-26",
                "merge",
                "destino:pr-26",
                "ya integrado",
                hora,
            )
            self.assertEqual(primero["n"], 1)
            vista = reparar_motor06(reg, enc, producto, SALIDA, "pr-26", destino, hora)
            self.assertEqual(producto.read_bytes(), antes)
            self.assertEqual(destino.consultar("destino:pr-26")["n"], 1)
            self.assertEqual(vista["estado"], "terminado")
            self.assertIn("kimi-code/k3", vista["ultimo_hecho"])
            self.assertIn("kimi-code/k3.1-pro", vista["ultimo_hecho"])
            self.assertNotIn("grep", vista["ultimo_hecho"])
            notas = reg.fold(enc).procedimiento
            self.assertTrue(any("parser TOML" in n for n in notas))
            reg.close()


if __name__ == "__main__":
    unittest.main()
