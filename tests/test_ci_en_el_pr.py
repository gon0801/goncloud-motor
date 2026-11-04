"""El CI deja de ser compuerta: la entrega `pr` termina con la aprobación y el
CI corre en el PR real. Se lee una vez cuando termina; un rojo de prueba vuelve
al responsable, uno de infraestructura solo avisa, y un pendiente o verde no
detiene nada."""

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


def setUpModule():
    sonda_sin_aislamiento_del_host()


class _GhConCorridas(_GhConDeepSeek):
    titulos = ()

    def __init__(self, d):
        super().__init__(d)
        self._job_ids = {}

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
        corridas = (cfg.get("corridas_sha") or {}).get(sha, cfg.get("corridas") or {})
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
            eventos = [ev for ev in r.reg.eventos(r.enc) if ev["kind"] == "ci_pr"]
            self.assertEqual(len(eventos), 1)
            hechos = directores.hechos_de_avance(e)
            avisos = [h for h in hechos if h["tipo"] == "ci_rojo"]
            self.assertEqual(len(avisos), 1)
            self.assertIn("bateria", avisos[0]["texto"])
            self.assertIn(LOG_BATERIA, avisos[0]["texto"])
            self.assertIn("bateria", prompt_responsable(e))
            self.assertIn(LOG_BATERIA, prompt_responsable(e))

            e2 = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, ahora_iso())
            self.assertEqual(e2.estado, "recibido")
            eventos = [ev for ev in r.reg.eventos(r.enc) if ev["kind"] == "ci_pr"]
            self.assertEqual(len(eventos), 1)

            corregido = vuelta("hola corregido\n")
            self.assertEqual(corregido.estado, "terminado")
            self.assertEqual(corregido.pr_numero, numero)


class RojoDeInfraestructuraSoloAvisa(unittest.TestCase):
    def test_no_devuelve_el_trabajo(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-3",
                _GhConCorridas,
                {"corridas": {"gate": "failure"}, "logs_job": {"gate": LOG_GATE}},
            )
            e = vuelta("hola\n")
            self.assertEqual(e.estado, "terminado")

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, ahora_iso())
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.fase_actual.ci_pr_estado, "rojo")
            self.assertEqual(e.fase_actual.ci_pr_fallas, ("gate (failure)",))
            avisos = [
                h for h in directores.hechos_de_avance(e) if h["tipo"] == "ci_rojo"
            ]
            self.assertEqual(len(avisos), 1)
            self.assertIn("gate", avisos[0]["texto"])
            self.assertIn("leaks found", avisos[0]["texto"])


class PendienteOVerdeNoDetiene(unittest.TestCase):
    def test_pendiente_no_hace_nada_y_verde_cierra_sin_devolver(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-4",
                _GhConCorridas,
                {"corridas": {"bateria": "in_progress"}},
            )
            e = vuelta("hola\n")
            self.assertEqual(e.estado, "terminado")

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, ahora_iso())
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(
                [ev for ev in r.reg.eventos(r.enc) if ev["kind"] == "ci_pr"], []
            )

            cfg = json.loads((vuelta.gh.d / "repos.json").read_text())
            cfg[str(e.entrega.repo)]["corridas"] = {"bateria": "success"}
            (vuelta.gh.d / "repos.json").write_text(json.dumps(cfg))
            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, ahora_iso())
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.fase_actual.ci_pr_estado, "verde")
            eventos = [ev for ev in r.reg.eventos(r.enc) if ev["kind"] == "ci_pr"]
            self.assertEqual(len(eventos), 1)


if __name__ == "__main__":
    unittest.main()
