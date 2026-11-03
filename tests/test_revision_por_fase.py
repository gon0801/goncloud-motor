"""Opus una vez por tramo, DeepSeek como compuerta aparte y sesión nueva por fase."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

from motor.encargos import ciclo
from motor.encargos.agentes import _sesion_modo
from motor.encargos.aislamiento import SondaEntorno
from motor.encargos.ciclo import _paso_entrega, ahora_iso, barrer, corregir
from motor.encargos.dominio import alcance_de, bloqueantes_abiertos
from tests.test_flujo_revisiones import (
    Recorrido,
    _GhConDeepSeek,
    _bloqueante,
    _entrega_pr,
    _interno,
    _lanzaria,
)


class _GhConDeepSeekReescrito(_GhConDeepSeek):
    """DeepSeek reescribe su comentario limpio sobre el mismo SHA."""

    def __init__(self, d):
        super().__init__(d)
        self.sufijo = ""

    def comentarios_deepseek(self, repo, numero, sha=""):
        if self.titulos:
            return super().comentarios_deepseek(repo, numero, sha)
        return [
            {
                "texto": "DeepSeek: APPROVED" + self.sufijo,
                "autor": "DeepSeek",
                "sha": sha,
            }
        ]


def setUpModule():
    parche = mock.patch(
        "motor.encargos.aislamiento.sondear", return_value=SondaEntorno(True)
    )
    parche.start()
    unittest.addModuleCleanup(parche.stop)


def _revisiones(r, origen: str) -> list[dict]:
    return [
        ev
        for ev in r.reg.eventos(r.enc)
        if ev["kind"] == "revision" and ev["data"].get("origen") == origen
    ]


def _estimar_tokens(texto: str) -> int:
    """Cota baja y estable sin tokenizador: 4 caracteres por token."""
    return max(1, len(texto) // 4)


def _lanzar(r, lanzador):
    with mock.patch(
        "motor.encargos.aislamiento.sondear", return_value=SondaEntorno(True)
    ), mock.patch(
        "motor.flota.runtime.exigir_poteto",
        return_value=(Path(r.base) / "poteto.md", "digest"),
    ), mock.patch(
        "motor.encargos.ciclo.observar", return_value="ausente"
    ):
        barrer(r.reg, ahora_iso(), time.time(), lanzador)


class DeepSeekNoPasaPorOpus(unittest.TestCase):
    def test_dos_correcciones_deepseek_no_gastan_opus_y_la_final_si(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "opus-fase", _GhConDeepSeek)
            gh = vuelta.gh
            e = vuelta("uno")
            self.assertEqual(e.nivel_alcanzado, "pr_abierto")
            self.assertEqual(len(_revisiones(r, "independiente")), 1)
            inicial = e.revision_vigente.version

            def correccion(contenido: str):
                (r.espacio / "saludo.txt").write_text(contenido)
                r.incorporar()
                return _paso_entrega(r.reg, r.fold(), gh, ahora_iso())

            gh.titulos = ("Fuga uno",)
            e = correccion("dos")
            self.assertEqual(e.estrategia, "investigar")
            self.assertEqual(len(_revisiones(r, "independiente")), 1)

            gh.titulos = ("Fuga dos",)
            e = correccion("tres")
            self.assertEqual(e.estrategia, "investigar")
            self.assertEqual(len(_revisiones(r, "independiente")), 1)

            # DeepSeek ya no tiene High ni Medium abiertos. El tramo acumulado
            # necesita una sola revisión final de Opus, no una por vuelta.
            gh.titulos = ()
            e = ciclo._releer_deepseek(
                r.reg, r.fold(), gh, r.d, ahora_iso(), time.time() + 3600
            )
            self.assertEqual(bloqueantes_abiertos(e, "deepseek"), ())
            self.assertEqual(e.estrategia, "revisar_evidencia")
            self.assertEqual(e.rol, "revisor")
            self.assertEqual(len(_revisiones(r, "independiente")), 1)
            self.assertNotEqual(e.fase_actual.estado, "preparada")
            self.assertNotEqual(e.estado, "terminado")
            self.assertTrue(_lanzaria(e))
            tramo = alcance_de(e)
            self.assertEqual(tramo.clase, "correccion")
            self.assertEqual(tramo.base_version, inicial)
            self.assertIn("saludo.txt", tramo.tocadas)

            r.juicio(
                {
                    "veredicto": "aprobado",
                    "evidencia": "revise solo el tramo corregido desde la ultima aprobacion",
                }
            )
            e = r.incorporar()
            revisiones = _revisiones(r, "independiente")
            self.assertEqual(len(revisiones), 2)
            final = revisiones[-1]["data"]
            self.assertEqual(final["alcance"], "correccion")
            self.assertEqual(final["base_version"], inicial)

    def test_la_primera_lectura_limpia_tras_corregir_devuelve_a_opus(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "opus-limpia", _GhConDeepSeek)
            gh = vuelta.gh
            vuelta("uno")
            gh.titulos = ()
            (r.espacio / "saludo.txt").write_text("dos")
            r.incorporar()
            e = _paso_entrega(r.reg, r.fold(), gh, ahora_iso())
            self.assertEqual(bloqueantes_abiertos(e, "deepseek"), ())
            self.assertEqual(e.estrategia, "revisar_evidencia")
            self.assertEqual(e.rol, "revisor")
            self.assertNotEqual(e.fase_actual.estado, "preparada")
            self.assertNotEqual(e.estado, "terminado")
            self.assertEqual(len(_revisiones(r, "independiente")), 1)
            self.assertTrue(_lanzaria(e))

    def test_con_un_bloqueante_de_opus_no_se_adelanta_otra_revision(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "opus-bloqueante", _GhConDeepSeekReescrito
            )
            gh = vuelta.gh
            vuelta("uno")
            gh.titulos = ()
            (r.espacio / "saludo.txt").write_text("dos")
            r.incorporar()
            e = _paso_entrega(r.reg, r.fold(), gh, ahora_iso())
            self.assertEqual(e.estrategia, "revisar_evidencia")

            r.juicio(
                {
                    "veredicto": "cambios",
                    "evidencia": "la correccion rompe X",
                    "bloqueantes": [_bloqueante("rompe X")],
                }
            )
            e = r.incorporar()
            self.assertEqual(e.rol, "responsable")
            self.assertEqual(e.estrategia, "investigar")
            abiertos = [h.id for h in bloqueantes_abiertos(e, "independiente")]
            self.assertEqual(len(abiertos), 1)
            independientes = len(_revisiones(r, "independiente"))

            # DeepSeek reescribe su comentario limpio sobre el mismo SHA. El
            # turno sigue siendo del responsable: Opus ya rechazó esta versión.
            gh.sufijo = " (re-run)"
            e = ciclo._releer_deepseek(
                r.reg, r.fold(), gh, r.d, ahora_iso(), time.time() + 3600
            )
            self.assertEqual(
                [h.id for h in bloqueantes_abiertos(e, "independiente")], abiertos
            )
            self.assertEqual(e.rol, "responsable")
            self.assertEqual(e.estrategia, "investigar")
            self.assertEqual(len(_revisiones(r, "independiente")), independientes)
            self.assertTrue(_lanzaria(e))

    def test_sin_cambios_en_el_tramo_no_hay_revision_final(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "opus-vacio", _GhConDeepSeek)
            gh = vuelta.gh
            e = vuelta("uno")
            self.assertEqual(len(_revisiones(r, "independiente")), 1)

            # DeepSeek retira su veredicto sobre el mismo SHA: el producto no
            # cambió, el tramo está vacío y la aprobación de Opus lo cubre.
            gh.titulos = ()
            e = ciclo._releer_deepseek(
                r.reg, r.fold(), gh, r.d, ahora_iso(), time.time() + 3600
            )
            self.assertEqual(bloqueantes_abiertos(e, "deepseek"), ())
            self.assertEqual(e.estrategia, "investigar")
            self.assertEqual(len(_revisiones(r, "independiente")), 1)

            e = _paso_entrega(r.reg, r.fold(), gh, ahora_iso())
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(len(_revisiones(r, "independiente")), 1)


class DisputaConEvidencia(unittest.TestCase):
    def test_un_hallazgo_de_deepseek_disputado_espera_al_propietario(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "disputa", _GhConDeepSeek)
            e = vuelta("uno")
            abiertos = bloqueantes_abiertos(e, "deepseek")
            self.assertEqual(len(abiertos), 1)
            hallazgo = abiertos[0]
            (_interno(r) / "disputa.json").write_text(
                json.dumps(
                    {
                        "hallazgo": hallazgo.id,
                        "evidencia": (
                            "el token del log es un identificador publico de prueba; "
                            "grep 'sk-' en el diff no encuentra ninguno"
                        ),
                    }
                )
            )
            e = r.incorporar()
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertFalse(_lanzaria(e))
            self.assertEqual(len(_revisiones(r, "independiente")), 1)
            self.assertTrue(e.preguntas)
            self.assertIn(hallazgo.titulo, e.preguntas[-1])
            self.assertIn("identificador publico de prueba", e.preguntas[-1])

    def test_una_disputa_invalida_repetida_termina_en_impedimento(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "disputa-invalida", _GhConDeepSeek)
            vuelta("uno")
            (_interno(r) / "disputa.json").write_text(
                json.dumps({"hallazgo": "", "evidencia": ""})
            )
            e = r.incorporar()
            self.assertEqual(e.estrategia, "reparar_instrumento")
            self.assertEqual(e.impedimento, "")
            e = r.incorporar()
            self.assertEqual(e.impedimento, "disputa.json")
            self.assertEqual(
                e.siguiente,
                "esperar a que se cumpla: que .interno/disputa.json vuelva a validar",
            )
            self.assertFalse(_lanzaria(e))
            e = r.incorporar()
            self.assertEqual(e.impedimento, "disputa.json")
            self.assertFalse(_lanzaria(e))

    def test_una_lista_de_disputas_vacia_es_sin_disputa(self):
        for contenido in ({"disputas": []}, []):
            with self.subTest(contenido=contenido), tempfile.TemporaryDirectory(
                ignore_cleanup_errors=True
            ) as tmp:
                vuelta, r = _entrega_pr(self, tmp, "disputa-vacia", _GhConDeepSeek)
                vuelta("uno")
                (_interno(r) / "disputa.json").write_text(json.dumps(contenido))
                e = r.incorporar()
                fallos = [
                    f
                    for f in r.eventos("instrumento_fallo")
                    if f["data"].get("clase") == "disputa.json"
                ]
                self.assertEqual(fallos, [])
                self.assertEqual(e.impedimento, "")

    def test_el_impedimento_de_disputa_se_levanta_al_reparar_el_archivo(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "disputa-reparada", _GhConDeepSeek)
            vuelta("uno")
            archivo = _interno(r) / "disputa.json"
            archivo.write_text(json.dumps({"hallazgo": "", "evidencia": ""}))
            r.incorporar()
            e = r.incorporar()
            self.assertEqual(e.impedimento, "disputa.json")
            self.assertFalse(
                ciclo._verificar_impedimento(
                    e.impedimento, e, r.espacio, r.base, r.reg
                )
            )
            hallazgo = bloqueantes_abiertos(e, "deepseek")[0]
            archivo.write_text(
                json.dumps(
                    {
                        "hallazgo": hallazgo.id,
                        "evidencia": "el token del log es un identificador publico",
                    }
                )
            )
            self.assertTrue(
                ciclo._verificar_impedimento(
                    e.impedimento, e, r.espacio, r.base, r.reg
                )
            )
            ciclo._resolver_impedimento(r.reg, e, e.impedimento, ahora_iso())
            self.assertEqual(r.fold().impedimento, "")


class SesionNuevaPorFase(unittest.TestCase):
    def _con_fases(self, tmp: str):
        r = Recorrido(tmp, "fase-sesion", "a.txt con A y b.txt con B")
        self.addCleanup(r.reg.close)
        (_interno(r) / "fases.json").write_text(
            json.dumps(
                {
                    "fases": [
                        {"id": "a", "resultado": "a.txt con A"},
                        {"id": "b", "resultado": "b.txt con B", "requiere": ["a"]},
                    ]
                }
            )
        )
        return r

    def _con_sesiones_de_a(self, r) -> None:
        for rol, agente, modelo, sesion in (
            ("responsable", "claude", "modelo-r", "ses-a-responsable"),
            ("revisor", "codex", "modelo-v", "ses-a-revisor"),
        ):
            r.reg.anotar(
                r.enc,
                {
                    "kind": "intento_abierto",
                    "data": {
                        "id": f"tok-{rol}",
                        "pid": os.getpid(),
                        "inicio": "t",
                        "rol": rol,
                        "agente": agente,
                        "modelo": modelo,
                    },
                },
                f"t-abre-{rol}",
            )
            r.reg.anotar(
                r.enc,
                {
                    "kind": "intento_cerrado",
                    "data": {"hora": ahora_iso(), "sesion_runtime": sesion},
                },
                f"t-cierra-{rol}",
            )

    def _capturar(self, r, lanzados: list) -> None:
        def lanzador(enc, prompt, espacio, sesion):
            lanzados.append((enc, sesion, Path(prompt).read_text(encoding="utf-8")))
            return None

        _lanzar(r, lanzador)

    def test_al_cambiar_de_fase_no_reanuda_y_el_prompt_lleva_el_resumen(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = self._con_fases(tmp)
            (r.espacio / "a.txt").write_text("A")
            fase_a = []
            self._capturar(r, fase_a)
            self.assertEqual(len(fase_a), 1)
            prompt_a = fase_a[0][2]
            r.incorporar()
            self._con_sesiones_de_a(r)
            e = r.fold()
            epoca_a = e.epoca_responsable
            sesion_a = str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"{r.enc}:responsable:{epoca_a}")
            )
            r.juicio({"veredicto": "aprobado", "evidencia": "cat a.txt -> A"})
            e = r.incorporar()
            self.assertEqual(e.fase_activa, "b")
            self.assertGreater(e.epoca_responsable, epoca_a)
            self.assertGreater(e.epoca_revisor, 0)
            self.assertEqual(e.sesion_responsable, "")
            self.assertEqual(e.sesion_revisor, "")
            self.assertFalse(e.viva_responsable)
            self.assertFalse(e.viva_revisor)
            self.assertNotEqual(_sesion_modo(e, "claude", "responsable"), "reanudada")

            corregir(r.reg, r.enc, "usa UTF-8 en los avisos", "c1", ahora_iso())
            (r.espacio / "b.txt").write_text("B")
            fase_b = []
            self._capturar(r, fase_b)
            self.assertEqual(len(fase_b), 1)
            enc, sesion_b, prompt = fase_b[0]
            self.assertEqual(enc.rol, "responsable")
            self.assertNotEqual(sesion_b, sesion_a)
            self.assertIn("a.txt con A", prompt)
            self.assertIn("b.txt con B", prompt)
            self.assertIn("usa UTF-8 en los avisos", prompt)

            # Medición del caso: un turno de la fase b que reanudara la sesión de
            # la fase a leería como caché al menos los prompts de ambas; la
            # sesión nueva solo lee el resumen de b.
            antes = _estimar_tokens(prompt_a) + _estimar_tokens(prompt)
            despues = _estimar_tokens(prompt)
            self.assertLess(despues, antes)
            print(
                "tokens de prompt (4 chars/token): antes~",
                antes,
                "despues~",
                despues,
            )


if __name__ == "__main__":
    unittest.main()
