"""El comprobador falla si no puede plegar un prefijo del corpus."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class AplicarCorpus(unittest.TestCase):
    def test_un_registro_truncado_hace_fallar_la_comprobacion(self) -> None:
        from tools.aplicar_corpus import comprobar

        def reducir(estado, evento, nuevo=False):
            if evento["kind"] == "avanza":
                raise ValueError("privado")
            return estado or object()

        with tempfile.TemporaryDirectory() as temporal:
            ruta = Path(temporal) / "registros"
            ruta.mkdir()
            (ruta / "uno.sqlite").touch()
            with (
                mock.patch("tools.aplicar_corpus.ultimo_evento", return_value=1),
                mock.patch("tools.aplicar_corpus.leer_encargos", return_value=iter([
                    ("e", [{"kind": "entrega", "data": {}},
                           {"kind": "avanza", "data": {}}]),
                ])),
                mock.patch("tools.aplicar_corpus.reducir", side_effect=reducir),
                mock.patch("sys.stdout", new_callable=io.StringIO) as salida,
            ):
                self.assertEqual(comprobar(Path(temporal)), 1)
            self.assertIn("1 registros truncados", salida.getvalue())
            self.assertNotIn("privado", salida.getvalue())
