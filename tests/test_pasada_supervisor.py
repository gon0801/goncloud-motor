"""El recibo de pasada del supervisor que lee la comprobación del despliegue."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor import version
from motor.encargos import cli


class ReciboDePasada(unittest.TestCase):
    def setUp(self):
        version._SHA_ARRANQUE = None

    def tearDown(self):
        version._SHA_ARRANQUE = None

    def test_deja_sha_hora_y_pasada_completa(self):
        self.assertTrue(
            hasattr(cli, "_marcar_pasada"),
            "el supervisor no deja recibo de pasada con su SHA",
        )
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with mock.patch("motor.version.sha_actual", return_value="a" * 40):
                cli._marcar_pasada(base, "2026-11-08T10:00:00Z", [{"estado": "recibido"}])
            doc = json.loads((base / "supervisor-pasada.json").read_text(encoding="utf-8"))
            self.assertEqual(doc["sha"], "a" * 40)
            self.assertEqual(doc["ruta"], str(version.RAIZ))
            self.assertEqual(doc["hora"], "2026-11-08T10:00:00Z")
            self.assertTrue(doc["completa"])
            self.assertEqual(doc["encargos"], 1)
            self.assertFalse((base / "supervisor-pasada.json.tmp").exists())

    def test_repetir_la_pasada_es_idempotente(self):
        self.assertTrue(hasattr(cli, "_marcar_pasada"))
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with mock.patch("motor.version.sha_actual", return_value="b" * 40):
                for hora in ("2026-11-08T10:00:00Z", "2026-11-08T10:00:01Z"):
                    cli._marcar_pasada(base, hora, [])
            doc = json.loads((base / "supervisor-pasada.json").read_text(encoding="utf-8"))
            self.assertEqual(doc["hora"], "2026-11-08T10:00:01Z")

    def test_el_recibo_conserva_el_sha_del_arranque_aunque_cambie_el_disco(self):
        # Un supervisor sin reiniciar sigue con el código viejo aunque un
        # checkout mueva HEAD en disco: su pasada no acredita el SHA nuevo.
        self.assertTrue(
            hasattr(cli, "_marcar_pasada"),
            "el supervisor no deja recibo de pasada con su SHA",
        )
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with mock.patch("motor.version.sha_actual", return_value="a" * 40):
                cli._marcar_pasada(base, "2026-11-08T10:00:00Z", [])
            with mock.patch("motor.version.sha_actual", return_value="b" * 40):
                cli._marcar_pasada(base, "2026-11-08T10:01:00Z", [])
            doc = json.loads((base / "supervisor-pasada.json").read_text(encoding="utf-8"))
            self.assertEqual(doc["sha"], "a" * 40)
            self.assertEqual(doc["hora"], "2026-11-08T10:01:00Z")


if __name__ == "__main__":
    unittest.main()
