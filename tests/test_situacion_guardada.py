from __future__ import annotations

import unittest
from dataclasses import fields

from motor.encargos.dominio import Encargo, Fase, Proc, reducir
from tools.plegado import campos_de


def _pedido() -> Encargo:
    return reducir(None, {"kind": "entrega", "data": {
        "id": "e", "clave": "e", "huella": "h", "texto": "pedido",
    }}, nuevo=False)


class SituacionGuardada(unittest.TestCase):
    def test_estado_tiene_un_solo_dueno_y_la_proyeccion_legada_no_lo_pierde(self) -> None:
        from motor.encargos.dominio import CAMPOS_LEGADOS
        from motor.encargos.situacion import Recibido

        plano = _pedido()
        self.assertIsInstance(plano.situacion, Recibido)
        nombres = {campo.name for campo in fields(plano)}
        self.assertIn("situacion", nombres)
        self.assertEqual(set(CAMPOS_LEGADOS), {
            "estado", "orden_parada", "reanuda_en", "intento", "proc", "reserva",
            "intento_interrumpido", "cierre_forzado_fase", "cierre_forzado_huella",
        })
        self.assertTrue(set(CAMPOS_LEGADOS).isdisjoint(nombres))
        self.assertTrue({"escrituras", "intentos_nuevos", "evidencia"} <= nombres)
        self.assertEqual(campos_de(plano)["estado"], "recibido")
        self.assertNotIn("situacion", campos_de(plano))

    def test_recibido_conserva_contexto_de_intento_y_cierre_historico(self) -> None:
        from motor.encargos.encargo import evolucionar
        from motor.encargos.situacion import Recibido

        fase = Fase("F", "resultado")
        proc = Proc(1, "inicio", "boot")
        encargo = evolucionar(
            _pedido(), intento="i", proc=proc, reserva=True,
            reanuda_en="después", cierre_forzado_fase=fase,
            cierre_forzado_huella="huella",
        )
        self.assertIsInstance(encargo.situacion, Recibido)
        self.assertEqual(
            (encargo.intento, encargo.proc, encargo.reserva, encargo.reanuda_en,
             encargo.cierre_forzado_fase, encargo.cierre_forzado_huella),
            ("i", proc, True, "después", fase, "huella"),
        )
        legado = campos_de(encargo)
        self.assertNotIn("situacion", legado)
        self.assertEqual(legado["estado"], "recibido")
        self.assertEqual(legado["cierre_forzado_huella"], "huella")
        self.assertEqual(legado["proc"]["pid"], 1)


if __name__ == "__main__":
    unittest.main()
