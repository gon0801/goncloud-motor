from __future__ import annotations

import unittest
from dataclasses import fields, replace

from motor.encargos.dominio import Encargo, Fase, Proc, reducir
from tools.plegado import campos_de


def _pedido() -> Encargo:
    return reducir(None, {"kind": "entrega", "data": {
        "id": "e", "clave": "e", "huella": "h", "texto": "pedido",
    }}, nuevo=False)


class SituacionGuardada(unittest.TestCase):
    def test_constructor_tipado_conserva_escritura_espera_y_detencion(self) -> None:
        from motor.encargos.situacion import (
            Cancelar, Deteniendo, Ejecutando, EsperandoReintento, situacion_de,
        )

        def directo(situacion) -> Encargo:
            return Encargo(
                id="e", clave="e", huella="h", director="", texto="pedido",
                adjuntos=(), restricciones=(), canal="", situacion=situacion,
                ultimo_hecho="hecho",
            )

        proc = Proc(123, "inicio", "boot")
        activo = directo(Ejecutando("F", "I", proc, True))
        self.assertEqual((activo.fase_activa, activo.intento, activo.proc, activo.reserva),
                         ("F", "I", proc, True))
        self.assertEqual(situacion_de(activo).fase, "F")
        self.assertEqual(situacion_de(replace(activo, fase_activa="G")).fase, "G")

        espera = directo(EsperandoReintento("red", "mañana"))
        self.assertEqual((espera.motivo, espera.reanuda_en), ("red", "mañana"))
        self.assertEqual(situacion_de(espera).motivo, "red")

        detiene = directo(Deteniendo(Cancelar(), True))
        self.assertEqual((detiene.orden_parada, detiene.intento_interrumpido),
                         ("cancelar", True))
        self.assertEqual(situacion_de(detiene).orden.accion, "cancelar")

    def test_constructor_tipado_conserva_cierre_forzado_y_rechaza_conflictos(self) -> None:
        from motor.encargos.situacion import (
            CierreForzado, Contexto, Ejecutando, EstadoIncoherente,
            Parcial, Terminado, situacion_de,
        )

        fase = Fase("F", "resultado")
        base = dict(id="e", clave="e", huella="h", director="", texto="pedido",
                    adjuntos=(), restricciones=(), canal="")
        cerrado = Encargo(**base, fases=(fase,), evidencia=("probado",),
                          situacion=Terminado(Parcial(("F",)), CierreForzado("F", "h")))
        self.assertEqual((cerrado.cierre_forzado_fase, cerrado.cierre_forzado_huella),
                         (fase, "h"))
        self.assertEqual(situacion_de(cerrado).forzado, CierreForzado("F", "h"))

        with self.assertRaises(EstadoIncoherente):
            Encargo(**base, situacion=Ejecutando(
                "F", "I", None, False, contexto=Contexto(intento="otro"),
            ))

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
