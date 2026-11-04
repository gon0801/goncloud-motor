"""El CI deja de ser compuerta: la entrega `pr` termina con la aprobación y el
CI corre en el PR real. Se lee cuando termina; un rojo de prueba vuelve al
responsable, uno de tubería se relanza una sola vez y solo avisa si es fijo,
uno incierto recomienda una revisión, y un pendiente o verde no detiene nada.
El aviso es para alguien no técnico: dice qué falló, de quién es y qué hacer."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from motor.encargos import ciclo
from motor.encargos.ciclo import ahora_iso
from motor.github import veredicto
from motor.encargos.dominio import prompt_responsable
from motor.integraciones import directores
from tests.test_flujo_revisiones import (
    _GhConDeepSeek,
    _entrega_pr,
    sonda_sin_aislamiento_del_host,
)

LOG_BATERIA = "ERROR: 3 failed, 0 passed in tests/test_bateria.py"
LOG_GATE = "leaks found: 13\nfinding: generic-api-key en docs/viejo.md"
LOG_INCIERTO = "fallo ambiguo sin diagnóstico"


def setUpModule():
    sonda_sin_aislamiento_del_host()


class _GhConCorridas(_GhConDeepSeek):
    titulos = ()

    def __init__(self, d):
        super().__init__(d)
        self._job_ids = {}
        self.relanzadas = []
        self._reintentos = {}

    def checks(self, repo, numero, sha, extra, relanzadas=(), *, base_politica=""):
        cfg = self._cfg(repo)
        estados = {
            "success": ("completed", "success"),
            "failure": ("completed", "failure"),
            "timed_out": ("completed", "timed_out"),
            "startup_failure": ("completed", "startup_failure"),
            "in_progress": ("in_progress", None),
            "cancelled": ("completed", "cancelled"),
            "skipped": ("completed", "skipped"),
            "neutral": ("completed", "neutral"),
        }
        if self._reintentos.get(repo) and "corridas_tras_reintento" in cfg:
            corridas = cfg["corridas_tras_reintento"]
        else:
            corridas = (cfg.get("corridas_sha") or {}).get(
                sha, cfg.get("corridas") or {}
            )
        runs = []
        for nombre, estado in corridas.items():
            status, conclusion = estados[estado]
            rid = 40000 + len(runs)
            job_id = rid
            self._job_ids[(repo, nombre)] = str(job_id)
            runs.append(
                {
                    "name": nombre,
                    "status": status,
                    "conclusion": conclusion,
                    "completed_at": "2026-11-03T21:46:00Z",
                    "id": job_id,
                    "html_url": f"https://github.com/o/r/actions/runs/{rid}/job/{job_id}",
                    "details_url": f"https://github.com/o/r/actions/runs/{rid}/job/{job_id}",
                }
            )
        return veredicto(sha, tuple(extra), runs, "")

    def log_fallido_job(self, repo, run_id, job, job_id=""):
        logs = self._cfg(repo).get("logs_job") or {}
        return str(logs.get(job, ""))

    def relanzar_fallidas(self, repo, run_id):
        self.relanzadas.append((repo, str(run_id)))
        self._reintentos[repo] = self._reintentos.get(repo, 0) + 1


def _segunda_corrida(vuelta, repo: str, corridas: dict, logs: dict | None = None):
    ruta = vuelta.gh.d / "repos.json"
    cfg = json.loads(ruta.read_text())
    entrada = cfg[str(repo)]
    entrada["corridas_tras_reintento"] = corridas
    if logs is not None:
        entrada["logs_job"] = logs
    ruta.write_text(json.dumps(cfg))


def _avisos(e):
    return [h for h in directores.hechos_de_avance(e) if h["tipo"] == "ci_rojo"]


def _estados_ci(r):
    return [
        ev["data"]["estado"]
        for ev in r.reg.eventos(r.enc)
        if ev["kind"] == "ci_pr"
    ]


class EntregaSinCiAntesDelPr(unittest.TestCase):
    def test_termina_con_la_aprobacion_sin_pedir_ci(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "ci-pr-1", _GhConCorridas)
            e = vuelta("hola\n")
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.nivel_alcanzado, "pr")
            self.assertIsNotNone(e.pr_numero)
            self.assertEqual(r.eventos("solicitud"), [])
            self.assertFalse(
                [ev for ev in r.reg.eventos(r.enc) if ev["kind"] == "ci_producto"]
            )


class RojoDePruebaVuelveAlResponsable(unittest.TestCase):
    def test_devuelve_el_log_una_sola_vez_en_el_mismo_pr(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-2",
                _GhConCorridas,
                {"corridas": {"bateria": "failure"}, "logs_job": {"bateria": LOG_BATERIA}},
            )
            e = vuelta("hola\n")
            self.assertEqual(e.estado, "terminado")
            numero = e.pr_numero

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, ahora_iso())
            self.assertEqual(e.estado, "recibido")
            self.assertEqual(e.rol, "responsable")
            self.assertEqual(e.pr_numero, numero)
            self.assertEqual(e.fase_actual.ci_pr_estado, "rojo")
            self.assertEqual(e.fase_actual.ci_pr_clase, "codigo")
            self.assertEqual(_estados_ci(r), ["rojo"])
            self.assertEqual(vuelta.gh.relanzadas, [])
            avisos = _avisos(e)
            self.assertEqual(len(avisos), 1)
            self.assertIn("bateria", prompt_responsable(e))
            self.assertIn(LOG_BATERIA, prompt_responsable(e))

            e2 = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, ahora_iso())
            self.assertEqual(e2.estado, "recibido")
            self.assertEqual(_estados_ci(r), ["rojo"])

            corregido = vuelta("hola corregido\n")
            self.assertEqual(corregido.estado, "terminado")
            self.assertEqual(corregido.pr_numero, numero)

    def test_el_aviso_de_codigo_es_llano_y_recomienda_no_hacer_nada(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-2b",
                _GhConCorridas,
                {"corridas": {"bateria": "failure"}, "logs_job": {"bateria": LOG_BATERIA}},
            )
            e = vuelta("hola\n")
            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, ahora_iso())
            avisos = _avisos(e)
            self.assertEqual(len(avisos), 1)
            texto = avisos[0]["texto"]
            self.assertIn("código del cambio", texto)
            self.assertIn(
                "ya se mandó a corregir en el mismo pr, no tienes que hacer nada",
                texto.lower(),
            )
            self.assertNotIn(LOG_BATERIA, texto)
            self.assertNotIn("docs/", texto)


class RojoDeTuberiaSeRelanzaUnaVez(unittest.TestCase):
    def test_pasajero_pasa_en_la_segunda_y_no_avisa(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-3",
                _GhConCorridas,
                {"corridas": {"gate": "failure"}, "logs_job": {"gate": LOG_GATE}},
            )
            e = vuelta("hola\n")
            repo = e.entrega.repo

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, ahora_iso())
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.fase_actual.ci_pr_estado, "reintentando")
            self.assertEqual(len(vuelta.gh.relanzadas), 1)
            self.assertEqual(_avisos(e), [])

            _segunda_corrida(vuelta, repo, {"gate": "success"})
            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, ahora_iso())
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.fase_actual.ci_pr_estado, "verde")
            self.assertEqual(_avisos(e), [])
            self.assertEqual(len(vuelta.gh.relanzadas), 1)
            self.assertEqual(_estados_ci(r), ["reintentando", "verde"])

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, ahora_iso())
            self.assertEqual(len(vuelta.gh.relanzadas), 1)
            self.assertEqual(_estados_ci(r), ["reintentando", "verde"])

    def test_permanente_falla_otra_vez_y_avisa_en_llano(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-4",
                _GhConCorridas,
                {"corridas": {"gate": "failure"}, "logs_job": {"gate": LOG_GATE}},
            )
            e = vuelta("hola\n")
            repo = e.entrega.repo

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, ahora_iso())
            self.assertEqual(e.fase_actual.ci_pr_estado, "reintentando")
            self.assertEqual(len(vuelta.gh.relanzadas), 1)

            _segunda_corrida(vuelta, repo, {"gate": "failure"}, {"gate": LOG_GATE})
            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, ahora_iso())
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.fase_actual.ci_pr_estado, "rojo")
            self.assertEqual(e.fase_actual.ci_pr_clase, "tuberia")
            self.assertEqual(e.fase_actual.ci_pr_reintento, "permanente")
            self.assertEqual(len(vuelta.gh.relanzadas), 1)
            avisos = _avisos(e)
            self.assertEqual(len(avisos), 1)
            texto = avisos[0]["texto"]
            self.assertIn("tubería", texto)
            self.assertIn("problema fijo", texto)
            self.assertIn("control de secretos", texto)
            self.assertIn("el cambio ya está revisado; recomiendo mergear", texto.lower())
            self.assertIn("abrir un encargo para arreglar la tubería", texto.lower())
            self.assertNotIn(LOG_GATE, texto)

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, ahora_iso())
            self.assertEqual(len(vuelta.gh.relanzadas), 1)
            self.assertEqual(_estados_ci(r), ["reintentando", "rojo"])
            self.assertEqual(len(_avisos(e)), 1)


class RojoInciertoNoDevuelveNiReintenta(unittest.TestCase):
    def test_avisa_que_no_puede_clasificar_y_recomienda_a_claude(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-5",
                _GhConCorridas,
                {
                    "corridas": {"bateria": "failure"},
                    "logs_job": {"bateria": LOG_INCIERTO},
                },
            )
            e = vuelta("hola\n")
            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, ahora_iso())
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.fase_actual.ci_pr_estado, "rojo")
            self.assertEqual(e.fase_actual.ci_pr_clase, "incierto")
            self.assertEqual(vuelta.gh.relanzadas, [])
            avisos = _avisos(e)
            self.assertEqual(len(avisos), 1)
            texto = avisos[0]["texto"]
            self.assertIn("no puedo saber con certeza", texto)
            self.assertIn("Claude", texto)


class ClasificacionDeUnFallo(unittest.TestCase):
    def test_un_job_conocido_sin_diagnostico_es_incierto(self):
        self.assertEqual(
            ciclo._clasificar_fallo_ci("bateria", "failure", LOG_INCIERTO),
            "incierto",
        )

    def test_gitleaks_es_tuberia_y_una_prueba_roja_es_codigo(self):
        self.assertEqual(
            ciclo._clasificar_fallo_ci("gate", "failure", LOG_GATE), "tuberia"
        )
        self.assertEqual(
            ciclo._clasificar_fallo_ci("bateria", "failure", LOG_BATERIA), "codigo"
        )


class PendienteOVerdeNoDetiene(unittest.TestCase):
    def test_pendiente_no_hace_nada_y_verde_cierra_sin_devolver(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-6",
                _GhConCorridas,
                {"corridas": {"bateria": "in_progress"}},
            )
            e = vuelta("hola\n")
            self.assertEqual(e.estado, "terminado")

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, ahora_iso())
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(_estados_ci(r), [])

            cfg = json.loads((vuelta.gh.d / "repos.json").read_text())
            cfg[str(e.entrega.repo)]["corridas"] = {"bateria": "success"}
            (vuelta.gh.d / "repos.json").write_text(json.dumps(cfg))
            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, ahora_iso())
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.fase_actual.ci_pr_estado, "verde")
            self.assertEqual(_estados_ci(r), ["verde"])


if __name__ == "__main__":
    unittest.main()
