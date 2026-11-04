"""Regresiones de la limpieza de lanzamiento del 2 de noviembre."""

import os
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from motor.encargos import aislamiento, arranque
from motor.encargos.ciclo import recibir
from motor.encargos.dominio import prompt_responsable
from motor.encargos.registro import Registro


class LimpiezaDelTemporal(unittest.TestCase):
    def test_un_tmpdir_con_otro_nombre_no_borra_su_hermano_tmp(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ajeno = base / "tmp"
            ajeno.mkdir()
            archivo = ajeno / "conservar.txt"
            archivo.write_text("del usuario", encoding="utf-8")
            arranque._limpiar_temporal_de({"env": {"TMPDIR": str(base / "cache")}})
            self.assertTrue(archivo.is_file(), "se borró el tmp hermano de otro TMPDIR")
            self.assertEqual(archivo.read_text(encoding="utf-8"), "del usuario")

    def test_un_tmpdir_relativo_no_se_interpreta_desde_el_cwd(self):
        with mock.patch("motor.encargos.aislamiento.limpiar_temporal") as limpiar:
            arranque._limpiar_temporal_de({"env": {"TMPDIR": "runtime/tmp"}})
        limpiar.assert_not_called()

    def test_el_tmp_absoluto_del_runtime_se_borra_y_conserva_su_vecino(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "runtime"
            temporal = aislamiento.temporal_de(runtime)
            (temporal / "resto.txt").write_text("resto", encoding="utf-8")
            vecino = runtime / "conservar.txt"
            vecino.write_text("vigente", encoding="utf-8")
            arranque._limpiar_temporal_de({"env": {"TMPDIR": str(temporal)}})
            self.assertFalse(temporal.exists())
            self.assertEqual(vecino.read_text(encoding="utf-8"), "vigente")


class NivelDeEntregaEnElPrompt(unittest.TestCase):
    def test_el_responsable_recibe_el_nivel_registrado_sin_alterar_el_pedido(self):
        for nivel in ("terminado", "pr", "integrado", "desplegado"):
            with self.subTest(nivel=nivel), tempfile.TemporaryDirectory() as tmp:
                with closing(Registro(Path(tmp) / "base")) as reg:
                    recibido = recibir(
                        reg,
                        "Corregir el informe",
                        "limpieza-b",
                        "2026-11-02T00:00:00Z",
                        entrega_nivel=nivel,
                        repo="gon0801/goncloud-motor",
                        base_rama="main",
                    )
                    encargo = reg.fold(recibido["encargo"])
                texto = prompt_responsable(encargo)
                self.assertIn(f"Nivel de entrega autorizado: {nivel}.", texto)
                self.assertIn("Pedido:\nCorregir el informe", texto)
                self.assertIn("pedido-efecto.json", texto)


class SondaWindowsConPython(unittest.TestCase):
    def test_python_comprueba_las_rutas_sin_sondear_bash(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            espacio = base / "espacio"
            espacio.mkdir()
            fuente = base / "fuente.txt"
            fuente.write_text("dato", encoding="utf-8")
            temporal = base / "temporal"
            temporal.mkdir()

            def preparar(argv, *args, **kwargs):
                self.assertEqual(argv[:2], [sys.executable, "-c"])
                return aislamiento.ComandoAislado(
                    argv,
                    {**os.environ, "TMPDIR": str(temporal),
                     "CLAUDE_CODE_TMPDIR": str(temporal)},
                    "directo",
                )

            with mock.patch.object(aislamiento, "os", SimpleNamespace(name="nt")), \
                 mock.patch.object(aislamiento, "aislar", side_effect=preparar):
                resultado = aislamiento.sondear(espacio, base / "runtime", (fuente,))
                self.assertTrue(resultado.ok, resultado.causa)
                fuente.unlink()
                sin_fuente = aislamiento.sondear(espacio, base / "runtime", (fuente,))
            self.assertFalse(sin_fuente.ok)
            self.assertIn(str(fuente), sin_fuente.causa)
            self.assertEqual(list(espacio.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
