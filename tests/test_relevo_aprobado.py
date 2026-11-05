"""Una aprobación repetida no resuelve un bloqueo de entrega."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from motor.encargos import ciclo
from motor.encargos.dominio import decision_proceso, relevos_de, vista
from motor.encargos.registro import Registro


class RelevoConAprobacion(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.reg = Registro(Path(tmp.name) / "base")
        self.addCleanup(self.reg.close)
        self.id = "relevo-aprobado"
        self.hora = ciclo.ahora_iso()
        self.serie = 0
        self.anotar(
            "entrega", id=self.id, clave=self.id, huella="pedido",
            texto="Integrar el resultado revisado", entrega_nivel="integrado",
            repo="ejemplo/app", rama="cambio", base="main",
        )
        self.anotar("producto_listo", version="version-1", manifiesto=[])
        self.aprobar()

    def anotar(self, kind, **data):
        self.serie += 1
        resultado = self.reg.anotar(
            self.id, {"kind": kind, "data": {**data, "hora": self.hora}},
            f"prueba:{self.serie}",
        )
        self.assertNotEqual(resultado, "ilegal", (kind, data))
        return self.reg.fold(self.id)

    def aprobar(self):
        return self.anotar(
            "revision", veredicto="aprobado", version="version-1",
            revision_id=f"revision-{self.serie}", origen="independiente",
        )

    def nueva_vuelta(self):
        enc = self.reg.fold(self.id)
        self.anotar("reserva", rol="revisor", generacion=enc.generacion + 1)
        self.anotar("reserva_cerrada")

    def entrega_sin_avance(self):
        ciclo._sin_avance(
            self.reg, self.reg.fold(self.id),
            "la entrega sigue sin acreditar el resultado autorizado",
            "revisar de nuevo la misma versión aprobada", self.hora,
            "entrega", rol="revisor",
        )
        return self.reg.fold(self.id)

    def relevar(self):
        self.nueva_vuelta()
        self.entrega_sin_avance()
        self.nueva_vuelta()
        enc = self.entrega_sin_avance()
        self.assertEqual(relevos_de(enc, "revisor"), 1)
        return enc

    def test_aprobar_otra_vez_no_reinicia_el_diagnostico(self):
        self.nueva_vuelta()
        self.entrega_sin_avance()
        self.nueva_vuelta()
        self.aprobar()

        enc = self.entrega_sin_avance()

        self.assertEqual(relevos_de(enc, "revisor"), 1)
        self.assertEqual(enc.estado, "recibido")

    def test_el_relevo_que_aprueba_sin_avance_escala_y_no_abre_mas_turnos(self):
        self.relevar()
        self.nueva_vuelta()
        self.aprobar()

        enc = self.entrega_sin_avance()

        self.assertEqual(enc.estado, "necesita_usuario")
        self.assertEqual(relevos_de(enc, "revisor"), 1)
        self.assertTrue(enc.preguntas)
        self.assertIn("entrega", enc.motivo)
        self.assertEqual(enc.pendientes_entrega, ())
        self.assertFalse(
            decision_proceso(enc, "ausente", None, False, True, self.hora)["lanzar"]
        )
        self.assertEqual(vista(enc)["estado"], "necesita_usuario")

        lanzamientos = []
        for _ in range(3):
            ciclo.barrer(
                self.reg, self.hora, time.time(),
                lambda *args, **kw: lanzamientos.append((args, kw)),
            )
        enc = self.reg.fold(self.id)
        self.assertEqual(enc.estado, "necesita_usuario")
        self.assertEqual(lanzamientos, [])
        self.assertFalse(any(
            ev["kind"] == "cierre_forzado" for ev in self.reg.eventos(self.id)
        ))

    def test_concluir_una_revision_pendiente_si_empieza_otra_escalera(self):
        self.anotar("revision", veredicto="no_concluyente", version="version-1")
        self.relevar()

        enc = self.aprobar()

        self.assertEqual(enc.sin_progreso, 0)
        self.assertEqual(relevos_de(enc, "revisor"), 0)


if __name__ == "__main__":
    unittest.main()
