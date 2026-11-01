"""El modelo TLA+ de la inspección (verificacion/inspeccion) usa el mismo tope del freno
de vueltas sin concluir que el motor. Si alguien cambia TOPE_NO_CONCLUYENTES, los .cfg
dejan de describir el código y la verificación sale verde sin decir nada."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from motor.encargos.dominio import TOPE_NO_CONCLUYENTES

MODELO = Path(__file__).resolve().parent.parent / "verificacion" / "inspeccion"


class TopeDelModelo(unittest.TestCase):
    def test_cada_cfg_usa_el_tope_del_motor(self):
        cfgs = sorted(MODELO.glob("*.cfg"))
        self.assertTrue(cfgs, "no hay configuraciones del modelo")
        for cfg in cfgs:
            with self.subTest(cfg=cfg.name):
                coincidencias = re.findall(r"^\s*Tope\s*=\s*(\d+)\s*$", cfg.read_text(), re.M)
                self.assertEqual(
                    coincidencias,
                    [str(TOPE_NO_CONCLUYENTES)],
                    f"{cfg.name} debe fijar Tope = {TOPE_NO_CONCLUYENTES} "
                    "(dominio.TOPE_NO_CONCLUYENTES); mueve el commit de sincronía de la skill "
                    "verificar-inspeccion después de revisar el modelo",
                )


if __name__ == "__main__":
    unittest.main()
