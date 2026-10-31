"""`motor director corregir`: los directores mandan correcciones con el encargo en curso."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from motor.encargos import cli as motor_cli
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
            codigo_primera, primera = self._corregir(base, encargo)
            codigo_repetida, repetida = self._corregir(base, encargo)
            codigo_otra, otra = self._corregir(base, encargo, texto="otro texto")
            self.assertEqual(repetida["recibo"], primera["recibo"])
            self.assertTrue(repetida["retransmision"])
            self.assertTrue(otra["conflicto"])
            self.assertEqual(
                (codigo_primera, codigo_repetida, codigo_otra), (0, 0, 2)
            )
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


class MotorCorregir(unittest.TestCase):
    """`motor corregir` sale con 2 cuando hay conflicto o rechazo."""

    def _correr(self, argv: list[str]) -> tuple[int, dict]:
        with contextlib.redirect_stdout(io.StringIO()) as salida:
            codigo = motor_cli.main(argv)
        return codigo, json.loads(salida.getvalue().strip())

    def test_conflicto_y_rechazo_salen_con_codigo_distinto_de_cero(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            codigo, recibo = self._correr(
                [
                    "encargar", "--base", str(base), "--clave", "pedido-1",
                    "--texto", "Crea dato.txt con 42",
                ]
            )
            self.assertEqual(codigo, 0)
            encargo = recibo["encargo"]

            def corregir(clave: str, texto: str) -> tuple[int, dict]:
                return self._correr(
                    [
                        "corregir", encargo, "--base", str(base),
                        "--clave", clave, "--texto", texto,
                    ]
                )

            self.assertEqual(corregir("c1", "usa UTF-8")[0], 0)
            self.assertEqual(corregir("c1", "usa UTF-8")[0], 0)
            codigo, salida = corregir("c1", "otro texto")
            self.assertTrue(salida["conflicto"])
            self.assertEqual(codigo, 2)
            self.assertEqual(
                self._correr(["cancelar", encargo, "--base", str(base), "--clave", "x"])[0],
                0,
            )
            reg = Registro(base)
            self.addCleanup(reg.close)
            reg.anotar(
                encargo, {"kind": "arbol", "data": {"observacion": "ausente"}}, "t-arbol"
            )
            codigo, salida = corregir("c2", "tarde")
            self.assertTrue(salida["rechazada"])
            self.assertEqual(codigo, 2)


if __name__ == "__main__":
    unittest.main()
