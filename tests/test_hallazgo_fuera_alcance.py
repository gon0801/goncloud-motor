"""Hallazgos externos fuera de alcance y disputas que decide el revisor."""

from __future__ import annotations

import json
import tempfile
import unittest

from motor.encargos.ciclo import _paso_entrega, ahora_iso, recibir_resultado
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

    def test_no_tocar_nada_fuera_de_la_ruta_no_infiere_nada(self):
        # «fuera de» es ambigua: la heurística no descarta nada y el revisor
        # independiente decide con su juicio.
        texto = "No tocar nada fuera de motor/encargos/cli.py"
        self.assertEqual(
            hallazgo_fuera_de_alcance_por_restriccion(
                (texto,), _hallazgo("motor/encargos/cli.py:1")
            ),
            "",
        )
        self.assertEqual(
            hallazgo_fuera_de_alcance_por_restriccion(
                (texto,), _hallazgo("motor/otro.py:1")
            ),
            "",
        )

    def test_la_prohibicion_no_alcanza_por_tallo_de_prueba(self):
        self.assertEqual(
            hallazgo_fuera_de_alcance_por_restriccion(
                ("No tocar motor/encargos/cli.py",),
                _hallazgo("tests/test_cli_reabrir.py:1"),
            ),
            "",
        )

    def test_ancla_sin_directorio_cuenta_para_la_ruta_permitida(self):
        self.assertEqual(
            hallazgo_fuera_de_alcance_por_restriccion(
                ("Cambio acotado a motor/encargos/cli.py",),
                _hallazgo("cli.py:120"),
            ),
            "",
        )

    def test_una_excepcion_no_convierte_la_prohibicion_en_lista_blanca(self):
        # «salvo» deja la decisión al revisor: no se infiere lista blanca ni
        # prohibición para otras rutas.
        texto = "No tocar motor/a.py salvo motor/b.py"
        for ancla in ("motor/a.py:1", "motor/b.py:1", "motor/c.py:1"):
            self.assertEqual(
                hallazgo_fuera_de_alcance_por_restriccion(
                    (texto,), _hallazgo(ancla)
                ),
                "",
            )

    def test_un_fuera_de_sin_nada_no_infiere_nada(self):
        # «fuera de» sin una fórmula genérica es ambigua: no se descarta nada.
        texto = "No modificar motor/a.py fuera de lo estrictamente necesario"
        for ancla in ("motor/a.py:1", "motor/c.py:1"):
            self.assertEqual(
                hallazgo_fuera_de_alcance_por_restriccion(
                    (texto,), _hallazgo(ancla)
                ),
                "",
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

    def test_el_camino_de_autoridad_aplica_fuera_de_alcance(self):
        with tempfile.TemporaryDirectory() as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "autoridad-1", _GhConHallazgo,
                texto="cambia el comando de encargos",
            )
            e = vuelta("hola")
            self.assertEqual([h.id for h in bloqueantes_abiertos(e, "deepseek")], ["F1"])
            r.reg.anotar(
                r.enc,
                {"kind": "conductor", "data": {"token": "tok", "host": "host"}},
                "conductor-autoridad",
            )
            enc = r.fold()
            observaciones = {
                "version": enc.producto.firma,
                "manifiesto": [list(x) for x in enc.producto.manifiesto],
            }
            juicio = {
                "version": enc.producto.firma,
                "veredicto": "aprobado",
                "evidencia": "la evidencia de la disputa es correcta",
                "fuera_de_alcance": [
                    {"hallazgo": "F1", "razon": "fuera del alcance del encargo"}
                ],
            }
            salida = recibir_resultado(
                r.reg, r.enc, "tok", "host", enc.huella,
                observaciones, juicio, ahora_iso(),
            )
            self.assertTrue(salida.get("registrado"), salida)
            final = r.fold()
            self.assertEqual(bloqueantes_abiertos(final, "deepseek"), ())
            self.assertEqual(final.fuera_de_alcance[0][0], "F1")

    def test_prohibido_fuera_de_la_ruta_no_infiere_nada(self):
        # «fuera de» deja la decisión al revisor; no se infiere lista blanca.
        texto = "Prohibido tocar archivos fuera de motor/a.py"
        for ancla in ("motor/a.py:1", "motor/c.py:1"):
            self.assertEqual(
                hallazgo_fuera_de_alcance_por_restriccion(
                    (texto,), _hallazgo(ancla)
                ),
                "",
            )

    def test_nada_salvo_la_ruta_no_infiere_nada(self):
        # «salvo» deja la decisión al revisor; no se infiere lista blanca.
        texto = "No tocar nada salvo motor/b.py"
        for ancla in ("motor/b.py:1", "motor/c.py:1"):
            self.assertEqual(
                hallazgo_fuera_de_alcance_por_restriccion(
                    (texto,), _hallazgo(ancla)
                ),
                "",
            )

    def test_el_camino_de_autoridad_aplica_resueltos_externos(self):
        with tempfile.TemporaryDirectory() as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "autoridad-resueltos-1", _GhConHallazgo,
                texto="cambia el comando de encargos",
            )
            e = vuelta("hola")
            self.assertEqual([h.id for h in bloqueantes_abiertos(e, "deepseek")], ["F1"])
            r.reg.anotar(
                r.enc,
                {"kind": "conductor", "data": {"token": "tok", "host": "host"}},
                "conductor-autoridad-resueltos",
            )
            enc = r.fold()
            observaciones = {
                "version": enc.producto.firma,
                "manifiesto": [list(x) for x in enc.producto.manifiesto],
            }
            juicio = {
                "version": enc.producto.firma,
                "veredicto": "aprobado",
                "evidencia": "la evidencia del revisor descarta el hallazgo",
                "resueltos": ["F1"],
            }
            salida = recibir_resultado(
                r.reg, r.enc, "tok", "host", enc.huella,
                observaciones, juicio, ahora_iso(),
            )
            self.assertTrue(salida.get("registrado"), salida)
            final = r.fold()
            self.assertEqual(bloqueantes_abiertos(final, "deepseek"), ())
            cerrado = next(h for h in final.hallazgos if h.id == "F1")
            self.assertEqual(cerrado.estado, "resuelto")
            self.assertEqual(
                cerrado.cierre_evidencia, "la evidencia del revisor descarta el hallazgo"
            )
            self.assertTrue(any(
                ev["kind"] == "nota_pr"
                and "F1" in ev["data"].get("texto", "")
                for ev in r.reg.eventos(r.enc)
            ))

    def test_una_categoria_con_salvo_no_es_lista_blanca(self):
        texto = "No tocar los tests salvo tests/test_a.py"
        self.assertEqual(
            hallazgo_fuera_de_alcance_por_restriccion(
                (texto,), _hallazgo("motor/encargos/cli.py:1")
            ),
            "",
        )
        self.assertEqual(
            hallazgo_fuera_de_alcance_por_restriccion(
                (texto,), _hallazgo("tests/test_a.py:1")
            ),
            "",
        )

    def test_un_contrato_fuera_de_no_es_lista_blanca(self):
        texto = "No modificar el contrato público fuera de motor/api.py"
        for ancla in ("motor/api.py:1", "motor/otro.py:1"):
            self.assertEqual(
                hallazgo_fuera_de_alcance_por_restriccion(
                    (texto,), _hallazgo(ancla)
                ),
                "",
            )

    def test_fuera_de_con_salvo_no_excluye_la_ruta_permitida(self):
        self.assertEqual(
            hallazgo_fuera_de_alcance_por_restriccion(
                ("No tocar nada fuera de motor/a.py salvo su prueba",),
                _hallazgo("motor/a.py:1"),
            ),
            "",
        )

    def test_no_tocar_una_ruta_y_solo_otra_es_ambigua(self):
        texto = "No tocar motor/a.py, solo motor/b.py"
        for ancla in ("motor/a.py:1", "motor/b.py:1", "motor/c.py:1"):
            self.assertEqual(
                hallazgo_fuera_de_alcance_por_restriccion(
                    (texto,), _hallazgo(ancla)
                ),
                "",
            )


if __name__ == "__main__":
    unittest.main()
