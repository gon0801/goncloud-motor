"""El primer drenado de una base con canal no anuncia hechos que ya existían."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from motor.encargos.registro import Registro
from motor.integraciones.directores import RECIBO_SEMILLA, drenar, encargar


class SemillaDeAvisos(unittest.TestCase):
    def test_el_historico_no_sale_y_el_hecho_nuevo_si(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marca = root / "enviado"
            script = root / "marca.py"
            script.write_text(
                "import os\n"
                "from pathlib import Path\n"
                f"Path({str(marca)!r}).write_text(os.environ.get('MOTOR_AVISO_ID', 'x'))\n"
                "print(os.environ.get('MOTOR_AVISO_ID', 'ok'))\n",
                encoding="utf-8",
            )
            reg = Registro(root / "base")
            self.addCleanup(reg.close)
            (reg.base / "canal.json").write_text(
                json.dumps({"ensayo": {"comando": [os.environ.get("PYTHON", "python3"), str(script)]}}),
                encoding="utf-8",
            )
            encargo = encargar(
                reg, "pedido", "k1", "2026-10-30T18:00:00Z", "claw", "ensayo", "durable"
            )["encargo"]
            reg.anotar(
                encargo,
                {
                    "kind": "fases",
                    "data": {
                        "fases": [{"id": "f1", "resultado": "reporte"}],
                        "hora": "2026-10-30T18:01:00Z",
                    },
                },
                "fases-1",
            )
            primero = drenar(reg)
            self.assertFalse(marca.exists(), "el hecho previo no debe salir al canal")
            self.assertTrue(primero["avisos"])
            self.assertTrue(
                all(aviso.get("recibo") == RECIBO_SEMILLA for aviso in primero["avisos"])
            )
            reg.anotar(
                encargo,
                {
                    "kind": "decision",
                    "data": {
                        "id": "d1",
                        "eleccion": "campanas_diarias",
                        "motivo": "los totales coinciden",
                        "hora": "2026-10-30T18:02:00Z",
                    },
                },
                "decision-1",
            )
            drenar(reg)
            self.assertTrue(marca.is_file(), "un hecho posterior a la semilla sí sale")


if __name__ == "__main__":
    unittest.main()
