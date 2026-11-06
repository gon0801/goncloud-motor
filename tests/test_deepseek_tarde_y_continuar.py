"""La revisión externa que no llega no cierra la fase y continuar retoma.

Dos fallas reales del 6-nov en Gon-PC: el tope de DeepSeek terminaba el
encargo completo con `_entregar_con_pendientes`, y un continuar aplicado dejaba
el encargo en `recibido` con la pregunta vieja del relevo, sin un intento.
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from unittest import mock

from motor.encargos import ciclo
from motor.encargos.aislamiento import SondaEntorno
from motor.encargos.ciclo import ahora_iso, barrer
from motor.encargos.dominio import (
    bloqueantes_abiertos,
    decision_proceso,
    reducir,
)
from motor.github import FakeGitHub
from tests.test_flujo_revisiones import Recorrido, _entrega_pr


class _GhConComentario(FakeGitHub):
    hallazgo: dict | None = None

    def comentarios_deepseek(self, repo, numero, sha=""):
        if self.hallazgo is None:
            return []
        texto = "DeepSeek: CHANGES_REQUESTED\nai-review:findings=" + json.dumps(
            [self.hallazgo]
        )
        return [{"texto": texto, "autor": "DeepSeek", "sha": sha}]


class _GhPorNumero(FakeGitHub):
    """Comentarios por PR; los que no están declarados se aprueban limpios."""

    textos: dict[int, str | None] = {}

    def comentarios_deepseek(self, repo, numero, sha=""):
        texto = self.textos.get(numero, "DeepSeek: APPROVED")
        if texto is None:
            return []
        return [{"texto": texto, "autor": "DeepSeek", "sha": sha}]


def _medio(ident: str = "F6") -> dict:
    return {
        "id": ident,
        "severity": "medium",
        "status": "open",
        "title": f"hallazgo {ident}",
        "file": "saludo.txt",
    }


def _comentario(*hallazgos: dict) -> str:
    return "DeepSeek: CHANGES_REQUESTED\nai-review:findings=" + json.dumps(
        list(hallazgos)
    )


def _ids(e) -> list[str]:
    return [h.id for h in bloqueantes_abiertos(e, "deepseek")]


class TopeDeDeepSeekNoTerminaElEncargo(unittest.TestCase):
    def test_dos_fases_sin_revision_tras_el_tope_integra_y_arranca_la_siguiente(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "ds-tope-dos-fases", FakeGitHub, nivel="integrado"
            )
            r.reg.anotar(
                r.enc,
                {
                    "kind": "fases",
                    "data": {
                        "fases": [
                            {"id": "uno", "resultado": "saludo.txt con uno"},
                            {
                                "id": "dos",
                                "resultado": "saludo.txt con dos",
                                "requiere": ["uno"],
                            },
                        ],
                        "hora": r.hora,
                    },
                },
                "fases-dos",
            )
            e = vuelta("uno")
            self.assertEqual(e.estado, "esperando_reintento")
            hora = datetime.fromisoformat(e.hora.replace("Z", "+00:00"))
            tarde = (hora + timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%SZ")

            e = ciclo._paso_entrega(r.reg, r.fold(), vuelta.gh, tarde)

            self.assertEqual(e.fase_activa, "dos")
            self.assertNotEqual(e.estado, "terminado")
            self.assertEqual(e.fases[0].nivel_alcanzado, "integrado")
            self.assertEqual(e.fases[0].estado, "preparada")
            self.assertTrue(
                any("sin revisión de DeepSeek" in n for n in e.notas_pr), e.notas_pr
            )
            self.assertEqual(r.eventos("cierre_forzado"), [])
            pasos = [
                ev["data"].get("paso")
                for ev in r.reg.eventos(r.enc)
                if ev["kind"] == "entrega_externa"
            ]
            self.assertIn("integrado", pasos)

    def test_un_medium_tardio_de_un_pr_ya_integrado_no_se_pierde(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "ds-tardio-integrado", _GhPorNumero, nivel="integrado"
            )
            r.reg.anotar(
                r.enc,
                {
                    "kind": "fases",
                    "data": {
                        "fases": [
                            {"id": "uno", "resultado": "saludo.txt con uno"},
                            {
                                "id": "dos",
                                "resultado": "saludo.txt con dos",
                                "requiere": ["uno"],
                            },
                        ],
                        "hora": r.hora,
                    },
                },
                "fases-dos",
            )
            e = vuelta("uno")
            hora = datetime.fromisoformat(e.hora.replace("Z", "+00:00"))
            tarde = (hora + timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%SZ")
            e = ciclo._paso_entrega(r.reg, r.fold(), vuelta.gh, tarde)
            primera = e.fases[0]
            self.assertEqual(primera.nivel_alcanzado, "integrado")

            vuelta.gh.textos[primera.pr_numero] = _comentario(_medio("F9"))
            e = vuelta("dos")

            self.assertNotEqual(e.estado, "terminado")
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertTrue(
                any("ya fusionado" in p and "F9" in p for p in e.preguntas),
                e.preguntas,
            )

    def test_un_medium_dentro_del_alcance_antes_del_tope_sigue_bloqueando(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "ds-medium-antes", _GhConComentario, nivel="integrado"
            )
            vuelta.gh.hallazgo = _medio()
            e = vuelta("uno")

            self.assertEqual(_ids(e), ["F6"])
            self.assertNotEqual(e.nivel_alcanzado, "integrado")
            self.assertNotEqual(e.estado, "terminado")
            pasos = [
                ev["data"].get("paso")
                for ev in r.reg.eventos(r.enc)
                if ev["kind"] == "entrega_externa"
            ]
            self.assertNotIn("integrado", pasos)


def _encargo_con_pregunta_vieja(tmp: str) -> Recorrido:
    """Reconstruye el estado de enc_daf8e41dd3a3fac6 con eventos mínimos."""
    r = Recorrido(
        tmp,
        "continuar-viejo",
        "arregla el CLI",
        entrega_nivel="terminado",
        restricciones=("Cambio acotado a motor/encargos/cli.py y su prueba.",),
    )
    hora = ahora_iso()
    r.reg.anotar(
        r.enc,
        {
            "kind": "revision",
            "data": {
                "fase": "",
                "veredicto": "cambios",
                "revisor": "deepseek",
                "origen": "deepseek",
                "version": "",
                "alcance": "completa",
                "hallazgos": [
                    {
                        "id": "F1",
                        "gravedad": "bloqueante",
                        "criterio": "comportamiento",
                        "titulo": "El descarte sigue en el CLI externo",
                        "ancla": "motor/integraciones/cli.py:176",
                        "origen": "deepseek",
                        "estado": "abierto",
                    }
                ],
                "hora": hora,
            },
        },
        "rev-vieja",
    )
    r.reg.anotar(
        r.enc,
        {
            "kind": "sin_progreso",
            "data": {
                "huella": "f1",
                "escalar": True,
                "rol": "responsable",
                "diagnostico": "F1 sigue",
                "hora": hora,
            },
        },
        "sin-progreso-viejo",
    )
    r.reg.anotar(
        r.enc,
        {
            "kind": "pregunta",
            "data": {
                "texto": (
                    "no queda otro modelo autorizado para el rol responsable "
                    "tras el relevo; hace falta autorizar otro modelo o ajustar "
                    "el encargo para continuar"
                ),
                "bloquea_todo": True,
                "hora": hora,
            },
        },
        "pregunta-vieja",
    )
    return r


class ReabrirReusaElPrYLaAprobacion(unittest.TestCase):
    def test_reabrir_y_continuar_retoman_la_fase_sin_repetir_el_pr(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "reabrir-pc", FakeGitHub, nivel="integrado"
            )
            r.reg.anotar(
                r.enc,
                {
                    "kind": "fases",
                    "data": {
                        "fases": [
                            {"id": "uno", "resultado": "saludo.txt con uno"},
                            {
                                "id": "dos",
                                "resultado": "saludo.txt con dos",
                                "requiere": ["uno"],
                            },
                        ],
                        "hora": r.hora,
                    },
                },
                "fases-dos",
            )
            e = vuelta("uno")
            pr_original = e.fases[0].pr_numero
            self.assertTrue(pr_original)
            hora = datetime.fromisoformat(e.hora.replace("Z", "+00:00"))
            tarde = (hora + timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%SZ")

            e = ciclo._entregar_con_pendientes(r.reg, r.fold(), vuelta.gh, tarde)
            self.assertEqual(e.estado, "terminado")
            reabierto = ciclo.ordenar(r.reg, r.enc, "reabrir", "r-1", tarde)
            self.assertFalse(reabierto.get("rechazada"), reabierto)
            continuado = ciclo.ordenar(r.reg, r.enc, "continuar", "c-1", tarde)
            self.assertFalse(continuado.get("rechazada"), continuado)

            e = ciclo._paso_entrega(r.reg, r.fold(), vuelta.gh, tarde)

            self.assertEqual(e.fase_activa, "dos")
            self.assertEqual(e.fases[0].pr_numero, pr_original)
            self.assertEqual(e.fases[0].nivel_alcanzado, "integrado")
            # El cierre forzado es el de la simulación del caso viejo; el
            # camino recuperado no añade otro.
            self.assertEqual(len(r.eventos("cierre_forzado")), 1)


class ContinuarRetomaElTrabajo(unittest.TestCase):
    def test_el_caso_del_registro_tras_continuar_lanza_un_intento_y_limpia_la_pregunta(
        self,
    ):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = _encargo_con_pregunta_vieja(tmp)
            self.addCleanup(r.reg.close)
            antes = r.fold()
            self.assertEqual(antes.estado, "necesita_usuario")
            self.assertEqual(antes.relevos, 1)
            self.assertEqual(_ids(antes), ["F1"])

            salida = ciclo.ordenar(r.reg, r.enc, "continuar", "continuar-viejo", ahora_iso())
            self.assertFalse(salida.get("rechazada"), salida)

            e = r.fold()
            self.assertEqual(e.estado, "recibido")
            self.assertEqual(e.preguntas, ())
            self.assertEqual(e.relevos, 0)
            self.assertEqual(e.sin_progreso, 0)
            self.assertEqual(e.devuelto_con, ())
            self.assertEqual(_ids(e), [])
            self.assertEqual(
                [h.id for h in e.fase_actual.hallazgos if h.estado == "abierto"],
                [],
            )
            self.assertTrue(
                any(hid == "F1" for hid, _titulo, _razon in e.fuera_de_alcance),
                e.fuera_de_alcance,
            )
            self.assertTrue(
                decision_proceso(e, "ausente", None, False, True, e.hora)["lanzar"]
            )

            lanzamientos = []

            def lanzador(enc, prompt, espacio, sesion):
                lanzamientos.append((enc.rol, enc.estrategia))
                return None

            with (
                mock.patch(
                    "motor.encargos.aislamiento.sondear",
                    return_value=SondaEntorno(True),
                ),
                mock.patch(
                    "motor.flota.runtime.exigir_poteto",
                    return_value=("/tmp/poteto-mode", "digest"),
                ),
                mock.patch(
                    "motor.encargos.ciclo._copiar_sesion_sin_caer",
                    return_value=None,
                ),
            ):
                barrer(r.reg, ahora_iso(), time.time(), lanzador)

            self.assertTrue(lanzamientos, "el supervisor no lanzó un intento nuevo")

    def test_un_recibido_trabado_sin_proceso_se_rearma_con_continuar(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = _encargo_con_pregunta_vieja(tmp)
            self.addCleanup(r.reg.close)
            trabado = replace(
                r.fold(),
                estado="recibido",
                proc=None,
                reserva=False,
                relevos=1,
                relevos_por_rol=(("responsable", 1),),
                sin_progreso=1,
                preguntas=("no queda otro modelo autorizado",),
            )

            nuevo = reducir(
                trabado,
                {"kind": "orden", "data": {"accion": "continuar", "hora": ahora_iso()}},
            )

            self.assertEqual(nuevo.estado, "recibido")
            self.assertEqual(nuevo.preguntas, ())
            self.assertEqual(nuevo.relevos, 0)
            self.assertEqual(nuevo.sin_progreso, 0)
            self.assertTrue(
                decision_proceso(nuevo, "ausente", None, False, True, nuevo.hora)[
                    "lanzar"
                ]
            )

    def test_continuar_desde_necesita_usuario_no_obliga_a_parar_primero(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = _encargo_con_pregunta_vieja(tmp)
            self.addCleanup(r.reg.close)
            self.assertEqual(r.fold().estado, "necesita_usuario")

            salida = ciclo.ordenar(r.reg, r.enc, "continuar", "directo", ahora_iso())

            self.assertFalse(salida.get("rechazada"), salida)
            self.assertEqual(r.fold().estado, "recibido")


if __name__ == "__main__":
    unittest.main()
