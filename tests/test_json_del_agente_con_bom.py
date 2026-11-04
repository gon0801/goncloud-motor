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

from motor.encargos import ciclo, comprobacion, fases, necesidades, transporte
from types import SimpleNamespace
from unittest import mock

from motor.encargos.ciclo import _pedidos_efecto, _resultado_desde_fold
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
        _escribir_con_bom(path, {"id": "p_1", "operacion": "remoto.leer"})
        self.assertEqual(necesidades._leer(path), {"id": "p_1", "operacion": "remoto.leer"})

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

    def test_juicio_con_bom_en_el_respaldo_de_revision_json(self) -> None:
        # _resultado_desde_fold sin una revision en el registro cae a leer revision.json
        # y el juicio que dejo el agente; antes leia ese juicio con utf-8 y fallaba en silencio.
        directorio = Path(self._tmp.name) / "intento"
        directorio.mkdir()
        observaciones = directorio / "observaciones.json"
        observaciones.write_text(json.dumps({"version": "v1"}), encoding="utf-8")
        resultado = directorio / "resultado.json"
        _escribir_con_bom(resultado, {"version": "v1", "veredicto": "aprobado"})
        (directorio / "revision.json").write_text(
            json.dumps({"observaciones": str(observaciones), "resultado": str(resultado)}),
            encoding="utf-8",
        )

        class RegistroSinEventos:
            def eventos(self, _encargo):
                return []

        enc = SimpleNamespace(id="enc_prueba", fase_activa="unica")
        observacion, juicio = _resultado_desde_fold(RegistroSinEventos(), enc, directorio)
        self.assertEqual(juicio, {"version": "v1", "veredicto": "aprobado"})
        self.assertEqual(observacion, {"version": "v1"})

    def test_juicio_con_bom_al_soltar_el_conductor(self) -> None:
        # _soltar_conductor lee el juicio para saber si el revisor ya aprobo. Si aprobo,
        # primero pregunta a la autoridad si el encargo termino y no suelta al conductor
        # mientras siga corriendo. Con utf-8 el juicio con BOM fallaba en silencio y el
        # conductor se soltaba con una aprobacion en camino.
        directorio = Path(self._tmp.name) / "intento"
        directorio.mkdir()
        resultado = directorio / "resultado.json"
        _escribir_con_bom(resultado, {"version": "v1", "veredicto": "aprobado"})
        (directorio / "revision.json").write_text(
            json.dumps({"resultado": str(resultado)}), encoding="utf-8"
        )
        ops: list[str] = []

        def operar(_base, mensaje, timeout=30):
            ops.append(mensaje["op"])
            if mensaje["op"] == "ver":
                return {"alcanzada": True, "salida": {"estado": "corriendo"}}
            return {"alcanzada": True, "salida": {"cerrado": True}}

        enc = SimpleNamespace(
            id="enc_prueba", estado="corriendo", candidato=None, conductor="tok"
        )
        registro = SimpleNamespace(base=Path(self._tmp.name))
        with mock.patch.object(transporte, "cargar", return_value={}), mock.patch.object(
            transporte, "operar", operar
        ), mock.patch.object(ciclo, "_directorio", return_value=directorio), mock.patch.object(
            ciclo, "_revision_aprobada_vigente", return_value=False
        ):
            soltado = ciclo._soltar_conductor(registro, enc, "2026-11-02T00:00:00Z")
        self.assertEqual(ops, ["ver"])
        self.assertFalse(soltado)


if __name__ == "__main__":
    unittest.main()
