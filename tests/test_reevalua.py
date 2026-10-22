"""MOTOR 02, bloque F: el plan y la máquina pueden corregirse a media corrida."""

from __future__ import annotations

import ast
import inspect
import unittest
import unittest.mock
from pathlib import Path

from motor import claves

RAIZ = Path(__file__).resolve().parent.parent


def _llamadas_con_ukey():
    """(archivo, línea, tipo de evento o None, valor de ukey) de cada `x.append(..., ukey=...)` del motor."""
    for f in sorted((RAIZ / "motor").rglob("*.py")):
        if f.name == "claves.py":
            continue
        for nodo in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if not isinstance(nodo, ast.Call):
                continue
            for kw in nodo.keywords:
                if kw.arg == "ukey":
                    tipo = None
                    if len(nodo.args) >= 3 and isinstance(nodo.args[2], ast.Constant):
                        tipo = nodo.args[2].value
                    yield f.name, kw.value.lineno, tipo, kw.value


class ClavesTest(unittest.TestCase):
    def test_ninguna_otra_parte_arma_una_a_mano(self):
        llamadas = list(_llamadas_con_ukey())
        self.assertGreater(len(llamadas), 20, "el recorrido encontró las llamadas")
        for archivo, linea, _, valor in llamadas:
            es_de_claves = (
                isinstance(valor, ast.Call)
                and isinstance(valor.func, ast.Attribute)
                and isinstance(valor.func.value, ast.Name)
                and valor.func.value.id == "claves"
            )
            self.assertTrue(es_de_claves, f"{archivo}:{linea} arma su ukey a mano")

    def test_cada_tipo_de_evento_usa_la_funcion_de_su_tipo(self):
        vistos = set()
        for archivo, linea, tipo, valor in _llamadas_con_ukey():
            if tipo is None:
                continue
            vistos.add(tipo)
            self.assertIn(tipo, claves.POR_TIPO, f"{archivo}:{linea}: {tipo} sin clave")
            self.assertEqual(
                valor.func.attr,
                claves.POR_TIPO[tipo].__name__,
                f"{archivo}:{linea}: {tipo} usa la clave de otro tipo",
            )
        self.assertEqual(vistos, set(claves.POR_TIPO), "sobra o falta un tipo")

    def test_ninguna_depende_del_reloj(self):
        fuente = inspect.getsource(claves)
        for palabra in ("import time", "datetime", "uuid", "random"):
            self.assertNotIn(palabra, fuente)
        with unittest.mock.patch("time.time", side_effect=[1.0, 2.0] * 50):
            antes = [
                claves.falla_de_efecto("p", "A", "Verify", 3),
                claves.checks_resultado("p", "A", "a" * 40, rechazo=2),
                claves.efecto_ok("p", "A", "Verify", 3),
            ]
        with unittest.mock.patch("time.time", return_value=9e9):
            despues = [
                claves.falla_de_efecto("p", "A", "Verify", 3),
                claves.checks_resultado("p", "A", "a" * 40, rechazo=2),
                claves.efecto_ok("p", "A", "Verify", 3),
            ]
        self.assertEqual(antes, despues)

    def test_lo_que_se_repite_de_verdad_se_numera(self):
        s = "a" * 40
        self.assertNotEqual(
            claves.falla_de_efecto("p", "A", "Verify", 1),
            claves.falla_de_efecto("p", "A", "Verify", 2),
        )
        self.assertNotEqual(
            claves.checks_resultado("p", "A", s, rechazo=0),
            claves.checks_resultado("p", "A", s, rechazo=1),
        )
        self.assertNotEqual(
            claves.checks_resultado("p", "A", s),
            claves.checks_resultado("p", "A", s, 0),
        )

    def test_ni_gruesas_ni_finas(self):
        s, o = "a" * 40, "b" * 40
        self.assertEqual(claves.integrado("p", "A", s), claves.integrado("p", "A", s))
        for distinta in (
            claves.integrado("q", "A", s),
            claves.integrado("p", "B", s),
            claves.integrado("p", "A", o),
        ):
            self.assertNotEqual(claves.integrado("p", "A", s), distinta)
        self.assertNotEqual(
            claves.intento_iniciado("p", "A", "implementar", 1),
            claves.intento_iniciado("p", "A", "revisar", 1),
        )
        self.assertNotEqual(
            claves.checks_esperando("p", "A", s, ("x",), False),
            claves.checks_esperando("p", "A", s, ("x",), True),
        )
        self.assertNotEqual(
            claves.checks_esperando("p", "A", s, ("x",), False),
            claves.checks_esperando("p", "A", o, ("x",), False),
        )

    def test_un_resultado_de_otro_insumo_es_otra_clave(self):
        s = "a" * 40
        for f in (claves.verificado, claves.probado, claves.desplegado):
            self.assertNotEqual(f("p", "A", s, "h1", "m1"), f("p", "A", s, "h2", "m1"))
            self.assertNotEqual(f("p", "A", s, "h1", "m1"), f("p", "A", s, "h1", "m2"))
            self.assertEqual(f("p", "A", s, "h1", "m1"), f("p", "A", s, "h1", "m1"))
            self.assertEqual(
                f("p", "A", s), f("p", "A", s, "", ""), "sin huella, como antes"
            )


if __name__ == "__main__":
    unittest.main()
