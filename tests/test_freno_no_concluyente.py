"""Traba 4 del 30 y 31 de octubre: el freno `sin_progreso` no contaba las
revisiones no_concluyente. Cinco vueltas del revisor sobre la misma versión,
pidiendo lo mismo, dejaban `sin_progreso=0` y gastaban turnos sin pedir ayuda."""

from __future__ import annotations

import unittest

from motor.encargos.ciclo import ahora_iso, corregir
from motor.encargos.dominio import bloqueantes_abiertos
from tests.test_flujo_revisiones import _bloqueante
from tests.test_respuestas_buzon import _Flujo

FALTANTE = "el log del job jaula-linux del CI de Linux"


def _pide(ruta: str, faltante: str = FALTANTE) -> dict:
    return {
        "veredicto": "no_concluyente",
        "evidencia": f"no se encontro {ruta}",
        "inspeccion_faltante": faltante,
        "inspeccion": {
            "operacion": "remoto.leer",
            "recurso": "ci-github",
            "parametros": {"ruta": ruta},
        },
    }


class _Freno(_Flujo):
    def vuelta(self, r, ruta: str, faltante: str = FALTANTE) -> None:
        """Un turno del revisor como el lanzador, y el barrido que le sigue."""
        self.abrir_turno(r)
        r.juicio(_pide(ruta, faltante))
        r.incorporar()
        self.cerrar_turno(r)
        self.barrer(r)


