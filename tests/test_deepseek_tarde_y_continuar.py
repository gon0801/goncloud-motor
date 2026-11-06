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
from pathlib import Path
from unittest import mock

from motor.encargos import ciclo
from motor.encargos.aislamiento import SondaEntorno
from motor.encargos.ciclo import ahora_iso, barrer, recibir
from motor.encargos.dominio import (
    bloqueantes_abiertos,
    decision_proceso,
    reducir,
    vista,
)
from motor.encargos.registro import Registro
from motor.github import FakeGitHub
from tests.test_flujo_revisiones import Recorrido, _entrega_pr


class _GhConComentario(FakeGitHub):
    hallazgo: dict | None = None
    texto: str | None = None
    falla = False

    def comentarios_deepseek(self, repo, numero, sha=""):
        if self.falla:
            raise RuntimeError("DeepSeek no responde")
        if self.texto is not None:
            return [{"texto": self.texto, "autor": "github-actions[bot]", "sha": sha}]
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

    def test_un_medium_tardio_en_una_fase_anterior_se_avisa_y_no_frena_el_cierre(self):
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

            self.assertEqual(e.estado, "terminado")
            self.assertTrue(any("F9" in n for n in e.notas_pr), e.notas_pr)
            self.assertTrue(
                any("abre otro encargo" in n for n in e.procedimiento),
                e.procedimiento,
            )
            self.assertEqual(e.preguntas, ())

    def test_un_medium_publicado_despues_del_cierre_se_avisa_y_no_cambia_el_estado(self):
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
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.preguntas, ())
            self.assertTrue(any("F10" in n for n in e.notas_pr), e.notas_pr)
            self.assertTrue(
                any("abre otro encargo" in n for n in e.procedimiento),
                e.procedimiento,
            )
            self.assertEqual(r.eventos("pregunta"), [])

            antes = len(e.notas_pr)
            barrer(
                r.reg, ahora_iso(), time.time() + 120, lambda *a, **k: None,
                gh=vuelta.gh,
            )
            self.assertEqual(len(r.fold().notas_pr), antes)

    def test_un_medium_posterior_al_cierre_en_una_fase_anterior_se_avisa_en_su_pr(self):
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
            estado_antes = json.loads(
                (vuelta.gh.d / "github.json").read_text()
            )
            cuerpo_antes = next(
                pr for pr in estado_antes["prs"].values()
                if pr["numero"] == pr_primera
            )["cuerpo"]

            vuelta.gh.textos[pr_primera] = _comentario(_medio("F11"))
            barrer(
                r.reg, ahora_iso(), time.time(), lambda *a, **k: None,
                gh=vuelta.gh,
            )

            e = r.fold()
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.preguntas, ())
            self.assertTrue(
                any("F11" in n and "fase uno" in n for n in e.notas_pr),
                e.notas_pr,
            )
            estado_fake = json.loads(
                (vuelta.gh.d / "github.json").read_text()
            )
            cuerpo = next(
                pr for pr in estado_fake["prs"].values()
                if pr["numero"] == pr_primera
            )["cuerpo"]
            self.assertIn("F11", cuerpo)
            # El cuerpo original de la fase anterior no se sobrescribe.
            self.assertIn(cuerpo_antes, cuerpo)

            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, ahora_iso(), time.time() + 120
            )
            estado_dos = json.loads(
                (vuelta.gh.d / "github.json").read_text()
            )
            cuerpo_dos = next(
                pr for pr in estado_dos["prs"].values()
                if pr["numero"] == pr_primera
            )["cuerpo"]
            self.assertEqual(cuerpo_dos.count("después de cerrar"), 1)
            huella = ciclo._huella_comentarios(
                [{"texto": _comentario(_medio("F11")), "sha": e.fases[0].pr_sha}]
            )
            self.assertIsNotNone(
                r.reg.por_ukey(
                    f"cuerpo-tardio:{r.enc}:{e.fases[0].id}:"
                    f"{e.fases[0].pr_sha}:{huella}"
                )
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
            epoch = ciclo._epoch_de_hora(e.hora)
            hora = e.hora
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
            self.assertEqual(e.estado, "terminado")
            self.assertTrue(any("F15" in n for n in e.notas_pr), e.notas_pr)

    def test_el_sondeo_espacia_la_primera_hora_y_despues(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._terminado(tmp)
            vuelta.gh.textos[e.pr_numero] = None
            epoch = ciclo._epoch_de_hora(e.hora)
            hora = e.hora
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch
            )
            self.assertEqual(len(vuelta.gh.llamadas), 1)
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch + 10
            )
            self.assertEqual(len(vuelta.gh.llamadas), 1)
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch + 61
            )
            self.assertEqual(len(vuelta.gh.llamadas), 2)
            # Pasada la primera hora, el espaciado es de 30 minutos.
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch + 3601
            )
            self.assertEqual(len(vuelta.gh.llamadas), 3)
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch + 3601 + 600
            )
            self.assertEqual(len(vuelta.gh.llamadas), 3)
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch + 3601 + 30 * 60
            )
            self.assertEqual(len(vuelta.gh.llamadas), 4)

    def test_una_lectura_fallida_no_finaliza_el_sha(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._terminado(tmp)
            seis_dias = 6 * 24 * 60 * 60
            epoch = ciclo._epoch_de_hora(e.hora)
            hora = e.hora
            vuelta.gh.textos[e.pr_numero] = None
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch
            )
            vuelta.gh.falla = True
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch + seis_dias + 61
            )
            vuelta.gh.falla = False
            vuelta.gh.textos[e.pr_numero] = _comentario(_medio("F23"))
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora,
                epoch + seis_dias + 61 + 3600,
            )
            e = r.fold()
            self.assertEqual(e.estado, "terminado")
            self.assertTrue(any("F23" in n for n in e.notas_pr), e.notas_pr)

    def test_un_medium_posterior_a_los_30_dias_no_se_sondea(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._terminado(tmp)
            vuelta.gh.textos[e.pr_numero] = None
            epoch = ciclo._epoch_de_hora(e.hora)
            hora = e.hora
            treinta = 30 * 24 * 60 * 60
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch
            )
            vuelta.gh.textos[e.pr_numero] = _comentario(_medio("F24"))
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch + treinta + 61
            )
            e = r.fold()
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(len(vuelta.gh.llamadas), 1)
            self.assertFalse(any("F24" in n for n in e.notas_pr), e.notas_pr)

    def test_un_medium_posterior_al_ano_no_se_sondea(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._terminado(tmp)
            vuelta.gh.textos[e.pr_numero] = None
            epoch = ciclo._epoch_de_hora(e.hora)
            hora = e.hora
            un_ano = 365 * 24 * 60 * 60
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch
            )
            vuelta.gh.textos[e.pr_numero] = _comentario(_medio("F25"))
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch + un_ano + 61
            )
            e = r.fold()
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(len(vuelta.gh.llamadas), 1)
            self.assertFalse(any("F25" in n for n in e.notas_pr), e.notas_pr)

    def test_un_medium_despues_de_una_ventana_vacia_dentro_de_la_semana_llega(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._terminado(tmp)
            vuelta.gh.textos[e.pr_numero] = None
            epoch = ciclo._epoch_de_hora(e.hora)
            hora = e.hora
            seis_dias = 6 * 24 * 60 * 60
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch
            )
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch + seis_dias + 61
            )
            vuelta.gh.textos[e.pr_numero] = _comentario(_medio("F22"))
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora,
                epoch + seis_dias + 61 + 3600,
            )
            e = r.fold()
            self.assertEqual(e.estado, "terminado")
            self.assertTrue(any("F22" in n for n in e.notas_pr), e.notas_pr)

    def test_un_medium_posterior_a_la_ventana_de_7_dias_no_se_sondea(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._terminado(tmp)
            vuelta.gh.textos[e.pr_numero] = "DeepSeek: APPROVED"
            epoch = ciclo._epoch_de_hora(e.hora)
            hora = e.hora
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch
            )
            vuelta.gh.textos[e.pr_numero] = _comentario(_medio("F20"))
            ventana = 7 * 24 * 60 * 60
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch + ventana + 61
            )
            e = r.fold()
            self.assertEqual(e.estado, "terminado")
            self.assertFalse(any("F20" in n for n in e.notas_pr), e.notas_pr)

    def test_una_actualizacion_posterior_a_la_ultima_lectura_llega(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._terminado(tmp)
            seis_dias = 6 * 24 * 60 * 60
            vuelta.gh.textos[e.pr_numero] = "DeepSeek: APPROVED"
            epoch = ciclo._epoch_de_hora(e.hora)
            hora = e.hora
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch
            )
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch + seis_dias + 61
            )
            vuelta.gh.textos[e.pr_numero] = _comentario(_medio("F21"))
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora,
                epoch + seis_dias + 61 + 3600,
            )
            e = r.fold()
            self.assertEqual(e.estado, "terminado")
            self.assertTrue(any("F21" in n for n in e.notas_pr), e.notas_pr)

    def test_un_medium_posterior_a_la_ventana_de_90_min_sigue_llegando(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._terminado(tmp)
            vuelta.gh.textos[e.pr_numero] = "DeepSeek: APPROVED"
            epoch = ciclo._epoch_de_hora(e.hora)
            hora = e.hora
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch
            )
            vuelta.gh.textos[e.pr_numero] = _comentario(_medio("F16"))
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, hora, epoch + 91 * 60
            )
            e = r.fold()
            self.assertEqual(e.estado, "terminado")
            self.assertTrue(any("F16" in n for n in e.notas_pr), e.notas_pr)

    def test_un_error_de_lectura_se_espacia(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, e = self._terminado(tmp)
            vuelta.gh.falla = True
            epoch = ciclo._epoch_de_hora(e.hora)
            hora = e.hora
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
            self.assertEqual(e.estado, "terminado")
            self.assertTrue(any("F17" in n for n in e.notas_pr), e.notas_pr)


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
            self.assertEqual(e.estado, "terminado")
            self.assertTrue(any("F14" in n for n in e.notas_pr), e.notas_pr)
            self.assertTrue(
                any("abre otro encargo" in n for n in e.procedimiento),
                e.procedimiento,
            )


class SondeoNoSeDetieneEnUnaFaseYaAvisada(unittest.TestCase):
    def test_sigue_con_las_fases_siguientes(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "ds-fases-avisadas", _GhPorNumero,
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
                "fases-avisadas",
            )
            e = vuelta("uno")
            e = vuelta("dos")
            self.assertEqual(e.estado, "terminado")
            uno, dos = e.fases
            vuelta.gh.textos[uno.pr_numero] = _comentario(_medio("F18"))
            vuelta.gh.textos[dos.pr_numero] = _comentario(_medio("F19"))

            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, ahora_iso(), time.time()
            )

            e = r.fold()
            self.assertEqual(e.estado, "terminado")
            self.assertTrue(any("F18" in n for n in e.notas_pr), e.notas_pr)
            self.assertTrue(any("F19" in n for n in e.notas_pr), e.notas_pr)


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
            self.assertEqual(e.estado, "terminado")
            self.assertTrue(any("F11" in n for n in e.notas_pr), e.notas_pr)


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
            "kind": "conductor",
            "data": {"token": "conductor-viejo", "host": "pc", "hora": hora},
        },
        "conductor-viejo",
    )
    r.reg.anotar(
        r.enc,
        {
            "kind": "intento_abierto",
            "data": {
                "id": "intento-viejo",
                "pid": 99999999,
                "inicio": hora,
                "rol": "responsable",
                "agente": "opencode",
                "host": "pc",
            },
        },
        "intento-viejo",
    )
    r.reg.anotar(
        r.enc,
        {"kind": "intento_cerrado", "data": {"hora": hora}},
        "intento-viejo-cerrado",
    )
    r.reg.anotar(
        r.enc,
        {
            "kind": "estrategia",
            "data": {
                "estrategia": "diagnosticar_bloqueo",
                "rol": "responsable",
                "motivo": "F1 sigue",
                "siguiente": "diagnosticar el bloqueo",
                "hora": hora,
            },
        },
        "estrategia-vieja",
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
            self.assertEqual(antes.devuelto_con, ("F1",))
            self.assertEqual(antes.intento, "intento-viejo")
            self.assertEqual(antes.conductor, "conductor-viejo")

            salida = ciclo.ordenar(r.reg, r.enc, "continuar", "continuar-viejo", ahora_iso())
            self.assertFalse(salida.get("rechazada"), salida)

            e = r.fold()
            self.assertEqual(e.estado, "recibido")
            self.assertEqual(e.preguntas, ())
            self.assertEqual(e.relevos, 0)
            self.assertEqual(e.sin_progreso, 0)
            self.assertEqual(e.devuelto_con, ())
            self.assertEqual(e.intento, "")
            self.assertEqual(e.conductor, "")
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
                lanzamientos.append((enc.estado, enc.rol, enc.estrategia))
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
            self.assertEqual(lanzamientos[0][0], "recibido")
            self.assertEqual(lanzamientos[0][1], "responsable")

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


class SinRevisionNoAvanzaConBloqueantesDelMismoSha(unittest.TestCase):
    def test_el_tope_no_avanza_con_un_bloqueante_del_sha_vigente(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "ds-f12", _GhConComentario, nivel="integrado"
            )
            vuelta.gh.hallazgo = _medio("F12")
            e = vuelta("uno")
            self.assertEqual(e.nivel_alcanzado, "pr_abierto")
            self.assertEqual(_ids(e), ["F12"])
            self.assertEqual(e.deepseek_sha, e.pr_sha)

            revision = next(
                ev["data"]
                for ev in r.reg.eventos(r.enc)
                if ev["kind"] == "revision"
                and ev["data"].get("revisor") == "deepseek"
            )
            r.reg.anotar(
                r.enc,
                {
                    "kind": "revision",
                    "data": {
                        **revision,
                        "revision_id": "rev-vieja-f12",
                        "lector_version": 0,
                        "hora": r.hora,
                    },
                },
                "rev-vieja-f12",
            )
            e = r.fold()
            self.assertTrue(ciclo._deepseek_por_releer(e))

            vuelta.gh.hallazgo = None
            vuelta.gh.texto = (
                "DeepSeek review\n<!-- ai-review:sticky -->\n"
                'ai-review:completion={"status":"timeout","retry":false}\n'
                "ai-review:findings=[]\n"
                "la revisión excedió el tiempo límite de 900 s; no se reintenta"
            )
            hora = datetime.fromisoformat(e.hora.replace("Z", "+00:00"))
            tarde = (hora + timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%SZ")

            e = ciclo._paso_entrega(r.reg, e, vuelta.gh, tarde)

            self.assertEqual(e.nivel_alcanzado, "pr_abierto")
            self.assertEqual(_ids(e), ["F12"])
            pasos = [
                ev["data"].get("paso")
                for ev in r.reg.eventos(r.enc)
                if ev["kind"] == "entrega_externa"
            ]
            self.assertNotIn("deepseek_sin_revision", pasos)
            self.assertNotIn("integrado", pasos)


class CierreDiceQueDeepSeekNoReviso(unittest.TestCase):
    def test_la_evidencia_de_un_cierre_sin_revision_no_dice_leido(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "ds-cierre-sin-revision", FakeGitHub, nivel="pr"
            )
            e = vuelta("uno")
            hora = datetime.fromisoformat(e.hora.replace("Z", "+00:00"))
            tarde = (hora + timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%SZ")
            e = ciclo._paso_entrega(r.reg, r.fold(), vuelta.gh, tarde)
            self.assertEqual(e.estado, "terminado")

            cierre = json.loads(
                (r.base / "ejecucion" / r.enc / "cierre.json").read_text()
            )
            self.assertIn("DeepSeek no revisó", cierre["evidencia"])
            self.assertNotIn("DeepSeek leído", cierre["evidencia"])


class NotaTardiaRespetaElLimiteDelCuerpo(unittest.TestCase):
    def test_el_cuerpo_del_pr_no_pasa_del_limite(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "ds-limite-cuerpo", _GhPorNumero, nivel="integrado"
            )
            e = vuelta("uno")
            hora = datetime.fromisoformat(e.hora.replace("Z", "+00:00"))
            tarde = (hora + timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%SZ")
            e = ciclo._paso_entrega(r.reg, r.fold(), vuelta.gh, tarde)
            self.assertEqual(e.estado, "terminado")

            estado = json.loads((vuelta.gh.d / "github.json").read_text())
            pr = next(
                pr for pr in estado["prs"].values()
                if pr["numero"] == e.pr_numero
            )
            cuerpo_original = "x" * (ciclo._LIMITE_CUERPO_PR - 5)
            pr["cuerpo"] = cuerpo_original
            (vuelta.gh.d / "github.json").write_text(
                json.dumps(estado), encoding="utf-8"
            )

            vuelta.gh.textos[e.pr_numero] = _comentario(_medio("F26"))
            ciclo._revisar_deepseek_del_cerrado(
                r.reg, r.fold(), vuelta.gh, ahora_iso(), time.time()
            )

            estado = json.loads((vuelta.gh.d / "github.json").read_text())
            cuerpo = next(
                pr for pr in estado["prs"].values()
                if pr["numero"] == e.pr_numero
            )["cuerpo"]
            # No se recorta el cuerpo existente: se perderían notas previas.
            self.assertEqual(cuerpo, cuerpo_original)
            e = r.fold()
            self.assertTrue(
                any("llegó al límite" in n for n in e.notas_pr), e.notas_pr
            )


class SondeoAcotadoPorCierre(unittest.TestCase):
    def _terminado(self, reg, clave, hora, repo, numero, sha="a" * 40):
        enc = recibir(
            reg, "arregla el CLI", clave, hora,
            entrega_nivel="pr", repo=repo, base_rama="main",
        )["encargo"]
        reg.anotar(
            enc,
            {
                "kind": "fases",
                "data": {
                    "fases": [{"id": "uno", "resultado": "saludo.txt"}],
                    "hora": hora,
                },
            },
            f"{clave}:fases",
        )
        for paso in ("commit", "push", "pr_listo"):
            reg.anotar(
                enc,
                {
                    "kind": "entrega_externa",
                    "data": {
                        "fase": "uno",
                        "intento": "",
                        "paso": paso,
                        "repo": repo,
                        "rama": f"encargo/{clave}",
                        "base": "main",
                        "numero": numero,
                        "sha": sha,
                        "recibo": sha,
                        "hora": hora,
                    },
                },
                f"{clave}:{paso}",
            )
        reg.anotar(
            enc,
            {
                "kind": "entrega_externa",
                "data": {
                    "fase": "uno",
                    "intento": "",
                    "paso": "deepseek_leido",
                    "repo": repo,
                    "numero": numero,
                    "sha": sha,
                    "huella": "h",
                    "recibo": sha,
                    "hora": hora,
                },
            },
            f"{clave}:ds",
        )
        reg.anotar(
            enc,
            {
                "kind": "cierre_forzado",
                "data": {"evidencia": "cierre de prueba", "hora": hora},
            },
            f"{clave}:cierre",
        )
        return enc

    def test_los_viejos_no_se_sondean_y_el_recien_cerrado_si(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            repo = str(Path(tmp) / "repo.git")
            viejo = (
                datetime.now() - timedelta(days=30)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            for i in range(70):
                self._terminado(reg, f"viejo-{i}", viejo, repo, 1)
            gh = _GhCuenta(Path(tmp) / "gh")

            barrer(reg, ahora_iso(), time.time(), lambda *a, **k: None, gh=gh)
            self.assertEqual(gh.llamadas, [])

            # El marcador de «desde» ya existe: uno recién cerrado sí se sondea.
            self._terminado(reg, "nuevo", ahora_iso(), repo, 2)
            epoch = time.time()
            barrer(reg, ahora_iso(), epoch, lambda *a, **k: None, gh=gh)
            self.assertTrue(gh.llamadas)
            llamadas = len(gh.llamadas)

            # A los 7 días del cierre deja de sondearse.
            barrer(
                reg, ahora_iso(), epoch + 7 * 24 * 60 * 60 + 1,
                lambda *a, **k: None, gh=gh,
            )
            self.assertEqual(len(gh.llamadas), llamadas)

    def test_el_barrido_hace_a_lo_sumo_cinco_consultas_por_pasada(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            repo = str(Path(tmp) / "repo.git")
            viejo = (
                datetime.now() - timedelta(days=30)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._terminado(reg, "viejo", viejo, repo, 1)
            gh = _GhCuenta(Path(tmp) / "gh")
            barrer(reg, ahora_iso(), time.time(), lambda *a, **k: None, gh=gh)
            self.assertEqual(gh.llamadas, [])

            for i in range(10):
                self._terminado(reg, f"nuevo-{i}", ahora_iso(), repo, i + 2)
            epoch = time.time()
            barrer(reg, ahora_iso(), epoch, lambda *a, **k: None, gh=gh)
            self.assertEqual(len(gh.llamadas), 5)

            barrer(
                reg, ahora_iso(), epoch + 120,
                lambda *a, **k: None, gh=gh,
            )
            self.assertEqual(len(gh.llamadas), 10)

    def test_el_marcador_sobrevive_a_la_limpieza_de_intentos(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            repo = str(Path(tmp) / "repo.git")
            enc = self._terminado(reg, "nuevo", ahora_iso(), repo, 1)
            intento = Path(reg.base) / "ejecucion" / enc / "intento-1-x"
            intento.mkdir(parents=True, exist_ok=True)
            (Path(reg.base) / "ejecucion" / enc / "actual").write_text(
                "intento-1-x", encoding="utf-8"
            )
            gh = _GhCuenta(Path(tmp) / "gh")
            epoch = time.time()
            barrer(reg, ahora_iso(), epoch, lambda *a, **k: None, gh=gh)
            self.assertTrue(gh.llamadas)

            ciclo._limpiar_encargo_cerrado(reg, reg.fold(enc), ahora_iso())
            marca = (
                Path(reg.base) / "ejecucion" / enc / "deepseek_cerrado.json"
            )
            self.assertTrue(marca.is_file())

            gh.llamadas.clear()
            barrer(
                reg, ahora_iso(), epoch + 120,
                lambda *a, **k: None, gh=gh,
            )
            self.assertTrue(gh.llamadas)


if __name__ == "__main__":
    unittest.main()
