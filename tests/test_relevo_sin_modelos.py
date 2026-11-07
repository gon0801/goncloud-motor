"""Agotar los modelos de un relevo no autoriza una entrega incompleta."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from motor.encargos import ciclo, cli
from motor.encargos.aislamiento import SondaEntorno
from motor.encargos.dominio import relevos_de
from motor.github import FakeGitHub
from tests.test_flujo_revisiones import _entrega_pr


GLM = "zai-coding-plan/glm-5.3-flash"
OTRO = "opencode-go/deepseek-v4.1-flash"


class RelevoSinModelos(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.raiz = Path(tmp.name)
        vuelta, self.r = _entrega_pr(self, tmp.name, "relevo-glm", FakeGitHub)
        self.gh = vuelta.gh
        self.serie = 0
        self.anotar("ruta_agentes", agentes=["opencode"])
        self.args = SimpleNamespace(
            agente="", respaldo=[], una_vez=True, umbral=30, intervalo=1,
        )
        for destino, valor in (
            ("motor.encargos.aislamiento.sondear", SondaEntorno(True)),
            ("motor.encargos.cli._disponible", True),
            ("motor.encargos.cli._puerto_github", self.gh),
            ("motor.flota.runtime.exigir_poteto", (self.raiz / "SKILL.md", "ensayo")),
        ):
            self.enterContext(mock.patch(destino, return_value=valor))
        self.modelos = self.enterContext(
            mock.patch.object(cli, "_modelos_para", return_value=(GLM,))
        )
        self.enterContext(mock.patch.object(
            cli, "_lanzador", side_effect=AssertionError("no debe ejecutar un modelo"),
        ))

    def anotar(self, kind, **data):
        self.serie += 1
        resultado = self.r.reg.anotar(
            self.r.enc, {"kind": kind, "data": {**data, "hora": self.r.hora}},
            f"regresion:{self.serie}",
        )
        self.assertEqual(resultado, "nuevo", (kind, data))

    def relevar(self, rol="responsable"):
        for n in (1, 2):
            self.anotar("reserva", rol=rol, generacion=n)
            self.anotar(
                "intento_abierto", id=f"glm-{n}", pid=99999999,
                inicio="ensayo", rol=rol, agente="opencode", modelo=GLM,
            )
            self.anotar("intento_cerrado")
            ciclo._sin_avance(
                self.r.reg, self.r.fold(), "no se produjo el cambio solicitado",
                "diagnosticar sin modificar el producto", self.r.hora,
                f"vuelta-{n}", rol=rol,
            )
        self.assertEqual(relevos_de(self.r.fold(), rol), 1)
        self.assertEqual(self.r.fold().pendientes_entrega, ())

    def barrer(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli._supervisor(self.r.reg, self.args), 0)
        return self.r.fold()

    def barrer_hasta_terminar(self):
        for _ in range(4):
            enc = self.barrer()
            if enc.estado == "terminado":
                return enc
        return enc

    def test_un_solo_responsable_glm_agotado_termina_con_el_motivo_sin_preguntar(self):
        self.relevar()

        enc = self.barrer_hasta_terminar()

        self.assertEqual(enc.estado, "terminado")
        # El pendiente vive en la fase bloqueada, no en el encargo.
        self.assertTrue(enc.fase_actual.pendientes)
        self.assertIn("relevo", " ".join(enc.fase_actual.pendientes).lower())
        self.assertEqual(enc.preguntas, ())
        self.assertTrue(any(
            ev["kind"] == "bloqueo_fase" for ev in self.r.reg.eventos(self.r.enc)
        ))
        self.assertEqual(
            [ev for ev in self.r.reg.eventos(self.r.enc) if ev["kind"] == "cierre_forzado"],
            [],
        )
        self.assertIn("no hay producto", enc.evidencia[-1].lower())

    def test_el_revisor_sin_otro_modelo_tambien_termina_con_pendientes(self):
        self.relevar("revisor")

        enc = self.barrer_hasta_terminar()

        self.assertEqual(enc.estado, "terminado")
        self.assertTrue(enc.fase_actual.pendientes)
        self.assertIn("revisor", " ".join(enc.fase_actual.pendientes).lower())
        self.assertEqual(enc.preguntas, ())

    def test_un_modelo_de_relevo_con_cuota_temporal_sigue_en_espera(self):
        self.relevar()
        self.modelos.return_value = (GLM, OTRO)
        self.anotar(
            "proveedor_fallo", agente="opencode", modelo=OTRO,
            hasta="2099-01-01T00:00:00Z", causa="cuota temporal agotada", relevo=True,
        )

        enc = self.barrer()

        self.assertEqual(enc.estado, "esperando_reintento")
        self.assertEqual(enc.reanuda_en, "2099-01-01T00:00:00Z")
        self.assertIn("cuota temporal", enc.motivo)
        self.assertEqual(enc.preguntas, ())
        self.assertEqual(enc.pendientes_entrega, ())

    def test_un_modelo_autorizado_sin_runtime_no_se_da_por_agotado(self):
        self.relevar()
        self.modelos.return_value = (GLM, OTRO)

        with mock.patch.object(cli, "_disponible", return_value=False):
            enc = self.barrer()

        self.assertEqual(enc.estado, "esperando_reintento")
        self.assertIn("runtime", enc.motivo)
        self.assertTrue(enc.reanuda_en)
        self.assertEqual(enc.preguntas, ())
        self.assertEqual(enc.pendientes_entrega, ())


if __name__ == "__main__":
    unittest.main()
