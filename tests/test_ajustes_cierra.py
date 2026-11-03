"""El cierre del operador llega al revisor y no se reabre sin evidencia nueva."""

from __future__ import annotations

import tempfile
import unittest

from motor.encargos.ciclo import corregir
from motor.encargos.comprobacion import firma_producto
from motor.encargos.dominio import (
    alcance_de,
    bloqueantes_abiertos,
    prompt_revisor,
    reducir,
)
from tests.test_flujo_revisiones import Recorrido, sonda_sin_aislamiento_del_host


def setUpModule():
    sonda_sin_aislamiento_del_host()


def _bloqueante(h_id: str, reproduccion: str = "cat malo.txt") -> dict:
    return {
        "id": h_id,
        "gravedad": "bloqueante",
        "criterio": "comportamiento",
        "titulo": "malo.txt no cumple",
        "reproduccion": reproduccion,
        "ancla": "malo.txt:1",
    }


class CierreDelOperador(unittest.TestCase):
    def _con_bloqueantes(self, tmp: str, clave: str, bloqueantes):
        r = Recorrido(tmp, clave, "Crea malo.txt con bueno")
        (r.espacio / "malo.txt").write_text("malo")
        e = r.incorporar()
        self.assertEqual(e.rol, "revisor")
        r.juicio(
            {
                "veredicto": "cambios",
                "evidencia": "malo.txt dice malo",
                "bloqueantes": bloqueantes,
            }
        )
        e = r.incorporar()
        return r, e

    def _con_bloqueante(self, tmp: str, clave: str, h_id: str = "h_ab12cd34"):
        r, e = self._con_bloqueantes(tmp, clave, [_bloqueante(h_id)])
        self.assertEqual([h.id for h in bloqueantes_abiertos(e)], [h_id])
        return r, e

    def test_el_ajuste_cierra_el_bloqueante_y_el_revisor_no_lo_reabre_sin_evidencia(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, e = self._con_bloqueante(tmp, "cierra-1")
            self.addCleanup(r.reg.close)
            salida = corregir(
                r.reg,
                r.enc,
                "corregido y comprobado",
                "aj-cierra-1",
                r.hora,
                cierra=["h_ab12cd34"],
                evidencia="prueba real X",
            )
            self.assertNotEqual(salida.get("estado"), "rechazada")
            e = r.fold()
            self.assertEqual(bloqueantes_abiertos(e), ())
            cerrado = next(h for h in e.fase_actual.hallazgos if h.id == "h_ab12cd34")
            self.assertEqual(cerrado.cierre_evidencia, "prueba real X")
            self.assertIn("h_ab12cd34", e.fase_actual.cerrados)

            texto = prompt_revisor(e, alcance_de(e))
            self.assertIn("declaró cerrados", texto)
            self.assertIn("h_ab12cd34", texto)

            version = firma_producto(r.espacio)
            e = reducir(
                e,
                {
                    "kind": "revision",
                    "data": {
                        "fase": e.fase_activa,
                        "version": version,
                        "veredicto": "cambios",
                        "alcance": "completa",
                        "hallazgos": [dict(_bloqueante("h_ab12cd34"), estado="abierto")],
                    },
                },
            )
            self.assertEqual(bloqueantes_abiertos(e), ())
            cerrado = next(h for h in e.fase_actual.hallazgos if h.id == "h_ab12cd34")
            self.assertEqual(cerrado.estado, "resuelto")

            e = reducir(
                e,
                {
                    "kind": "revision",
                    "data": {
                        "fase": e.fase_activa,
                        "version": version,
                        "veredicto": "cambios",
                        "alcance": "completa",
                        "hallazgos": [
                            dict(
                                _bloqueante("h_ab12cd34", "otra prueba distinta"),
                                estado="abierto",
                            )
                        ],
                    },
                },
            )
            self.assertEqual([h.id for h in bloqueantes_abiertos(e)], ["h_ab12cd34"])

    def test_el_operador_puede_nombrar_los_bloqueantes_por_su_titulo(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, e = self._con_bloqueantes(
                tmp,
                "cierra-titulo",
                [
                    dict(_bloqueante("h_f11"), titulo="F11 fuga en el log"),
                    dict(_bloqueante("h_pc", "prueba crlf"), titulo="prueba-pc-crlf"),
                ],
            )
            self.addCleanup(r.reg.close)
            self.assertEqual(
                {h.id for h in bloqueantes_abiertos(e)}, {"h_f11", "h_pc"}
            )
            corregir(
                r.reg,
                r.enc,
                "cierro F11 y prueba-pc-crlf: ya estan corregidos",
                "aj-cierra-titulo",
                r.hora,
                evidencia="las dos pruebas pasan",
            )
            e = r.fold()
            self.assertEqual(bloqueantes_abiertos(e), ())
            self.assertEqual(
                {h.id for h in e.fase_actual.hallazgos if h.cierre_evidencia},
                {"h_f11", "h_pc"},
            )

    def test_el_texto_del_ajuste_tambien_declara_el_cierre(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, _e = self._con_bloqueante(tmp, "cierra-2")
            self.addCleanup(r.reg.close)
            corregir(
                r.reg,
                r.enc,
                "cierro h_ab12cd34: ya esta corregido",
                "aj-cierra-2",
                r.hora,
                evidencia="log de la prueba",
            )
            e = r.fold()
            self.assertEqual(bloqueantes_abiertos(e), ())
            cerrado = next(h for h in e.fase_actual.hallazgos if h.id == "h_ab12cd34")
            self.assertEqual(cerrado.cierre_evidencia, "log de la prueba")


if __name__ == "__main__":
    unittest.main()