class RevisionesNoConcluyentesSinAvance(_Freno):
    def test_tres_vueltas_pidiendo_lo_mismo_piden_ayuda_con_la_causa(self):
        r = self.recorrido("freno-1", con_recurso=True)
        self.herramienta_ok()

        self.vuelta(r, "jaula-linux-a.txt")
        enc = r.fold()
        self.assertEqual((enc.no_concluyentes, enc.sin_progreso), (1, 0))

        self.vuelta(r, "jaula-linux-b.txt")
        enc = r.fold()
        self.assertEqual((enc.no_concluyentes, enc.sin_progreso), (2, 0))
        self.assertNotEqual(enc.estado, "necesita_usuario")

        self.lanzamientos.clear()
        self.vuelta(r, "jaula-linux-c.txt")
        enc = r.fold()
        # El revisor no concluyente ya no detiene el encargo: pasa a otro
        # revisor autorizado.
        self.assertNotEqual(enc.estado, "necesita_usuario")
        self.assertEqual(enc.relevos, 1)
        self.assertIn("3 veces seguidas", enc.motivo)
        self.assertIn(FALTANTE, enc.motivo)
        self.assertIn("no se encontro jaula-linux-c.txt", enc.motivo)
        self.assertEqual(len(enc.solicitudes), 2)
        self.assertEqual(enc.fases[0].inspeccion_pendiente, "")
        self.assertEqual([rol for rol, _ in self.lanzamientos], ["revisor"])
        self.assertEqual(enc.pendientes_entrega, ())

        # El revisor de relevo tampoco concluye: el barrido entrega con la nota.
        self.lanzamientos.clear()
        self.vuelta(r, "jaula-linux-d.txt")
        enc = r.fold()
        self.assertEqual(enc.estado, "terminado")
        self.assertTrue(
            any("no concluyó" in n for n in enc.evidencia), enc.evidencia
        )

    def test_un_ajuste_del_propietario_empieza_otra_cuenta_y_abre_el_turno(self):
        r = self.recorrido("freno-2", con_recurso=True)
        self.herramienta_ok()
        for ruta in ("a.txt", "b.txt", "c.txt"):
            self.vuelta(r, ruta)
        self.assertEqual(r.fold().relevos, 1)
        self.assertNotEqual(r.fold().estado, "necesita_usuario")

        corregir(r.reg, r.enc, "El log esta en indice.txt.", "aj-1", ahora_iso())
        enc = r.fold()
        self.assertEqual((enc.no_concluyentes, enc.relevos), (0, 0))
        self.assertEqual(enc.estado, "recibido")
        self.lanzamientos.clear()
        self.barrer(r)
        self.assertEqual([rol for rol, _ in self.lanzamientos], ["revisor"])

    def test_otra_version_no_empieza_la_cuenta(self):
        r = self.recorrido("freno-3", con_recurso=True)
        self.herramienta_ok()
        self.vuelta(r, "a.txt")
        self.vuelta(r, "b.txt")
        self.assertEqual(r.fold().no_concluyentes, 2)

        (r.espacio / "dato.txt").write_text("43")
        self.vuelta(r, "c.txt")

        enc = r.fold()
        versiones = [x["data"]["version"] for x in r.eventos("revision")]
        self.assertEqual(len(set(versiones)), 2, versiones)
        self.assertEqual(enc.no_concluyentes, 3)
        self.assertEqual(enc.relevos, 1)
        self.assertNotEqual(enc.estado, "necesita_usuario")

    def test_un_recurso_que_cambio_tambien_cuenta_hasta_el_tope(self):
        r = self.recorrido("freno-4", con_recurso=True)
        self.herramienta_ok()
        self.vuelta(r, "indice.txt")
        self.assertEqual(r.fold().no_concluyentes, 1)
        (self.raiz_ci / "indice.txt").write_text("contenido " * 2)
        self.vuelta(r, "indice.txt")
        self.assertEqual(r.fold().no_concluyentes, 2)
        (self.raiz_ci / "indice.txt").write_text("contenido " * 3)
        self.vuelta(r, "indice.txt")
        enc = r.fold()
        self.assertEqual(enc.no_concluyentes, 3)
        self.assertEqual(enc.relevos, 1)
        self.assertNotEqual(enc.estado, "necesita_usuario")

    def test_pedidos_distintos_en_la_misma_fase_cuentan(self):  # no-roja-en-base: la base ya cuenta pedidos distintos por separado; la fase amplia la cuenta a versiones nuevas, cubierta por las pruebas rojas
        r = self.recorrido("freno-5", con_recurso=True)
        self.herramienta_ok()
        self.vuelta(r, "a.txt", "el log del job jaula-linux")
        self.vuelta(r, "b.txt", "el hash del commit integrado")
        enc = r.fold()
        self.assertEqual(enc.no_concluyentes, 2)
        self.assertNotEqual(enc.estado, "necesita_usuario")

    def test_el_freno_de_bloqueos_no_hereda_las_vueltas_de_inspeccion(self):  # no-roja-en-base: la base ya lleva las dos cuentas separadas; la prueba conserva el limite mientras la fase amplia la de no_concluyentes
        r = self.recorrido("freno-7", con_recurso=True)
        self.herramienta_ok()
        bloqueante = [_bloqueante("Fuga de token en el log")]
        for ruta in ("a.txt", "b.txt"):
            self.abrir_turno(r)
            r.juicio({**_pide(ruta), "hallazgos": bloqueante})
            r.incorporar()
            self.cerrar_turno(r)
            self.barrer(r)
        self.assertEqual(r.fold().no_concluyentes, 2)

        self.abrir_turno(r)
        r.juicio(
            {
                "veredicto": "cambios",
                "bloqueantes": bloqueante,
                "evidencia": "lei el log; el bloqueo sigue",
            }
        )
        r.incorporar()
        self.cerrar_turno(r)

        enc = r.fold()
        self.assertEqual(enc.no_concluyentes, 0)
        self.assertEqual(enc.sin_progreso, 0)
        self.assertEqual(enc.estrategia, "investigar")
        self.assertTrue(bloqueantes_abiertos(enc))
        self.assertNotEqual(enc.estado, "necesita_usuario")
        self.assertNotIn("tras el diagnostico", enc.motivo)

    def test_la_no_concluyente_sin_inspeccion_tambien_cuenta(self):
        r = self.recorrido("freno-6", con_recurso=True)
        self.herramienta_ok()
        self.vuelta(r, "a.txt")
        self.vuelta(r, "b.txt")
        self.assertEqual(r.fold().no_concluyentes, 2)
        self.abrir_turno(r)
        r.juicio({"veredicto": "no_concluyente", "evidencia": "falta", "nota": "sin pedido"})
        r.incorporar()
        self.cerrar_turno(r)
        enc = r.fold()
        self.assertEqual(enc.no_concluyentes, 3)
        self.assertEqual(enc.relevos, 1)
        self.assertNotEqual(enc.estado, "necesita_usuario")


if __name__ == "__main__":
    unittest.main()
