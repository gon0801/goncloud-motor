"""Un rechazo transitorio de merge espera CI sin pedir una decisión humana."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from motor.encargos.ciclo import barrer
from motor.github import GhGitHub, PoliticaBloquea, PuntaMovida
from tests.test_flujo_revisiones import (
    _GhConDeepSeek,
    _entrega_pr,
    sonda_sin_aislamiento_del_host,
)


def setUpModule():
    sonda_sin_aislamiento_del_host()


class _GitHubChecks(GhGitHub):
    """Respuestas del CLI; el puerto real interpreta los requeridos."""

    def __init__(self):
        super().__init__()
        self.sha = "a" * 40
        self.revision = "APPROVED"
        self.requeridos = [{"name": "ci", "bucket": "pending", "state": "IN_PROGRESS"}]
        self.error = ""
        self.llamadas = []

    def _gh(self, *args):
        self.llamadas.append(args)
        if args[:2] == ("pr", "view"):
            return json.dumps({"headRefOid": self.sha, "reviewDecision": self.revision})
        if args[:2] == ("pr", "checks"):
            if self.error:
                raise RuntimeError(self.error)
            if "--required" in args:
                return json.dumps(self.requeridos)
            return json.dumps([
                *self.requeridos,
                {"name": "opcional", "bucket": "fail", "state": "FAILURE"},
            ])
        raise AssertionError(args)


class _PoliticaConChecks(_GhConDeepSeek):
    titulos = ()

    def __init__(self, directorio):
        super().__init__(directorio)
        self.api = _GitHubChecks()
        self.bloquea = True
        self.intentos = []

    def merge(self, repo, numero, sha, metodo, mensaje):
        self.intentos.append(sha)
        self.api.sha = sha
        if self.bloquea:
            raise PoliticaBloquea("the base branch policy prohibits the merge")
        return super().merge(repo, numero, sha, metodo, mensaje)

    def checks_requeridos(self, repo, numero, sha):
        return self.api.checks_requeridos("o/r", numero, sha)


class EntregaMergePendiente(unittest.TestCase):
    def preparar(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory(ignore_cleanup_errors=True))
        return _entrega_pr(self, tmp, "merge-ci", _PoliticaConChecks, nivel="integrado")

    def test_requerido_en_marcha_espera_y_el_supervisor_integra_sin_preguntar(self):
        vuelta, r = self.preparar()
        e = vuelta("hola")
        self.assertEqual(e.estado, "esperando_reintento")
        self.assertTrue(e.reanuda_en)
        self.assertEqual(e.nivel_alcanzado, "pr")
        self.assertEqual(e.preguntas, ())
        self.assertIn("ci", e.motivo)
        revisiones = r.eventos("revision")
        self.assertFalse(any(
            ev["data"].get("paso") == "integrado" for ev in r.eventos("entrega_externa")
        ))

        vuelta.gh.bloquea = False
        vuelta.gh.api.requeridos = [{"name": "ci", "bucket": "pass", "state": "SUCCESS"}]
        epoch = datetime.fromisoformat(e.reanuda_en.replace("Z", "+00:00")).timestamp()
        barrer(r.reg, e.reanuda_en, epoch, None, gh=vuelta.gh)
        final = r.fold()
        self.assertEqual(final.estado, "terminado")
        self.assertEqual(final.nivel_alcanzado, "integrado")
        self.assertEqual(final.pr_sha, e.pr_sha)
        self.assertEqual(final.preguntas, ())
        self.assertEqual(vuelta.gh.intentos, [e.pr_sha, e.pr_sha])
        self.assertEqual(r.eventos("revision"), revisiones)

    def test_requerido_fallido_pregunta_aunque_otro_siga_en_marcha(self):
        vuelta, r = self.preparar()
        vuelta.gh.api.requeridos.append({"name": "seguridad", "bucket": "fail", "state": "FAILURE"})
        e = vuelta("hola")
        self.assertEqual(e.estado, "necesita_usuario")
        self.assertEqual(e.nivel_alcanzado, "pr")
        self.assertTrue(e.preguntas)

    def test_revision_humana_requerida_pregunta_aunque_ci_siga_en_marcha(self):
        vuelta, r = self.preparar()
        vuelta.gh.api.revision = "REVIEW_REQUIRED"
        e = vuelta("hola")
        self.assertEqual(e.estado, "necesita_usuario")
        self.assertEqual(e.nivel_alcanzado, "pr")
        self.assertTrue(e.preguntas)

    def test_otro_bloqueo_de_politica_con_checks_verdes_pregunta(self):
        vuelta, r = self.preparar()
        vuelta.gh.api.requeridos = [{"name": "ci", "bucket": "pass", "state": "SUCCESS"}]
        e = vuelta("hola")
        self.assertEqual(e.estado, "necesita_usuario")
        self.assertEqual(e.nivel_alcanzado, "pr")

    def test_fallo_al_leer_checks_reintenta_sin_decision_humana(self):
        vuelta, r = self.preparar()
        vuelta.gh.api.error = "HTTP 503"
        e = vuelta("hola")
        self.assertEqual(e.estado, "esperando_reintento")
        self.assertEqual(e.preguntas, ())
        self.assertIn("503", e.motivo)


class ChecksRequeridosParaMerge(unittest.TestCase):
    def test_un_opcional_fallido_no_impide_esperar_al_requerido(self):
        gh = _GitHubChecks()
        v = gh.checks_requeridos("o/r", 7, gh.sha)
        self.assertEqual(v.en_marcha, ("ci",))
        self.assertEqual(v.faltan, ("ci",))
        self.assertEqual(v.fallas, ())

    def test_pendientes_sin_ejecucion_no_se_confunden_con_corridas_activas(self):
        for estado in ("STALE", "EXPECTED"):
            with self.subTest(estado=estado):
                gh = _GitHubChecks()
                gh.requeridos = [{"name": "ci", "bucket": "pending", "state": estado}]
                v = gh.checks_requeridos("o/r", 7, gh.sha)
                self.assertEqual(v.faltan, ("ci",))
                self.assertEqual(v.en_marcha, ())

    def test_check_cancelado_no_se_espera_como_si_siguiera_corriendo(self):
        gh = _GitHubChecks()
        gh.requeridos = [{"name": "ci", "bucket": "cancel", "state": "CANCELLED"}]
        v = gh.checks_requeridos("o/r", 7, gh.sha)
        self.assertEqual(v.fallas, ("ci",))
        self.assertEqual(v.en_marcha, ())

    def test_no_hay_requeridos_es_un_bloqueo_distinto_de_ci(self):
        gh = _GitHubChecks()
        gh.error = "no required checks reported on the 'feat' branch"
        v = gh.checks_requeridos("o/r", 7, gh.sha)
        self.assertEqual(v.en_marcha, ())

    def test_la_lectura_rechaza_una_punta_distinta_de_la_revisada(self):
        gh = _GitHubChecks()
        with self.assertRaises(PuntaMovida):
            gh.checks_requeridos("o/r", 7, "b" * 40)

    def test_el_merge_sigue_fijado_al_sha_y_sin_admin(self):
        gh = GhGitHub()
        comandos = []

        def ejecutar(*args):
            comandos.append(args)
            raise RuntimeError("the base branch policy prohibits the merge")

        with mock.patch.object(gh, "_gh", side_effect=ejecutar):
            with self.assertRaises(PoliticaBloquea):
                gh.merge("o/r", 7, "a" * 40, "squash", "entrega")
        comando, = comandos
        self.assertEqual(comando[comando.index("--match-head-commit") + 1], "a" * 40)
        self.assertNotIn("--admin", comando)
