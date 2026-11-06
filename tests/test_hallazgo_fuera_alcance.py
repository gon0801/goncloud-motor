"""Hallazgos externos fuera de alcance y disputas que decide el revisor."""

from __future__ import annotations

import json
import tempfile
import unittest

from motor.encargos.ciclo import _paso_entrega, ahora_iso
from motor.encargos.comprobacion import hallazgo_fuera_de_alcance_por_restriccion
from motor.encargos.dominio import Hallazgo, bloqueantes_abiertos
from motor.github import FakeGitHub
from tests.test_flujo_revisiones import (
    _entrega_pr,
    _interno,
    sonda_sin_aislamiento_del_host,
)

RESTRICCION = "Cambio acotado a motor/encargos/cli.py y su prueba"


def _hallazgo(ancla: str) -> Hallazgo:
    return Hallazgo(
        id="F1",
        gravedad="bloqueante",
        criterio="comportamiento",
        titulo="El comando de integraciones filtra el token",
        ancla=ancla,
    )


def setUpModule():
    sonda_sin_aislamiento_del_host()


class _GhConHallazgo(FakeGitHub):
    """DeepSeek pide cambios con un hallazgo estructurado en `archivo`."""

    archivo = "motor/integraciones/cli.py"
    severidad = "medium"
    identificador = "F1"

    def comentarios_deepseek(self, repo, numero, sha=""):
        hallazgos = [
            {
                "id": self.identificador,
                "severity": self.severidad,
                "title": "El comando de integraciones filtra el token",
                "file": self.archivo,
                "line": 176,
            }
        ]
        texto = (
            "DeepSeek: CHANGES_REQUESTED\nai-review:findings="
            + json.dumps(hallazgos)
        )
        return [{"texto": texto, "autor": "DeepSeek", "sha": sha}]


def _cuerpo_del_pr(vuelta) -> str:
    prs = vuelta.gh._cargar()["prs"]
    return next(iter(prs.values()))["cuerpo"]


class HallazgoFueraDeAlcance(unittest.TestCase):
    def test_restriccion_con_clausulas_clasifica_cada_ruta(self):
        texto = "Cambio acotado a motor/encargos/cli.py; no tocar motor/otro.py"
        permitido = _hallazgo("motor/encargos/cli.py:1")
        prohibido = _hallazgo("motor/otro.py:1")
        self.assertEqual(
            hallazgo_fuera_de_alcance_por_restriccion((texto,), permitido), ""
        )
        self.assertIn(
            "prohíbe tocar",
            hallazgo_fuera_de_alcance_por_restriccion((texto,), prohibido),
        )

    def test_restriccion_terminada_en_punto_extrae_la_ruta(self):
        self.assertIn(
            "prohíbe tocar",
            hallazgo_fuera_de_alcance_por_restriccion(
                ("No tocar motor/otro.py.",), _hallazgo("motor/otro.py:1")
            ),
        )

    def test_restriccion_expresa_deja_fuera_el_hallazgo_y_entrega(self):
        with tempfile.TemporaryDirectory() as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "fuera-1", _GhConHallazgo,
                texto="cambia el comando de encargos",
                restricciones=(RESTRICCION,),
            )
            e = vuelta("hola")
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(bloqueantes_abiertos(e, "deepseek"), ())
            self.assertFalse(any(
                ev["kind"] == "revision"
                and ev["data"].get("origen") == "deepseek"
                and ev["data"].get("veredicto") == "cambios"
                for ev in r.reg.eventos(r.enc)
            ))
            self.assertTrue(e.fuera_de_alcance)
            self.assertEqual(e.fuera_de_alcance[0][0], "F1")
            cuerpo = _cuerpo_del_pr(vuelta)
            self.assertIn("## Hallazgos fuera de alcance", cuerpo)
            self.assertIn("F1", cuerpo)
            self.assertIn("motor/integraciones/cli.py", cuerpo)

    def test_hallazgo_medium_dentro_del_alcance_sigue_bloqueando(self):
        class _Dentro(_GhConHallazgo):
            archivo = "motor/encargos/cli.py"

        with tempfile.TemporaryDirectory() as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "dentro-1", _Dentro,
                texto="cambia el comando de encargos",
                restricciones=(RESTRICCION,),
            )
            e = vuelta("hola")
            self.assertNotEqual(e.estado, "terminado")
            abiertos = bloqueantes_abiertos(e, "deepseek")
            self.assertEqual([h.id for h in abiertos], ["F1"])
            self.assertTrue(any(
                ev["kind"] == "revision"
                and ev["data"].get("origen") == "deepseek"
                and ev["data"].get("veredicto") == "cambios"
                for ev in r.reg.eventos(r.enc)
            ))

    def test_disputa_no_suma_sin_progreso_y_el_revisor_la_descarta(self):
        with tempfile.TemporaryDirectory() as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "disputa-1", _GhConHallazgo,
                texto="cambia el comando de encargos",
            )
            e = vuelta("hola")
            abiertos = bloqueantes_abiertos(e, "deepseek")
            self.assertEqual([h.id for h in abiertos], ["F1"])
            self.assertEqual(e.rol, "responsable")

            (_interno(r) / "disputa.json").write_text(json.dumps({
                "hallazgo": "F1",
                "evidencia": (
                    "el archivo que señala no lo toca este encargo; "
                    "git diff main...HEAD solo muestra saludo.txt"
                ),
            }))
            e = r.incorporar()
            self.assertEqual(e.sin_progreso, 0)
            self.assertEqual(e.rol, "revisor")
            self.assertEqual(e.estrategia, "decidir_disputa")
            self.assertEqual([h.id for h in bloqueantes_abiertos(e, "deepseek")], ["F1"])

            r.juicio({
                "veredicto": "aprobado",
                "evidencia": "la evidencia de la disputa es correcta; el archivo no se toca",
                "fuera_de_alcance": [
                    {"hallazgo": "F1", "razon": "señala un archivo fuera del alcance del encargo"}
                ],
            })
            e = r.incorporar()
            self.assertEqual(bloqueantes_abiertos(e, "deepseek"), ())
            e = _paso_entrega(r.reg, r.fold(), vuelta.gh, ahora_iso())
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.fuera_de_alcance[0][0], "F1")
            cuerpo = _cuerpo_del_pr(vuelta)
            self.assertIn("## Hallazgos fuera de alcance", cuerpo)
            self.assertIn("fuera del alcance del encargo", cuerpo)


if __name__ == "__main__":
    unittest.main()
