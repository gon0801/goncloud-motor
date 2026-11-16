"""La tabla de entrega usa el estado observado del PR, no el nivel alcanzado."""

from __future__ import annotations

import unittest
from dataclasses import replace

from motor.encargos.entrega.tipos import (
    PrAbierto, PrBorrador, PrCerrado, PrMergeado, PrSin,
)


class TablaDeEntrega(unittest.TestCase):
    def test_las_dieciseis_celdas_del_pr(self) -> None:
        from motor.encargos.entrega.decision import ContextoEntrega, decidir

        sin = PrSin()
        borrador = PrBorrador(7, "h", "main")
        abierto = PrAbierto(7, "h", "main")
        mergeado = PrMergeado(7, "h", "main", "m")
        en_cadena = PrMergeado(7, "h", "encargo/G", "m")
        otra = PrMergeado(7, "h", "otra", "m")
        cerrado = PrCerrado(7, "h", "main")
        casos = (
            (sin, "pr", "AbrirPR"),
            (sin, "integrado", "AbrirPR"),
            (borrador, "pr", "MarcarListo"),
            (borrador, "integrado", "MarcarListo"),
            (abierto, "pr", "SeguirRevision"),
            (abierto, "integrado", "SeguirRevision"),
            (mergeado, "pr", "AnotarMergeado"),
            (mergeado, "integrado", "AnotarMergeado"),
            (en_cadena, "pr", "AnotarObservado"),
            (en_cadena, "integrado", "AnotarObservado"),
            (otra, "pr", "DetenerOtraBase"),
            (otra, "integrado", "DetenerOtraBase"),
            (cerrado, "pr", "CancelarFase"),
            (cerrado, "integrado", "CancelarFase"),
        )
        for pr, nivel, esperado in casos:
            with self.subTest(pr=pr.estado, base=getattr(pr, "base", ""), nivel=nivel):
                ctx = ContextoEntrega("F", nivel, pr, "main", otras_ramas=(
                    ("encargo/G", abierto, ""),
                ))
                self.assertEqual(type(decidir(ctx)).__name__, esperado)

        for integrado, esperado in ((False, "AnotarObservado"),
                                     (True, "AnotarMergeado")):
            with self.subTest(cadena_integrada=integrado):
                g = (mergeado if integrado else abierto)
                ctx = ContextoEntrega("F", "pr", en_cadena, "main", otras_ramas=(
                    ("encargo/G", g, ""),
                ))
                self.assertEqual(type(decidir(ctx)).__name__, esperado)

    def test_nada_esperar_y_aceptacion_de_otra_base(self) -> None:
        from motor.encargos.entrega.decision import ContextoEntrega, decidir

        mergeado = PrMergeado(7, "h", "otra", "m")
        cerrado = PrCerrado(7, "h", "main")
        abierto = PrAbierto(7, "h", "main")
        casos = (
            ({"cancelado": True, "pr": abierto}, "Nada"),
            ({"canceladas": ("F",), "pr": cerrado}, "Nada"),
            ({"final": True, "pr": mergeado}, "AnotarObservado"),
            ({"pr": cerrado, "proceso_vivo": True}, "Esperar"),
            ({"pr": mergeado, "reserva": True}, "Esperar"),
            ({"pr": mergeado, "aceptada_en": "otra"}, "AnotarMergeado"),
            ({"pr": mergeado, "otra_base_de": ("F", "otra")}, "Nada"),
            ({"pr": abierto, "revision_lista": True, "nivel": "desplegado"},
             "Mergear"),
        )
        for cambios, esperado in casos:
            with self.subTest(cambios=cambios):
                ctx = ContextoEntrega("F", "pr", abierto, "main")
                ctx = replace(ctx, **cambios)
                self.assertEqual(type(decidir(ctx)).__name__, esperado)


if __name__ == "__main__":
    unittest.main()
