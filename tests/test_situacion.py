from __future__ import annotations
from motor.encargos.encargo import evolucionar

import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest import mock

from motor.encargos.dominio import Efecto, Fase, TransicionIlegal, reducir, vista
from tools import plegado
from tools.plegado import Reductor, Traza


def _entrega() -> tuple[dict, object]:
    evento = {"id": 1, "kind": "entrega", "data": {
        "id": "e", "clave": "e", "huella": "h", "texto": "pedido",
    }}
    return evento, reducir(None, evento, nuevo=False)


def _vista_situacion(e) -> dict:
    campos = asdict(e.situacion)
    campos.pop("contexto", None)
    return campos


class SituacionComoLectura(unittest.TestCase):
    def test_comprobar_no_cambia_el_veredicto_del_canario(self) -> None:
        from types import SimpleNamespace

        from ops import canario_juzgar, canario_observar

        self.assertIn("situacion_tipada", dict(plegado.INVARIANTES_COMPROBAR))
        evento, _ = _entrega()
        evento["ts"] = "2026-11-15T00:00:00Z"

        def reductor(estado, hecho, nuevo=False):
            return evolucionar(reducir(estado, hecho, nuevo), estado="ejecutando", ultimo_hecho="")

        with mock.patch.object(plegado, "cargar_reductor",
                               return_value=Reductor(reductor, ("ejecutando",))):
            pliegue, _ = canario_observar._plegar([evento])
        self.assertEqual(pliegue.violaciones, ())
        self.assertEqual(canario_juzgar._legible(SimpleNamespace(pliegue=pliegue), None)[0], "pasa")

    def test_variantes_se_guardan_en_encargo_sin_duplicar_estado(self) -> None:
        from motor.encargos.situacion import Cancelado, Recibido, situacion_de
        from dataclasses import fields

        _, e = _entrega()
        anterior = asdict(e)
        recibido = situacion_de(e)
        self.assertIsInstance(recibido, Recibido)
        self.assertIs(e.situacion, recibido)
        self.assertEqual(_vista_situacion(e), {"estado": "recibido"})
        self.assertNotIn("estado", {campo.name for campo in fields(e)})
        self.assertEqual(_vista_situacion(evolucionar(e, estado="cancelado")),
                         {"estado": "cancelado"})
        self.assertIsInstance(situacion_de(evolucionar(e, estado="cancelado")), Cancelado)
        self.assertEqual(asdict(e), anterior)

    def test_las_cuatro_incoherencias_conservan_sus_mensajes(self) -> None:
        from motor.encargos.situacion import EstadoIncoherente, situacion_de

        self.assertTrue(issubclass(EstadoIncoherente, TransicionIlegal))
        _, e = _entrega()
        casos = (
            ({"estado": "esperando_reintento"}, "espera incompleta"),
            ({"estado": "conciliando"}, "conciliando sin efecto incierto"),
            ({"estado": "terminado"}, "terminado sin evidencia"),
            ({"estado": "ejecutando", "ultimo_hecho": ""}, "ejecutando sin hecho"),
        )
        for cambios, mensaje in casos:
            with self.subTest(mensaje=mensaje), self.assertRaisesRegex(EstadoIncoherente, mensaje):
                situacion_de(evolucionar(e, **cambios))

    def test_vista_delega_sus_errores_y_contencion_muestra_la_minima(self) -> None:
        from motor.encargos.contencion import vista_o_minima
        from motor.encargos.situacion import EstadoIncoherente

        _, e = _entrega()
        roto = evolucionar(e, estado="terminado")
        with self.assertRaisesRegex(EstadoIncoherente, "terminado sin evidencia"):
            vista(roto)
        self.assertEqual(vista_o_minima(roto), {"encargo": e.id, "estado": "terminado"})

    def test_variantes_conservan_datos_reales_de_cada_estado(self) -> None:
        _, e = _entrega()
        casos = (
            ({"estado": "ejecutando", "fase_activa": "F", "intento": "I",
              "reserva": True, "ultimo_hecho": "en marcha"},
             {"estado": "ejecutando", "fase": "F", "intento": "I", "proc": None,
              "reservado": True}),
            ({"estado": "esperando_reintento", "motivo": "red", "reanuda_en": "mañana"},
             {"estado": "esperando_reintento", "motivo": "red", "reanuda_en": "mañana"}),
            ({"estado": "conciliando", "efectos": (Efecto("ef", "d", "op", "c", "incierto"),)},
             {"estado": "conciliando", "efecto": "ef"}),
            ({"estado": "deteniendo", "orden_parada": "cancelar",
              "intento_interrumpido": True},
             {"estado": "deteniendo", "orden": {"accion": "cancelar"},
              "interrumpido": True}),
            ({"estado": "detenido", "orden_parada": "fallo_interno"},
             {"estado": "detenido", "causa": {
                 "kind": "", "clase_ukey": "", "rechazo": "",
             }}),
        )
        for cambios, esperado in casos:
            with self.subTest(estado=cambios["estado"]):
                self.assertEqual(_vista_situacion(evolucionar(e, **cambios)), esperado)

    def test_terminado_con_fases_completas_y_pendientes_no_es_parcial_vacio(self) -> None:
        from motor.encargos.situacion import Completo, Parcial, Terminado, situacion_de

        _, e = _entrega()
        fase = Fase("F", "resultado", estado="preparada", evidencia="comprobado")
        cerrado = evolucionar(e, estado="terminado", fases=(fase,), evidencia=("probado",),
                          pendientes_entrega=("pendiente de entrega",))
        self.assertTrue(vista(cerrado)["objetivo"]["completo"])
        self.assertEqual(situacion_de(cerrado), Terminado(Completo(), None))
        incompleto = evolucionar(cerrado, fases=(Fase("F", "resultado"),),
                             cierre_forzado_fase=fase, cierre_forzado_huella="h")
        self.assertEqual(
            _vista_situacion(incompleto),
            {"resultado": asdict(Parcial(("F",))), "forzado": {"fase": "F", "huella": "h"},
             "estado": "terminado"},
        )

    def test_comprobar_mide_incoherencias_y_dependencia_del_estado(self) -> None:
        evento, e = _entrega()
        nombres = dict(plegado.INVARIANTES_COMPROBAR)
        self.assertIn("situacion_tipada", nombres)
        self.assertIn("datos_dependen_del_estado", nombres)
        reductor = Reductor(reducir, ("recibido",))
        incoherente = evolucionar(e, estado="terminado")
        hallado = nombres["situacion_tipada"](
            reductor, [evento], Traza(estados=[incoherente]),
        )
        self.assertIsNotNone(hallado)
        self.assertIn("terminado sin evidencia", hallado[1])
        posterior = {"id": 2, "kind": "recibo", "data": {}}
        dependencia = nombres["datos_dependen_del_estado"](
            reductor, [evento, posterior],
            Traza(estados=[e, replace(e, escrituras=False)]),
        )
        self.assertIsNotNone(dependencia)
        self.assertEqual(dependencia[0]["id"], 2)

    def test_comprobar_compara_datos_del_estado_entre_encargos(self) -> None:
        evento, e = _entrega()
        eventos = [evento]
        trazas = [
            Traza(estados=[e]),
            Traza(estados=[replace(e, escrituras=False)]),
        ]
        with (
            mock.patch.object(plegado, "ultimo_evento", return_value=2),
            mock.patch.object(plegado, "leer_encargos",
                              return_value=iter((("uno", eventos), ("dos", eventos)))),
            mock.patch.object(plegado, "trazar", side_effect=trazas),
            mock.patch.object(plegado, "INVARIANTES_COMPROBAR",
                              (("datos_dependen_del_estado",
                                dict(plegado.INVARIANTES_COMPROBAR)["datos_dependen_del_estado"]),)),
        ):
            resultado = plegado.comprobar(Reductor(reducir, ("recibido",)), Path("registro.sqlite"))
        self.assertEqual(
            [(v.invariante, v.encargo) for v in resultado.violaciones],
            [("datos_dependen_del_estado", "dos")],
        )

    def test_orden_reabrir_no_se_confunde_con_importar_ni_parar(self) -> None:
        from motor.encargos.situacion import PorOrden, situacion_de

        _, e = _entrega()
        importado = evolucionar(e, estado="detenido", orden_parada="")
        self.assertEqual(situacion_de(importado).causa, PorOrden("importar"))
        fase = Fase("F", "resultado")
        cerrado = evolucionar(e, estado="terminado", fases=(fase,), fase_activa=fase.id,
                          cierre_forzado_fase=fase, cierre_forzado_huella="huella",
                          evidencia=("comprobado",))
        reabierto = reducir(cerrado, {"kind": "orden", "data": {
            "accion": "reabrir", "fase": fase.id, "cierre": "huella",
        }}, nuevo=False)
        self.assertEqual(situacion_de(reabierto).causa, PorOrden("reabrir"))
        parado = reducir(reabierto, {"kind": "orden", "data": {"accion": "parar"}}, nuevo=False)
        self.assertEqual(
            situacion_de(evolucionar(parado, estado="detenido")).causa,
            PorOrden("parar"),
        )

    def test_comprobar_recuerda_estados_tras_primera_divergencia(self) -> None:
        evento, e = _entrega()
        eventos_a = [evento, {"id": 2, "kind": "recibo"}, {"id": 3, "kind": "recibo"}]
        terminado = evolucionar(e, estado="terminado", escrituras=False,
                            intentos_nuevos=False, evidencia=("si",))
        trazas = [
            Traza(estados=[e, replace(e, escrituras=False), terminado]),
            Traza(estados=[replace(terminado, escrituras=True)]),
        ]
        with (
            mock.patch.object(plegado, "ultimo_evento", return_value=3),
            mock.patch.object(plegado, "leer_encargos",
                              return_value=iter((("uno", eventos_a), ("dos", [evento])))),
            mock.patch.object(plegado, "trazar", side_effect=trazas),
            mock.patch.object(plegado, "INVARIANTES_COMPROBAR",
                              (("datos_dependen_del_estado",
                                dict(plegado.INVARIANTES_COMPROBAR)["datos_dependen_del_estado"]),)),
        ):
            resultado = plegado.comprobar(Reductor(reducir, ("recibido", "terminado")),
                                          Path("registro.sqlite"))
        self.assertEqual(
            [(v.invariante, v.encargo) for v in resultado.violaciones],
            [("datos_dependen_del_estado", "uno"),
             ("datos_dependen_del_estado", "dos")],
        )


if __name__ == "__main__":
    unittest.main()
