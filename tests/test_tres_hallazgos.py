"""Los tres hallazgos del modelo formal de #152, con el código del motor.

- Pregunta callada: un pedido repetido sobre un recurso que exige autorización
  no deja el encargo abierto y mudo esperando a una persona.
- Freno al continuar: con el freno del revisor en el tope, continuar sin
  ajuste ni respuesta no relanza la revisión; entrega con el pendiente.
- Respuesta sin ejecutar: toda respuesta registrada se ejecuta o se deniega
  antes de lanzar al revisor.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from motor.encargos import cli as cli_encargos
from motor.encargos import necesidades as nec
from motor.encargos.aislamiento import SondaEntorno
from motor.encargos.ciclo import ahora_iso, barrer, ordenar
from tests.test_flujo_revisiones import Recorrido
from tests.test_freno_no_concluyente import _Freno
from tests.test_respuestas_buzon import _pedido


class _ConPropietario(unittest.TestCase):
    """Un recurso que exige autorización del propietario y nadie contesta."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.r = Recorrido(tmp.name, "tres-hallazgos", "Crea dato.txt con 42")
        self.addCleanup(self.r.reg.close)
        (self.r.espacio / "dato.txt").write_text("42")
        raiz = Path(tmp.name) / "ci"
        raiz.mkdir()
        (raiz / "indice.txt").write_text("v1")
        (self.r.base / "capacidades.json").write_text(
            json.dumps(
                {
                    "recursos": {
                        "ci-github": {
                            "tipo": "remoto",
                            "raiz": str(raiz),
                            "autorizacion": "propietario",
                        }
                    }
                }
            )
        )
        sonda = mock.patch(
            "motor.encargos.aislamiento.sondear", return_value=SondaEntorno(True)
        )
        sonda.start()
        self.addCleanup(sonda.stop)
        self.lanzamientos: list[str] = []
        self.turnos = 0
        self.r.incorporar()

    def lanzador(self, enc, prompt, espacio, sesion):
        self.lanzamientos.append(enc.rol)
        return None

    def barrer(self) -> None:
        barrer(self.r.reg, ahora_iso(), time.time(), self.lanzador)

    def turno(self, datos: dict) -> None:
        enc = self.r.fold()
        self.turnos += 1
        self.r.reg.anotar(
            self.r.enc,
            {
                "kind": "reserva",
                "data": {"rol": "revisor", "generacion": enc.generacion + 1},
            },
            f"h-reserva-{self.turnos}",
        )
        self.r.reg.anotar(
            self.r.enc,
            {
                "kind": "intento_abierto",
                "data": {
                    "id": f"h{self.turnos}",
                    "pid": os.getpid(),
                    "inicio": "t",
                    "rol": "revisor",
                },
            },
            f"h-abierto-{self.turnos}",
        )
        self.r.juicio(datos)
        self.r.incorporar()
        self.r.reg.anotar(
            self.r.enc,
            {"kind": "intento_cerrado", "data": {}},
            f"h-cerrado-{self.turnos}",
        )


class PreguntaNuncaCallada(_ConPropietario):
    def test_el_pedido_repetido_deja_un_pendiente_y_no_una_espera_humana(self):
        self.turno(_pedido())
        self.barrer()
        enc = self.r.fold()
        self.assertNotEqual(enc.estado, "necesita_usuario")
        self.assertEqual(
            [s.estado for s in enc.solicitudes], ["pregunta"]
        )

        self.turno(_pedido())
        self.barrer()
        enc = self.r.fold()
        self.assertNotEqual(enc.estado, "necesita_usuario")
        # El pedido repetido se consume como una revisión más en ese mismo
        # barrido; la pregunta sigue visible sin bloquear.
        self.assertTrue(self.r.eventos("inspeccion_entregada"))
        self.assertEqual(enc.no_concluyentes, 2)
        self.assertEqual([s.estado for s in enc.solicitudes], ["pregunta"])


class FrenoSinReabrir(_Freno):
    def test_continuar_en_el_tope_no_relanza_al_revisor(self):
        r = self.recorrido("freno-continuar", con_recurso=True)
        self.herramienta_ok()
        for ruta in ("a.txt", "b.txt", "c.txt"):
            self.vuelta(r, ruta)
        self.assertEqual(r.fold().no_concluyentes, 3)

        ordenar(r.reg, r.enc, "parar", "h-parar", ahora_iso())
        self.barrer(r)
        ordenar(r.reg, r.enc, "continuar", "h-continuar", ahora_iso())

        self.lanzamientos.clear()
        self.barrer(r)
        enc = r.fold()
        self.assertEqual(self.lanzamientos, [], "se relanzó al revisor sin dato nuevo")
        self.assertNotEqual(enc.estado, "necesita_usuario")
        self.assertEqual(enc.fases[0].estado, "bloqueada")
        self.assertTrue(
            any("pendientes" in ev for ev in enc.evidencia), enc.evidencia
        )


class RevisorEsperaLaRespuesta(unittest.TestCase):
    def test_la_respuesta_se_atiende_antes_de_barrer(self):
        orden: list[str] = []

        def continuar(*args, **kwargs):
            orden.append("continuar_respuestas")
            return {}

        def barrer_falso(*args, **kwargs):
            orden.append("barrer")
            return []

        reg = SimpleNamespace(
            base=Path("/tmp"),
            ids=lambda: ["enc_1"],
            fold=lambda ident: SimpleNamespace(
                solicitudes=[SimpleNamespace(estado="respuesta")],
                agentes=("claude",),
                modelos_solicitados=(),
            ),
        )
        args = SimpleNamespace(
            agente="claude", respaldo=(), una_vez=True, umbral=30
        )
        with (
            mock.patch.object(nec, "continuar_respuestas", side_effect=continuar),
            mock.patch.object(cli_encargos, "barrer", side_effect=barrer_falso),
            mock.patch.object(cli_encargos, "lanzar_de_ruta", return_value=None),
            mock.patch.object(cli_encargos, "marcar_ruta", return_value=None),
            mock.patch.object(cli_encargos, "_puerto_github", return_value=None),
        ):
            cli_encargos._supervisor(reg, args)

        self.assertEqual(orden, ["continuar_respuestas", "barrer"])


if __name__ == "__main__":
    unittest.main()
