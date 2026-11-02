"""Los checks que corren en el PR frenan la entrega `pr` aunque nadie los
marque requeridos: un rojo devuelve el trabajo con el nombre del job y su log
literal, un pendiente espera y relee, y todo verde (un salto o neutral cuenta
por su conclusión, no por su nombre) termina. Es el caso de `enc_25d22b9bfce242f9`:
DeepSeek aprobó con `windows` en rojo y `bateria` corriendo y el encargo cerró."""

from __future__ import annotations

import json
import tempfile
import unittest

from motor.encargos import ciclo
from motor.encargos.ciclo import _paso_entrega, ahora_iso
from motor.github import veredicto
from tests.test_flujo_revisiones import (
    _GhConDeepSeek,
    _entrega_pr,
    sonda_sin_aislamiento_del_host,
)


def setUpModule():
    sonda_sin_aislamiento_del_host()


class _GhConCorridas(_GhConDeepSeek):
    """DeepSeek aprueba y los checks del SHA son corridas de GitHub. El config
    del repositorio declara `corridas` con el estado de cada una y `logs` con
    el log literal por run_id; la lista fija de requeridos queda vacía, como en
    un repo sin protección de rama ni `.motor.toml`."""

    titulos = ()

    def checks(self, repo, numero, sha, extra, relanzadas=(), *, base_politica=""):
        cfg = self._cfg(repo)
        estados = {
            "success": ("completed", "success"),
            "failure": ("completed", "failure"),
            "in_progress": ("in_progress", None),
            "skipped": ("completed", "skipped"),
            "neutral": ("completed", "neutral"),
        }
        runs = []
        for nombre, estado in (cfg.get("corridas") or {}).items():
            status, conclusion = estados[estado]
            rid = 40000 + len(runs)
            runs.append(
                {
                    "name": nombre,
                    "status": status,
                    "conclusion": conclusion,
                    "completed_at": "2026-11-02T21:46:00Z",
                    "id": rid,
                    "html_url": (
                        f"https://github.com/o/r/actions/runs/{rid}/job/{rid + 100000}"
                    ),
                    "details_url": (
                        f"https://github.com/o/r/actions/runs/{rid}/job/{rid + 100000}"
                    ),
                }
            )
        return veredicto(
            sha,
            tuple(dict.fromkeys([*cfg.get("requeridos", []), *extra])),
            runs,
            "",
        )

    def log_fallido(self, repo, run_id) -> str:
        return str((self._cfg(repo).get("logs") or {}).get(str(run_id), ""))


def _corridas(gh, repo, estados):
    cfg = json.loads((gh.d / "repos.json").read_text())
    cfg[repo]["corridas"] = estados
    (gh.d / "repos.json").write_text(json.dumps(cfg))


LOG_WINDOWS = "ERROR: 3 failed, 0 passed in tests/test_director_capacidades.py"


class CheckRojoSinListaFija(unittest.TestCase):
    def test_deepseek_aprueba_con_windows_en_rojo_y_no_termina(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ck-rojo",
                _GhConCorridas,
                {
                    "corridas": {"windows": "failure"},
                    "logs": {"40000": LOG_WINDOWS},
                },
            )
            e = vuelta("uno")
            self.assertNotEqual(e.estado, "terminado")
            self.assertEqual(e.estrategia, "investigar")
            self.assertIn("checks requeridos en rojo: windows", e.motivo)
            self.assertIn(LOG_WINDOWS, e.motivo)
            self.assertEqual(e.fase_actual.fallas_checks, ("windows",))
            self.assertEqual(e.fase_actual.entrega_bloqueada_sha, e.producto.sha_git)


class CheckPendienteSinListaFija(unittest.TestCase):
    def test_deepseek_aprueba_con_bateria_pendiente_y_espera(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ck-pend",
                _GhConCorridas,
                {"corridas": {"windows": "success", "bateria": "in_progress"}},
            )
            e = vuelta("uno")
            sha = e.producto.sha_git
            self.assertNotEqual(e.estado, "terminado")
            self.assertIn("esperando checks requeridos del PR #", e.motivo)
            self.assertIn("bateria", e.motivo)
            self.assertEqual(e.fase_actual.checks_esperando_sha, sha)

            _corridas(vuelta.gh, str(e.entrega.repo), {"bateria": "success"})
            e = _paso_entrega(r.reg, r.fold(), vuelta.gh, ahora_iso())
            self.assertEqual(e.estado, "terminado")


class TodoVerdeConUnSaltado(unittest.TestCase):
    def test_todo_verde_y_coderabbit_en_skipping_termina(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ck-verde",
                _GhConCorridas,
                {
                    "corridas": {
                        "windows": "success",
                        "bateria": "success",
                        "CodeRabbit": "skipped",
                    }
                },
            )
            e = vuelta("uno")
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.fase_actual.fallas_checks, ())


class CierrePorAtajoMiraLosChecks(unittest.TestCase):
    """DeepSeek leyó y aprobó mientras `bateria` seguía corriendo (la cronología
    de `enc_25d22b9bfce242f9`); el job se puso en rojo después: el cierre por el
    atajo de `_paso_entrega` mira los checks del head y no termina."""

    def test_el_atajo_no_cierra_con_un_check_en_rojo(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ck-atajo",
                _GhConCorridas,
                {
                    "corridas": {"windows": "success", "bateria": "in_progress"},
                    "logs": {"40001": LOG_WINDOWS},
                },
            )
            e = vuelta("uno")
            self.assertNotEqual(e.estado, "terminado")
            sha = e.producto.sha_git
            e = ciclo._anotar_lectura_deepseek(
                r.reg,
                r.fold(),
                e.pr_numero,
                sha,
                [{"texto": "DeepSeek: APPROVED", "autor": "DeepSeek", "sha": sha}],
                ahora_iso(),
            )
            self.assertEqual(e.deepseek_sha, sha)
            self.assertEqual(e.nivel_alcanzado, "pr")

            _corridas(
                vuelta.gh,
                str(e.entrega.repo),
                {"windows": "success", "bateria": "failure"},
            )
            e = _paso_entrega(r.reg, r.fold(), vuelta.gh, ahora_iso())
            self.assertNotEqual(e.estado, "terminado")
            self.assertEqual(e.estrategia, "investigar")
            self.assertIn("checks requeridos en rojo: bateria", e.motivo)
            self.assertIn(LOG_WINDOWS, e.motivo)
            self.assertEqual(e.fase_actual.fallas_checks, ("bateria",))


if __name__ == "__main__":
    unittest.main()
