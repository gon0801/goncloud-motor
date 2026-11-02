"""Los JSON que escribe un agente se leen aunque traigan BOM.

En Windows, varias herramientas escriben UTF-8 con BOM (EF BB BF). El 2-nov, en la PC,
el motor rechazó `.interno/fases.json` de un agente con "Unexpected UTF-8 BOM" y lo
contó como falla del instrumento. Estas pruebas fallan si alguno de los lectores de
archivos del agente vuelve a `encoding="utf-8"`.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from motor.encargos import comprobacion, fases, necesidades
from motor.encargos.ciclo import _pedidos_efecto
from motor.encargos.comprobacion import interno_de
from motor.encargos.dominio import AlcanceRevision

BOM = "﻿"


def _escribir_con_bom(path: Path, datos) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(BOM + json.dumps(datos), encoding="utf-8")
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")


class JsonDelAgenteConBom(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.espacio = Path(self._tmp.name) / "espacio"
        self.interno = interno_de(self.espacio)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_fases_json_con_bom(self) -> None:
        _escribir_con_bom(
            self.interno / fases.NOMBRE,
            {"fases": [{"id": "unica", "resultado": "el aviso se lee en lenguaje llano"}]},
        )
        propuesta = fases.leer_propuesta(self.interno)
        self.assertEqual([f["id"] for f in propuesta], ["unica"])

    def test_comprobaciones_json_con_bom(self) -> None:
        _escribir_con_bom(
            self.interno / "comprobaciones.json",
            {"comprobaciones": [{"objetivo": "pasa", "argv": ["python3", "-c", "pass"]}]},
        )
        declaradas = comprobacion.leer_declaradas(self.interno)
        self.assertEqual([c["objetivo"] for c in declaradas], ["pasa"])

    def test_peticion_del_buzon_con_bom(self) -> None:
        path = self.espacio / "peticion.json"
        _escribir_con_bom(path, {"id": "p_1", "operacion": "ci.correr"})
        self.assertEqual(necesidades._leer(path), {"id": "p_1", "operacion": "ci.correr"})

    def test_pedido_efecto_con_bom(self) -> None:
        _escribir_con_bom(
            self.interno / "pedido-efecto.json",
            {"id": "pr-1", "operacion": "merge", "clave": "pr-1", "cuerpo": "integrado"},
        )
        pedidos = _pedidos_efecto(self.espacio)
        self.assertEqual([p.get("id") for p in pedidos], ["pr-1"])

    def test_juicio_del_revisor_con_bom(self) -> None:
        path = self.espacio / "resultado.json"
        _escribir_con_bom(
            path,
            {"version": "v1", "veredicto": "aprobado", "hallazgos": [], "evidencia": "leí el diff"},
        )
        alcance = AlcanceRevision(clase="completa", version="v1", base_version="")
        resultado = comprobacion.leer_juicio(path, alcance)
        self.assertNotIsInstance(resultado, comprobacion.FalloInstrumento, resultado)


if __name__ == "__main__":
    unittest.main()
