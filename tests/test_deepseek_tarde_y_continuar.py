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
    vista,
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.textos = dict(type(self).textos)

    def comentarios_deepseek(self, repo, numero, sha=""):
        texto = self.textos.get(numero, "DeepSeek: APPROVED")
        if texto is None:
            return []
        return [{"texto": texto, "autor": "DeepSeek", "sha": sha}]


class _GhStickyTimeout(FakeGitHub):
    """El sticky de ai-review declara el timeout de 900 s y que no reintenta."""

    textos: dict[int, str] = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.textos = dict(type(self).textos)

    def comentarios_deepseek(self, repo, numero, sha=""):
        texto = self.textos.get(
            numero,
            "DeepSeek review\n<!-- ai-review:sticky -->\n"
            'ai-review:completion={"status":"timeout","retry":false}\n'
            "ai-review:findings=[]\n"
            "la revisión excedió el tiempo límite de 900 s; no se reintenta "
            "porque otro intento tardaría lo mismo",
        )
        return [{"texto": texto, "autor": "github-actions[bot]", "sha": sha}]


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

    def test_sticky_timeout_sin_revision_no_espera_ni_finge_aprobacion(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "ds-sticky", _GhStickyTimeout, nivel="integrado"
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
                "fases-sticky",
            )

            e = vuelta("uno")

            self.assertEqual(e.fase_activa, "dos")
            self.assertEqual(e.fases[0].nivel_alcanzado, "integrado")
            self.assertTrue(
                any(
                    "sin revisión de DeepSeek" in n
                    and "no se reintenta" in n
                    for n in e.notas_pr
                ),
                e.notas_pr,
            )
            pasos = [
                ev["data"].get("paso")
                for ev in r.reg.eventos(r.enc)
                if ev["kind"] == "entrega_externa"
            ]
            self.assertIn("deepseek_sin_revision", pasos)
            self.assertNotIn("deepseek_leido", pasos)
            self.assertEqual(
                e.fases[0].deepseek_huella, "sin-revision"
            )

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

    def test_continuar_no_borra_la_decision_de_un_medium_tardio(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "ds-tardio-continuar", _GhPorNumero, nivel="integrado"
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
                "fases-tardio-continuar",
            )
            e = vuelta("uno")
            hora = datetime.fromisoformat(e.hora.replace("Z", "+00:00"))
            tarde = (hora + timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%SZ")
            e = ciclo._paso_entrega(r.reg, r.fold(), vuelta.gh, tarde)
            primera = e.fases[0]
            vuelta.gh.textos[primera.pr_numero] = _comentario(_medio("F9"))
            e = vuelta("dos")
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertTrue(any("F9" in p for p in e.preguntas), e.preguntas)

            salida = ciclo.ordenar(
                r.reg, r.enc, "continuar", "continuar-decision-vigente", ahora_iso()
            )

            self.assertFalse(salida.get("rechazada"), salida)
            e = r.fold()
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertTrue(any("F9" in p for p in e.preguntas), e.preguntas)
            e = ciclo._paso_entrega(r.reg, r.fold(), vuelta.gh, ahora_iso())
            self.assertNotEqual(e.estado, "terminado")
            self.assertTrue(any("F9" in p for p in e.preguntas), e.preguntas)

    def test_un_medium_publicado_despues_del_cierre_vuelve_al_encargo(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "ds-cerrado-tardio", _GhPorNumero, nivel="integrado"
            )
            e = vuelta("uno")
            hora = datetime.fromisoformat(e.hora.replace("Z", "+00:00"))
            tarde = (hora + timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%SZ")
            e = ciclo._paso_entrega(r.reg, r.fold(), vuelta.gh, tarde)
            self.assertEqual(e.estado, "terminado")

            vuelta.gh.textos[e.pr_numero] = _comentario(_medio("F10"))
            barrer(
                r.reg, ahora_iso(), time.time(), lambda *a, **k: None,
                gh=vuelta.gh,
            )

            e = r.fold()
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertTrue(any("F10" in p for p in e.preguntas), e.preguntas)
            self.assertTrue(r.eventos("pregunta"))

    def test_un_medium_posterior_al_cierre_en_una_fase_anterior_vuelve(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "ds-cerrado-fase-anterior", _GhPorNumero,
                nivel="integrado",
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
                "fases-cerrado-anterior",
            )
            e = vuelta("uno")
            e = vuelta("dos")
            self.assertEqual(e.estado, "terminado")
            pr_primera = e.fases[0].pr_numero
            self.assertTrue(pr_primera)

            vuelta.gh.textos[pr_primera] = _comentario(_medio("F11"))
            barrer(
                r.reg, ahora_iso(), time.time(), lambda *a, **k: None,
                gh=vuelta.gh,
            )

            e = r.fold()
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertTrue(any("F11" in p for p in e.preguntas), e.preguntas)
            self.assertTrue(any("fase uno" in p for p in e.preguntas), e.preguntas)

    def test_un_ajuste_consume_la_decision_de_un_medium_tardio(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "ds-decision-ajuste", _GhPorNumero, nivel="integrado"
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
                "fases-decision-ajuste",
            )
            e = vuelta("uno")
            hora = datetime.fromisoformat(e.hora.replace("Z", "+00:00"))
            tarde = (hora + timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%SZ")
            e = ciclo._paso_entrega(r.reg, r.fold(), vuelta.gh, tarde)
            vuelta.gh.textos[e.fases[0].pr_numero] = _comentario(_medio("F9"))
            e = vuelta("dos")
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertTrue(any("F9" in p for p in e.preguntas), e.preguntas)

            salida = ciclo.corregir(
                r.reg,
                r.enc,
                "Seguir sin corregirlo; decisión del propietario.",
                "decision-f9",
                ahora_iso(),
            )

            self.assertFalse(salida.get("rechazada"), salida)
            e = r.fold()
            self.assertNotEqual(e.estado, "necesita_usuario")
            self.assertFalse(any("F9" in p for p in e.preguntas), e.preguntas)
            self.assertIn(
                "Seguir sin corregirlo; decisión del propietario.", e.ajustes
            )
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
                for _vuelta in range(2):
                    barrer(
                        r.reg, ahora_iso(), time.time(),
                        lambda *a, **k: None, gh=vuelta.gh,
                    )
                    e = r.fold()
                    self.assertNotEqual(e.estado, "necesita_usuario")
                    self.assertFalse(
                        any("F9" in p for p in e.preguntas), e.preguntas
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


def _dos_fases_con_pr_en_la_segunda(caso, tmp: str, clave: str):
    """Encargo de dos fases: la uno integrada y la dos con PR en nivel pr."""
    vuelta, r = _entrega_pr(caso, tmp, clave, _GhCuenta, nivel="integrado")
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
        f"{clave}:fases",
    )
    e = vuelta("uno")
    sha = "a" * 40
    for paso in ("commit", "push", "pr_listo"):
        r.reg.anotar(
            r.enc,
            {
                "kind": "entrega_externa",
                "data": {
                    "fase": "dos",
                    "intento": e.intento,
                    "paso": paso,
                    "repo": e.entrega.repo,
                    "rama": ciclo._rama_fase(e),
                    "base": e.entrega.base,
                    "numero": 2,
                    "sha": sha,
                    "recibo": sha,
                    "hora": r.hora,
                },
            },
            f"{clave}:{paso}",
        )
    r.reg.anotar(
        r.enc,
        {
            "kind": "entrega_externa",
            "data": {
                "fase": "dos",
                "intento": e.intento,
                "paso": "deepseek_leido",
                "repo": e.entrega.repo,
                "numero": 2,
                "sha": sha,
                "huella": "sin-revision",
                "recibo": sha,
                "hora": r.hora,
            },
        },
        f"{clave}:ds",
    )
    return vuelta, r, r.fold()


class _GhCuenta(_GhPorNumero):
    """Cuenta las lecturas de DeepSeek y puede fallar a voluntad."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.llamadas: list[str] = []
        self.falla = False

    def comentarios_deepseek(self, repo, numero, sha=""):
        self.llamadas.append(sha)
        if self.falla:
            raise RuntimeError("DeepSeek no responde")
        return super().comentarios_deepseek(repo, numero, sha)


class TopeConBloqueantesAnteriores(unittest.TestCase):
    def test_omitir_resuelve_el_bloqueante_viejo_y_no_finge_una_lectura(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "ds-bloqueante-viejo", FakeGitHub, nivel="integrado"
            )
            e = vuelta("uno")
            self.assertEqual(e.nivel_alcanzado, "pr_abierto")
            r.reg.anotar(
                r.enc,
                {
                    "kind": "revision",
                    "data": {
                        "fase": e.fase_activa,
                        "intento": e.intento,
                        "revision_id": "rev-vieja",
                        "version": e.producto.firma,
                        "base_version": "",
                        "alcance": "completa",
                        "veredicto": "cambios",
                        "revisor": "deepseek",
                        "origen": "deepseek",
                        "lector_version": 0,
                        "cubre": ["F8"],
                        "hallazgos": [
                            {
                                "id": "F8",
                                "gravedad": "bloqueante",
                                "criterio": "comportamiento",
                                "titulo": "bloqueante de un SHA anterior",
                                "origen": "deepseek",
                                "estado": "abierto",
                            }
                        ],
                        "hora": r.hora,
                    },
                },
                "rev-bloqueante-viejo",
            )
            e = r.fold()
            self.assertEqual(_ids(e), ["F8"])
            self.assertTrue(ciclo._deepseek_por_releer(e))

            e = ciclo._omitir_deepseek(
                r.reg, e, e.pr_numero, e.pr_sha,
                "sin revisión de DeepSeek tras 31 min; la fase sigue", r.hora,
            )

            self.assertEqual(_ids(e), [])
            self.assertFalse(ciclo._deepseek_por_releer(e))
            self.assertEqual(e.nivel_alcanzado, "pr")
            self.assertEqual(e.deepseek_huella, "sin-revision")
            self.assertFalse(vista(e)["entrega"]["deepseek"]["leida"])
            self.assertFalse(vista(e)["fases"][0]["deepseek"]["leida"])


class SondeoDeCerradosVeActualizaciones(unittest.TestCase):
    def _terminado(self, tmp: str):
        vuelta, r = _entrega_pr(
            self, tmp, "ds-sondeo-cerrado", _GhCuenta, nivel="integrado"
        )
        e = vuelta("uno")
        hora = datetime.fromisoformat(e.hora.replace("Z", "+00:00"))
        tarde = (hora + timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%SZ")
        e = ciclo._paso_entrega(r.reg, r.fold(), vuelta.gh, tarde)
        self.assertEqual(e.estado, "terminado")
        vuelta.gh.llamadas.clear()
        return vuelta, r, e

    def test_un_medium_posterior_a_una_lectura_limpia_vuelve_al_encargo(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._terminado(tmp)
            vuelta.gh.textos[e.pr_numero] = "DeepSeek: APPROVED"
            epoch = time.time()
            hora = ahora_iso()
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch
            )
            self.assertEqual(r.fold().estado, "terminado")
            self.assertEqual(r.fold().preguntas, ())

            vuelta.gh.textos[e.pr_numero] = _comentario(_medio("F15"))
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch + 61
            )

            e = r.fold()
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertTrue(any("F15" in p for p in e.preguntas), e.preguntas)

    def test_el_sondeo_no_consulta_github_de_por_vida(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._terminado(tmp)
            vuelta.gh.textos[e.pr_numero] = None
            epoch = time.time()
            hora = ahora_iso()
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch
            )
            self.assertEqual(len(vuelta.gh.llamadas), 1)
            # La ventana total es de 7 días; al vencer se hace una última
            # lectura y después deja de consultarse.
            ventana = 7 * 24 * 60 * 60
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch + ventana + 61
            )
            self.assertEqual(len(vuelta.gh.llamadas), 2)
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch + ventana + 3600
            )
            self.assertEqual(len(vuelta.gh.llamadas), 2)

    def test_un_medium_posterior_a_la_ventana_de_7_dias_llega(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._terminado(tmp)
            vuelta.gh.textos[e.pr_numero] = "DeepSeek: APPROVED"
            epoch = time.time()
            hora = ahora_iso()
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch
            )
            vuelta.gh.textos[e.pr_numero] = _comentario(_medio("F20"))
            ventana = 7 * 24 * 60 * 60
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch + ventana + 61
            )
            e = r.fold()
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertTrue(any("F20" in p for p in e.preguntas), e.preguntas)

    def test_una_actualizacion_posterior_a_la_ultima_lectura_llega(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._terminado(tmp)
            ventana = 7 * 24 * 60 * 60
            vuelta.gh.textos[e.pr_numero] = "DeepSeek: APPROVED"
            epoch = time.time()
            hora = ahora_iso()
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch
            )
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch + ventana + 61
            )
            vuelta.gh.textos[e.pr_numero] = _comentario(_medio("F21"))
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora,
                epoch + ventana + 61 + 3600,
            )
            e = r.fold()
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertTrue(any("F21" in p for p in e.preguntas), e.preguntas)

    def test_un_medium_posterior_a_la_ventana_de_90_min_sigue_llegando(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._terminado(tmp)
            vuelta.gh.textos[e.pr_numero] = "DeepSeek: APPROVED"
            epoch = time.time()
            hora = ahora_iso()
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch
            )
            vuelta.gh.textos[e.pr_numero] = _comentario(_medio("F16"))
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch + 91 * 60
            )
            e = r.fold()
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertTrue(any("F16" in p for p in e.preguntas), e.preguntas)

    def test_un_error_de_lectura_se_espacia(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._terminado(tmp)
            vuelta.gh.falla = True
            epoch = time.time()
            hora = ahora_iso()
            ciclo._revisar_deepseek_del_cerrado(r.reg, r.fold(), vuelta.gh, hora, epoch)
            self.assertEqual(len(vuelta.gh.llamadas), 1)
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch + 10
            )
            self.assertEqual(len(vuelta.gh.llamadas), 1)
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch + 61
            )
            self.assertEqual(len(vuelta.gh.llamadas), 2)
            # El sondeo sigue vivo para ver actualizaciones tardías, pero el
            # error no se reintenta en cada barrido.
            vuelta.gh.falla = False
            vuelta.gh.textos[e.pr_numero] = _comentario(_medio("F17"))
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch + 91 * 60
            )
            e = r.fold()
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertTrue(any("F17" in p for p in e.preguntas), e.preguntas)


class FailOpenDelUltimoCierre(unittest.TestCase):
    def test_una_lectura_fallida_al_cerrar_no_pierde_la_fase_anterior(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = _dos_fases_con_pr_en_la_segunda(
                self, tmp, "ds-fail-open"
            )
            self.assertEqual(e.nivel_alcanzado, "pr")
            vuelta.gh.textos[e.fases[0].pr_numero] = _comentario(
                _medio("F14")
            )
            vuelta.gh.falla = True

            self.assertIsNone(
                ciclo._veredicto_antes_de_cerrar(
                    r.reg, r.fold(), vuelta.gh, ahora_iso()
                )
            )

            vuelta.gh.falla = False
            r.reg.anotar(
                r.enc,
                {
                    "kind": "cierre_forzado",
                    "data": {"evidencia": "cierre de prueba", "hora": r.hora},
                },
                "ds-fail-open:cierre",
            )
            self.assertEqual(r.fold().estado, "terminado")
            barrer(
                r.reg, ahora_iso(), time.time(), lambda *a, **k: None,
                gh=vuelta.gh,
            )

            e = r.fold()
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertTrue(any("F14" in p for p in e.preguntas), e.preguntas)


class DecisionTardiaConUnaSolaClave(unittest.TestCase):
    def test_tras_el_ajuste_no_se_pregunta_otra_vez_con_otra_clave(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "ds-clave-unica", _GhPorNumero, nivel="integrado"
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
                "fases-clave-unica",
            )
            e = vuelta("uno")
            sha = "a" * 40
            for paso in ("commit", "push", "pr_listo"):
                r.reg.anotar(
                    r.enc,
                    {
                        "kind": "entrega_externa",
                        "data": {
                            "fase": "dos",
                            "intento": e.intento,
                            "paso": paso,
                            "repo": e.entrega.repo,
                            "rama": ciclo._rama_fase(e),
                            "base": e.entrega.base,
                            "numero": 2,
                            "sha": sha,
                            "recibo": sha,
                            "hora": r.hora,
                        },
                    },
                    f"clave-unica:{paso}",
                )
            r.reg.anotar(
                r.enc,
                {
                    "kind": "entrega_externa",
                    "data": {
                        "fase": "dos",
                        "intento": e.intento,
                        "paso": "deepseek_leido",
                        "repo": e.entrega.repo,
                        "numero": 2,
                        "sha": sha,
                        "huella": "h2",
                        "recibo": sha,
                        "hora": r.hora,
                    },
                },
                "clave-unica:ds",
            )
            r.reg.anotar(
                r.enc,
                {
                    "kind": "cierre_forzado",
                    "data": {"evidencia": "cierre de prueba", "hora": r.hora},
                },
                "clave-unica:cierre",
            )
            e = r.fold()
            self.assertEqual(e.estado, "terminado")
            vuelta.gh.textos[e.fases[0].pr_numero] = _comentario(_medio("F13"))
            barrer(
                r.reg, ahora_iso(), time.time(), lambda *a, **k: None,
                gh=vuelta.gh,
            )
            self.assertEqual(len(r.eventos("pregunta")), 1)

            ciclo.corregir(
                r.reg,
                r.enc,
                "Seguir sin corregirlo; decisión del propietario.",
                "decision-f13",
                ahora_iso(),
            )
            self.assertEqual(r.fold().preguntas, ())
            ciclo._veredicto_antes_de_cerrar(
                r.reg, r.fold(), vuelta.gh, ahora_iso()
            )

            self.assertEqual(len(r.eventos("pregunta")), 1)
            self.assertFalse(
                any("F13" in p for p in r.fold().preguntas), r.fold().preguntas
            )


class SondeoNoSeDetieneEnUnaFaseYaPreguntada(unittest.TestCase):
    def test_sigue_con_las_fases_siguientes(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "ds-fases-preguntadas", _GhPorNumero,
                nivel="integrado",
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
                "fases-preguntadas",
            )
            e = vuelta("uno")
            e = vuelta("dos")
            self.assertEqual(e.estado, "terminado")
            uno, dos = e.fases
            texto_uno = _comentario(_medio("F18"))
            texto_dos = _comentario(_medio("F19"))
            vuelta.gh.textos[uno.pr_numero] = texto_uno
            vuelta.gh.textos[dos.pr_numero] = texto_dos
            huella_uno = ciclo._huella_comentarios(
                [{"texto": texto_uno, "sha": uno.pr_sha}]
            )
            clave_uno = ciclo._clave_decision_tardia(
                r.reg, e, uno.id, uno.pr_sha, huella_uno
            )
            r.reg.anotar(
                r.enc,
                {
                    "kind": "pregunta",
                    "data": {
                        "texto": "DeepSeek dejó abierto F18 en la fase uno.",
                        "bloquea_todo": False,
                        "hora": r.hora,
                    },
                },
                clave_uno,
            )

            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, ahora_iso(), time.time()
            )

            e = r.fold()
            self.assertTrue(any("F19" in p for p in e.preguntas), e.preguntas)


class CierreSinPrEnLaFaseActiva(unittest.TestCase):
    def test_el_sondeo_llega_a_una_fase_anterior_con_pr(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "ds-cierre-sin-pr", _GhPorNumero, nivel="integrado"
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
                "fases-cierre-sin-pr",
            )
            e = vuelta("uno")
            self.assertEqual(e.fase_activa, "dos")
            self.assertTrue(e.fases[0].pr_numero)
            self.assertFalse(e.pr_numero)
            r.reg.anotar(
                r.enc,
                {
                    "kind": "cierre_forzado",
                    "data": {"evidencia": "cierre de prueba", "hora": r.hora},
                },
                "cierre-sin-pr-activo",
            )
            self.assertEqual(r.fold().estado, "terminado")
            self.assertFalse(r.fold().pr_numero)

            vuelta.gh.textos[e.fases[0].pr_numero] = _comentario(_medio("F11"))
            barrer(
                r.reg, ahora_iso(), time.time(), lambda *a, **k: None,
                gh=vuelta.gh,
            )

            e = r.fold()
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertTrue(any("F11" in p for p in e.preguntas), e.preguntas)


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
            self.assertEqual(e.fases[0].estado, "preparada")
            self.assertEqual(e.fases[0].pr_numero, pr_original)
            reabierto = ciclo.ordenar(r.reg, r.enc, "reabrir", "r-1", tarde)
            self.assertFalse(reabierto.get("rechazada"), reabierto)
            self.assertEqual(r.fold().fase_activa, "uno")
            self.assertNotEqual(r.fold().fases[0].estado, "preparada")
            continuado = ciclo.ordenar(r.reg, r.enc, "continuar", "c-1", tarde)
            self.assertFalse(continuado.get("rechazada"), continuado)

            e = ciclo._paso_entrega(r.reg, r.fold(), vuelta.gh, tarde)

            self.assertEqual(e.fase_activa, "dos")
            self.assertEqual(e.fases[0].pr_numero, pr_original)
            self.assertEqual(e.fases[0].nivel_alcanzado, "integrado")
            aperturas = [
                ev for ev in r.reg.eventos(r.enc)
                if ev["kind"] == "entrega_externa"
                and ev["data"].get("paso") == "pr_abierto"
            ]
            self.assertEqual(len(aperturas), 1)
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
