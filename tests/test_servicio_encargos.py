"""La instalación del servicio de encargos exige ruta y vincula su base.

También cubre el cuerpo del aviso cuando la evidencia viene en prosa.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import hashlib

from motor.encargos.registro import Registro
from motor.integraciones.directores import _cuerpo_del_aviso
from motor.servicio import instalable_encargos, vincular_base


class ServicioEncargos(unittest.TestCase):
    def test_sin_ruta_no_hay_plantilla(self):
        with self.assertRaises(ValueError):
            instalable_encargos("mac", "u", "/py", "/base", "/raiz")

    def test_plantilla_lleva_la_ruta_autorizada(self):
        mac = instalable_encargos(
            "mac", "u", "/py", "/base", "/raiz",
            agente="grok", respaldos=("codex",),
        )
        self.assertIn("<string>--agente</string><string>grok</string>", mac)
        self.assertIn("<string>--respaldo</string><string>codex</string>", mac)
        pc = instalable_encargos(
            "pc", "u", "/py", "/base", "/raiz", agente="codex"
        )
        self.assertIn("--agente codex", pc)

    def test_vincular_base_escribe_flota_y_canal(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            flota = raiz / "flota"
            (flota / "generaciones").mkdir(parents=True)
            canal = raiz / "canal.json"
            canal.write_text(json.dumps({"openbot": {"comando": ["x"]}}))
            base = raiz / "base"
            salida = vincular_base(base, flota, canal)
            self.assertEqual(
                json.loads((base / "flota-home.json").read_text())["home"],
                str(flota.resolve()),
            )
            self.assertEqual(salida["canales"], ["openbot"])
            with self.assertRaises(ValueError):
                vincular_base(base, raiz / "sin-flota")
            malo = raiz / "canal-malo.json"
            malo.write_text("{no es json")
            base2 = raiz / "base2"
            with self.assertRaises(ValueError):
                vincular_base(base2, flota, malo)
            self.assertFalse((base2 / "flota-home.json").exists())
            inutil = raiz / "canal-inutil.json"
            inutil.write_text(json.dumps({"openbot": "no-es-config"}))
            with self.assertRaises(ValueError):
                vincular_base(base2, flota, inutil)

    def test_supervisor_sin_ruta_no_arranca_en_silencio(self):
        from motor.encargos.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            base = str(Path(tmp) / "encargos")
            main(["encargar", "--base", base, "--texto", "hola", "--clave", "s1"])
            self.assertNotEqual(
                main(["supervisor", "--base", base, "--una-vez"]), 0
            )


    def test_cuerpo_del_aviso_con_evidencia_en_prosa(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            espacio = reg.base / "espacio" / "enc_x"
            espacio.mkdir(parents=True)
            (espacio / "resultado.txt").write_text("la frase pedida", encoding="utf-8")
            self.assertEqual(
                _cuerpo_del_aviso(reg, "enc_x", "El juez vio la frase pedida."),
                "la frase pedida",
            )
            huella = hashlib.sha256(b"la frase pedida").hexdigest()
            self.assertEqual(
                _cuerpo_del_aviso(reg, "enc_x", huella), "la frase pedida"
            )
            self.assertIsNone(_cuerpo_del_aviso(reg, "enc_x", "0" * 64))


if __name__ == "__main__":
    unittest.main()
