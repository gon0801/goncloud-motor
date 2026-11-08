"""La escalera de cada rol sale de los ids y del registro, no de contadores.

Estas pruebas fallan con el codigo de 874daed: alli la escalera era un conjunto
de campos guardados que cada camino reiniciaba a mano.
"""

from __future__ import annotations

import dataclasses
import tempfile
import unittest

from motor.encargos import ciclo, dominio
from motor.encargos.dominio import relevos_de
from tests.test_escalera_ids import Recorrido, _bloqueante


class LaEscaleraNoEsUnConjuntoDeCamposGuardados(unittest.TestCase):
    def test_ningun_contador_ni_huella_vive_fuera_del_registro(self):
        nombres = {f.name for f in dataclasses.fields(dominio.Encargo)}
        for nombre in (
            "relevos",
            "relevo_huella",
            "sin_progreso",
            "bloqueo_huella",
            "relevos_por_rol",
            "relevo_huella_por_rol",
        ):
            self.assertNotIn(nombre, nombres, nombre)

    def test_las_dos_listas_de_reinicio_manual_ya_no_existen(self):
        self.assertFalse(hasattr(dominio, "_limpiar_para_continuar"))
        self.assertFalse(hasattr(dominio, "_reiniciar_escalera"))


class ContinuarNoReiniciaLaEscalera(unittest.TestCase):
    def _tras_el_diagnostico(self, tmp: str) -> Recorrido:
        r = Recorrido(tmp, "escalera-continuar", "malo.txt con B")
        self.addCleanup(r.reg.close)
        (r.espacio / "malo.txt").write_text("X")
        r.incorporar()
        r.juicio(
            {
                "veredicto": "cambios",
                "evidencia": "lei malo.txt",
                "bloqueantes": [_bloqueante("malo.txt no contiene B")],
            }
        )
        r.incorporar()
        e = r.incorporar()
        self.assertEqual(e.sin_progreso, 1)
        self.assertEqual(relevos_de(e, "responsable"), 0)
        return r

    def _detener(self, r: Recorrido) -> None:
        hora = ciclo.ahora_iso()
        r.reg.anotar(
            r.enc, {"kind": "orden", "data": {"accion": "parar", "hora": hora}},
            "parar-escalera",
        )
        r.reg.anotar(
            r.enc,
            {"kind": "arbol", "data": {"observacion": "ausente", "hora": hora}},
            "arbol-escalera",
        )
        self.assertEqual(r.fold().estado, "detenido")

    def test_continuar_conserva_el_diagnostico_y_la_siguiente_vuelta_releva(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._tras_el_diagnostico(tmp)
            self._detener(r)

            ciclo.ordenar(r.reg, r.enc, "continuar", "continuar-escalera", ciclo.ahora_iso())
            continuado = r.fold()
            self.assertEqual(continuado.sin_progreso, 1)

            e = r.incorporar()
            self.assertEqual(relevos_de(e, "responsable"), 1)
            self.assertEqual(e.pendientes_entrega, ())


if __name__ == "__main__":
    unittest.main()
