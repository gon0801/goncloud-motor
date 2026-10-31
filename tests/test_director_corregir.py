"""`motor director corregir`: los directores mandan correcciones con el encargo en curso."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from motor.encargos.registro import Registro
from motor.integraciones import cli as director_cli


class DirectorCorregir(unittest.TestCase):
    def _correr(self, argv: list[str]) -> tuple[int, dict]:
        with contextlib.redirect_stdout(io.StringIO()) as salida:
            codigo = director_cli.main(argv)
        texto = salida.getvalue().strip()
        return codigo, json.loads(texto) if texto else {}

    def _encargar(self, base: Path, director: str = "hermes", sesion: str = "") -> str:
        argv = [
            "encargar", "--base", str(base), "--director", director,
            "--via", "durable", "--canal", director, "--clave", "pedido-1",
            "--texto", "Crea dato.txt con 42",
        ]
        if sesion:
            argv += ["--sesion", sesion]
        codigo, recibo = self._correr(argv)
        self.assertEqual(codigo, 0)
        return recibo["encargo"]

    def _corregir(self, base: Path, encargo: str, **kw) -> tuple[int, dict]:
        argv = [
            "corregir", encargo, "--base", str(base),
            "--clave", kw.get("clave", "corr-1"),
            "--texto", kw.get("texto", "usa UTF-8"),
            "--canal", kw.get("canal", "hermes"),
        ]
        if kw.get("sesion"):
            argv += ["--sesion", kw["sesion"]]
        return self._correr(argv)

    def test_de_punta_a_punta_con_el_encargo_recibido(self):
        for director in ("hermes", "claw", "openbot"):
            with self.subTest(director=director), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp) / "base"
                encargo = self._encargar(base, director)
                codigo, salida = self._corregir(base, encargo, canal=director)
                self.assertEqual(codigo, 0)
                self.assertNotIn("rechazada", salida)
                self.assertEqual(salida["estado"], "recibido")
                reg = Registro(base)
                self.addCleanup(reg.close)
                self.assertEqual(reg.fold(encargo).ajustes, ("usa UTF-8",))

    def test_la_misma_clave_no_duplica_y_otro_texto_es_conflicto(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            encargo = self._encargar(base)
            _, primera = self._corregir(base, encargo)
            _, repetida = self._corregir(base, encargo)
            _, otra = self._corregir(base, encargo, texto="otro texto")
            self.assertEqual(repetida["recibo"], primera["recibo"])
            self.assertTrue(repetida["retransmision"])
            self.assertTrue(otra["conflicto"])
            reg = Registro(base)
            self.addCleanup(reg.close)
            self.assertEqual(reg.fold(encargo).ajustes, ("usa UTF-8",))

    def test_otro_canal_no_corrige_el_encargo(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            encargo = self._encargar(base)
            codigo, salida = self._corregir(base, encargo, canal="claw")
            self.assertEqual(codigo, 2)
            self.assertIn("canal", salida["motivo"])
            reg = Registro(base)
            self.addCleanup(reg.close)
            self.assertEqual(reg.fold(encargo).ajustes, ())

    def test_otra_sesion_no_corrige_el_encargo(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            encargo = self._encargar(base, sesion="sesion-a")
            codigo, salida = self._corregir(base, encargo, sesion="sesion-b")
            self.assertEqual(codigo, 2)
            self.assertIn("identidad", salida["motivo"])
            codigo, _ = self._corregir(base, encargo, clave="corr-2", sesion="sesion-a")
            self.assertEqual(codigo, 0)

    def test_encargo_cerrado_se_rechaza_con_su_motivo(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            encargo = self._encargar(base)
            codigo, _ = self._correr(
                ["cancelar", encargo, "--base", str(base), "--clave", "cancela-1"]
            )
            self.assertEqual(codigo, 0)
            reg = Registro(base)
            self.addCleanup(reg.close)
            reg.anotar(
                encargo,
                {"kind": "arbol", "data": {"observacion": "ausente"}},
                "t-arbol",
            )
            self.assertEqual(reg.fold(encargo).estado, "cancelado")
            codigo, salida = self._corregir(base, encargo)
            self.assertEqual(codigo, 2)
            self.assertIn("cancelado", salida["motivo"])

    def test_encargo_inexistente_se_rechaza(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            codigo, salida = self._corregir(base, "enc_no_existe")
            self.assertEqual(codigo, 2)
            self.assertEqual(salida["motivo"], "no hay encargo")


if __name__ == "__main__":
    unittest.main()
