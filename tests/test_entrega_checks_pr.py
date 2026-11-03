"""Los checks que corren en el PR frenan la entrega `pr` aunque nadie los
marque requeridos: un rojo devuelve el trabajo con el nombre del job y su log
literal, un pendiente espera y relee, y todo verde (un salto o neutral cuenta
por su conclusión, no por su nombre) termina. Es el caso de `enc_25d22b9bfce242f9`:
DeepSeek aprobó con `windows` en rojo y `bateria` corriendo y el encargo cerró."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

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
    del repositorio declara `corridas` —o `corridas_sha` por SHA, para un head
    que se mueve— con el estado de cada una, `logs` con lo que devolvería el
    `--log-failed` agregado de cada run, y `logs_job` con el log literal por
    job; la lista fija de requeridos queda vacía, como en un repo sin
    protección de rama ni `.motor.toml`."""

    titulos = ()

    def checks(self, repo, numero, sha, extra, relanzadas=(), *, base_politica=""):
        cfg = self._cfg(repo)
        estados = {
            "success": ("completed", "success"),
            "failure": ("completed", "failure"),
            "timed_out": ("completed", "timed_out"),
            "startup_failure": ("completed", "startup_failure"),
            "in_progress": ("in_progress", None),
            "skipped": ("completed", "skipped"),
            "neutral": ("completed", "neutral"),
            "cancelled": ("completed", "cancelled"),
            "action_required": ("completed", "action_required"),
            "stale": ("completed", "stale"),
        }
        corridas = (cfg.get("corridas_sha") or {}).get(
            sha, cfg.get("corridas") or {}
        )
        if not hasattr(self, "_job_ids"):
            self._job_ids = {}
        runs = []
        for nombre, estado in corridas.items():
            run_fijo = None
            if isinstance(estado, dict):
                run_fijo = {"run": estado.get("run"), "id": estado.get("id")}
                estado = estado["estado"]
            status, conclusion = estados[estado]
            rid = 40000 + len(runs)
            run_id = (run_fijo or {}).get("run") or rid
            job_id = (run_fijo or {}).get("id") or rid
            self._job_ids[(repo, nombre)] = str(job_id)
            runs.append(
                {
                    "name": nombre,
                    "status": status,
                    "conclusion": conclusion,
                    "completed_at": "2026-11-02T21:46:00Z",
                    "id": job_id,
                    "html_url": (
                        f"https://github.com/o/r/actions/runs/{run_id}/job/{job_id}"
                    ),
                    "details_url": (
                        f"https://github.com/o/r/actions/runs/{run_id}/job/{job_id}"
                    ),
                }
            )
        return veredicto(
            sha,
            tuple(dict.fromkeys([*cfg.get("requeridos", []), *extra])),
            runs,
            "",
            list(cfg.get("estados") or []),
        )

    def log_fallido(self, repo, run_id) -> str:
        return str((self._cfg(repo).get("logs") or {}).get(str(run_id), ""))

    def log_fallido_job(self, repo, run_id, job, job_id="") -> str:
        jid = str(job_id or "")
        if not hasattr(self, "llamadas_log_job"):
            self.llamadas_log_job = []
        self.llamadas_log_job.append((repo, str(run_id), job, jid))
        esperado = getattr(self, "_job_ids", {}).get((repo, job), "")
        if not jid or (esperado and jid != esperado):
            return ""
        logs = self._cfg(repo).get("logs_job") or {}
        return str(logs.get(jid, logs.get(job, "")))


def _corridas(gh, repo, estados):
    cfg = json.loads((gh.d / "repos.json").read_text())
    cfg[repo]["corridas"] = estados
    (gh.d / "repos.json").write_text(json.dumps(cfg))


def _corridas_sha(gh, repo, por_sha, logs_job=None):
    cfg = json.loads((gh.d / "repos.json").read_text())
    cfg[repo]["corridas_sha"] = por_sha
    if logs_job is not None:
        cfg[repo]["logs_job"] = logs_job
    (gh.d / "repos.json").write_text(json.dumps(cfg))


LOG_WINDOWS = "ERROR: 3 failed, 0 passed in tests/test_director_capacidades.py"
LOG_BATERIA = "ERROR: 5 failed, 0 passed in tests/test_bateria.py"


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
                    "logs_job": {"windows": LOG_WINDOWS},
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
                    "logs_job": {"bateria": LOG_WINDOWS},
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


class ElCierreConciliaLaCabezaDelPR(unittest.TestCase):
    """El atajo de cierre juzga el head vigente del PR, no el SHA guardado
    (h_9d9b387c): DeepSeek aprobó A, alguien empujó B a la rama del PR y B está
    en rojo. Terminar con A aprobado y B en rojo es el mismo tipo de entrega
    rota que enc_25d22b9bfce242f9."""

    def test_un_push_posterior_en_rojo_no_cierra_terminado(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ck-cabeza",
                _GhConCorridas,
                {"corridas": {"windows": "success", "bateria": "in_progress"}},
            )
            e = vuelta("uno")
            self.assertNotEqual(e.estado, "terminado")
            repo = str(e.entrega.repo)
            sha_a = e.producto.sha_git
            e = ciclo._anotar_lectura_deepseek(
                r.reg,
                r.fold(),
                e.pr_numero,
                sha_a,
                [{"texto": "DeepSeek: APPROVED", "autor": "DeepSeek", "sha": sha_a}],
                ahora_iso(),
            )
            self.assertEqual(e.deepseek_sha, sha_a)

            rama = vuelta.gh.ver(repo, e.pr_numero).rama
            clon = Path(tmp) / "clon"
            _git("clone", "--quiet", repo, str(clon))
            _git("-C", str(clon), "checkout", "--quiet", rama)
            (clon / "otro.txt").write_text("otro\n")
            _git("-C", str(clon), "add", "-A")
            _git(
                "-C", str(clon),
                "-c", "user.name=t", "-c", "user.email=t@l",
                "commit", "--quiet", "-m", "push externo",
            )
            _git("-C", str(clon), "push", "--quiet", "origin", rama)
            sha_b = subprocess.run(
                ["git", "-C", str(clon), "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            self.assertNotEqual(sha_a, sha_b)

            _corridas_sha(
                vuelta.gh,
                repo,
                {
                    sha_a: {"windows": "success", "bateria": "success"},
                    sha_b: {"windows": "failure"},
                },
                logs_job={"windows": LOG_WINDOWS},
            )
            e = _paso_entrega(r.reg, r.fold(), vuelta.gh, ahora_iso())
            self.assertNotEqual(e.estado, "terminado")
            self.assertEqual(e.estrategia, "investigar")
            self.assertIn("checks requeridos en rojo: windows", e.motivo)
            self.assertIn(LOG_WINDOWS, e.motivo)
            self.assertEqual(e.fase_actual.fallas_checks, ("windows",))
            self.assertEqual(e.fase_actual.entrega_bloqueada_sha, sha_b)

    def test_un_push_posterior_en_verde_no_cierra_con_el_sha_guardado(self):
        """F6: el head vigente gobierna el cierre, no solo los checks. Con B
        verde pero el sha aprobado A guardado, cerrar daría por terminado un
        PR cuyo head es contenido que nadie aprobó: el trabajo vuelve nombrando
        ambos shas."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ck-head-verde",
                _GhConCorridas,
                {"corridas": {"windows": "success", "bateria": "in_progress"}},
            )
            e = vuelta("uno")
            self.assertNotEqual(e.estado, "terminado")
            repo = str(e.entrega.repo)
            sha_a = e.producto.sha_git
            e = ciclo._anotar_lectura_deepseek(
                r.reg,
                r.fold(),
                e.pr_numero,
                sha_a,
                [{"texto": "DeepSeek: APPROVED", "autor": "DeepSeek", "sha": sha_a}],
                ahora_iso(),
            )
            rama = vuelta.gh.ver(repo, e.pr_numero).rama
            sha_b = _empujar_head(tmp, repo, rama)
            _corridas_sha(
                vuelta.gh,
                repo,
                {
                    sha_a: {"windows": "success", "bateria": "success"},
                    sha_b: {"windows": "success"},
                },
            )
            e = _paso_entrega(r.reg, r.fold(), vuelta.gh, ahora_iso())
            self.assertNotEqual(e.estado, "terminado")
            self.assertEqual(e.estrategia, "investigar")
            self.assertIn("head vigente del PR #", e.motivo)
            self.assertIn(sha_b[:12], e.motivo)
            self.assertIn(sha_a[:12], e.motivo)
            self.assertIn("concilia el head del PR", e.motivo)
            self.assertEqual(e.fase_actual.fallas_checks, ())


def _git(*args):
    subprocess.run(["git", *args], check=True, capture_output=True, text=True)


def _empujar_head(tmp, repo, rama, nombre="otro.txt"):
    """Empuja un commit externo a la rama del PR y devuelve el head nuevo."""
    clon = Path(tmp) / f"clon-{nombre.replace('.', '-')}"
    _git("clone", "--quiet", repo, str(clon))
    _git("-C", str(clon), "checkout", "--quiet", rama)
    (clon / nombre).write_text("otro\n")
    _git("-C", str(clon), "add", "-A")
    _git(
        "-C", str(clon),
        "-c", "user.name=t", "-c", "user.email=t@l",
        "commit", "--quiet", "-m", "push externo",
    )
    _git("-C", str(clon), "push", "--quiet", "origin", rama)
    return subprocess.run(
        ["git", "-C", str(clon), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _pasos(r, paso):
    return [
        ev for ev in r.reg.eventos(r.enc)
        if ev["kind"] == "entrega_externa" and ev["data"].get("paso") == paso
    ]


class LasRondasNoRevivenUnShaQueYaNoEsLaCabeza(unittest.TestCase):
    """F6: las rondas de reconciliación leen el sha bloqueado; si el head del
    PR ya es otro contenido, ese sha está muerto: un rerun en verde sobre él
    no se anota como checks_verdes ni abre el cierre. El head se concilia en
    el turno de entrega, que devuelve el trabajo nombrando ambos shas."""

    def test_el_rerun_verde_de_un_sha_que_ya_no_es_head_no_se_anota(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ck-ronda-head",
                _GhConCorridas,
                {"corridas": {"windows": "failure"}, "logs_job": {"windows": LOG_WINDOWS}},
            )
            e = vuelta("uno")
            self.assertEqual(e.fase_actual.fallas_checks, ("windows",))
            repo = str(e.entrega.repo)
            sha_a = e.producto.sha_git

            rama = vuelta.gh.ver(repo, e.pr_numero).rama
            sha_b = _empujar_head(tmp, repo, rama)
            _corridas_sha(
                vuelta.gh,
                repo,
                {
                    sha_a: {"windows": "success"},
                    sha_b: {"windows": "success"},
                },
            )
            with mock.patch.object(
                vuelta.gh, "ver", wraps=vuelta.gh.ver
            ) as ver_spy:
                e = ciclo._reconciliar_checks(
                    r.reg, r.fold(), vuelta.gh, r.espacio, ahora_iso(), 1000.0,
                )
                self.assertEqual(ver_spy.call_count, 1)
                e = ciclo._reconciliar_checks(
                    r.reg, r.fold(), vuelta.gh, r.espacio, ahora_iso(), 1001.0,
                )
                self.assertEqual(ver_spy.call_count, 1)
            self.assertEqual(_pasos(r, "checks_verdes"), [])
            self.assertEqual(e.fase_actual.fallas_checks, ("windows",))

            # En el turno de entrega, el head concilia: trabajo de vuelta
            # nombrando el head vigente y el sha aprobado.
            e = _paso_entrega(r.reg, r.fold(), vuelta.gh, ahora_iso())
            self.assertNotEqual(e.estado, "terminado")
            self.assertIn("head vigente del PR #", e.motivo)
            self.assertIn("concilia el head del PR", e.motivo)
            # F10: la desviación nombra los dos shas reales, nunca uno vacío.
            self.assertIn(sha_a[:12], e.motivo)
            self.assertIn(sha_b[:12], e.motivo)


class ElLogDelJobEnRojoEsElDelJob(unittest.TestCase):
    """La devolución trae el log literal del job exacto (h_a8968fb0): la cola
    agregada del run mezcla los jobs y puede borrar las líneas del que falló;
    el motivo no debe omitir el error de un job ni atribuirle el de otro."""

    def test_cada_job_en_rojo_trae_su_propio_log(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            cola_agregada = "\n".join(
                ["##[error]Proceso completado con código de salida 1."]
                + [f"BATERIA_FALLA {i}" for i in range(100)]
            )
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ck-log",
                _GhConCorridas,
                {
                    "corridas": {"windows": "failure", "bateria": "failure"},
                    "logs": {"40000": cola_agregada, "40001": cola_agregada},
                    "logs_job": {"windows": LOG_WINDOWS, "bateria": LOG_BATERIA},
                },
            )
            e = vuelta("uno")
            self.assertNotEqual(e.estado, "terminado")
            self.assertIn("checks requeridos en rojo: bateria, windows", e.motivo)
            por_job = {
                seccion.split(" (")[0]: seccion
                for seccion in e.motivo.split("log de ")[1:]
            }
            self.assertIn(LOG_WINDOWS, por_job["windows"])
            self.assertNotIn(LOG_BATERIA, por_job["windows"])
            self.assertIn(LOG_BATERIA, por_job["bateria"])
            self.assertNotIn(LOG_WINDOWS, por_job["bateria"])
            self.assertNotIn("BATERIA_FALLA", e.motivo)
            self.assertEqual(
                vuelta.gh.llamadas_log_job,
                [
                    (str(e.entrega.repo), "40000", "windows", "40000"),
                    (str(e.entrega.repo), "40001", "bateria", "40001"),
                ],
            )


class ElEstadoDelMismoNombreNoLoEscondeLaCorrida(unittest.TestCase):
    """Un check-run verde y un estado del mismo contexto se evalúan los dos:
    el peor manda (h_f05e0491, segunda ronda). La corrida verde no esconde al
    estado en rojo o pendiente del mismo nombre."""

    def test_corrida_verde_con_estado_en_rojo_no_termina(self):
        for estado in ("failure", "error"):
            with self.subTest(estado=estado), tempfile.TemporaryDirectory(
                ignore_cleanup_errors=True
            ) as tmp:
                vuelta, r = _entrega_pr(
                    self,
                    tmp,
                    f"ck-estado-{estado}",
                    _GhConCorridas,
                    {
                        "corridas": {"windows": "success"},
                        "estados": [{"context": "windows", "state": estado}],
                    },
                )
                e = vuelta("uno")
                self.assertNotEqual(e.estado, "terminado")
                self.assertEqual(e.estrategia, "investigar")
                self.assertIn(
                    "checks requeridos en rojo: windows", e.motivo
                )
                self.assertEqual(e.fase_actual.fallas_checks, ("windows",))

    def test_corrida_verde_con_estado_pendiente_espera(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ck-estado-pendiente",
                _GhConCorridas,
                {
                    "corridas": {"windows": "success"},
                    "estados": [{"context": "windows", "state": "pending"}],
                },
            )
            e = vuelta("uno")
            self.assertNotEqual(e.estado, "terminado")
            self.assertIn("esperando checks requeridos del PR #", e.motivo)
            self.assertEqual(
                e.fase_actual.checks_esperando_sha, e.producto.sha_git
            )

    def test_corrida_verde_con_estado_verde_termina(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ck-estado-verde",
                _GhConCorridas,
                {
                    "corridas": {"windows": "success"},
                    "estados": [{"context": "windows", "state": "success"}],
                },
            )
            e = vuelta("uno")
            self.assertEqual(e.estado, "terminado")


class ElLogDelJobConservaElFallo(unittest.TestCase):
    """El literal completo del log del job queda en el espacio del responsable
    (.interno/logs, que no viaja en el producto) y la devolución lo
    referencia: un fallo puede vivir en cualquier parte del log y no depende
    del recorte del motivo (h_a8968fb0)."""

    def _log_con_fallo_en(self, linea: int) -> str:
        lineas = [f"preparacion {i}" for i in range(100)]
        lineas.insert(linea, "WINDOWS_FAILURE_DETAIL: AssertionError en tests/test_director_capacidades.py")
        del lineas[101:]
        return "\n".join(lineas)

    def test_el_error_de_la_cabeza_del_log_llega_al_motivo(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            log_largo = "\n".join(
                [LOG_WINDOWS] + [f"limpieza {i}" for i in range(100)]
            )
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ck-log-cabeza",
                _GhConCorridas,
                {
                    "corridas": {"windows": "failure"},
                    "logs_job": {"windows": log_largo},
                },
            )
            e = vuelta("uno")
            self.assertNotEqual(e.estado, "terminado")
            self.assertIn(LOG_WINDOWS, e.motivo)
            self.assertIn("se omiten 22 líneas intermedias", e.motivo)
            self.assertIn(
                "copia literal: .interno/logs/windows-job40000-run40000.log", e.motivo
            )
            copia = r.espacio / ".interno" / "logs" / "windows-job40000-run40000.log"
            self.assertTrue(copia.is_file())
            self.assertEqual(len(copia.read_text().splitlines()), 101)
            self.assertIn(LOG_WINDOWS, copia.read_text())

    def test_el_error_en_medio_del_log_queda_en_la_copia(self):
        """La cronología de la revisión: 60 líneas de setup, el fallo en la
        línea 61 y 40 de limpieza. El extracto puede omitirlo; la copia no."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            log_medio = self._log_con_fallo_en(60)
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ck-log-medio",
                _GhConCorridas,
                {
                    "corridas": {"windows": "failure"},
                    "logs_job": {"windows": log_medio},
                },
            )
            e = vuelta("uno")
            self.assertNotEqual(e.estado, "terminado")
            fallo = "WINDOWS_FAILURE_DETAIL: AssertionError"
            self.assertIn("copia literal: .interno/logs/windows-job40000-run40000.log", e.motivo)
            self.assertIn("queda en .interno/logs/windows-job40000-run40000.log", e.motivo)
            copia = r.espacio / ".interno" / "logs" / "windows-job40000-run40000.log"
            self.assertTrue(copia.is_file())
            literal = copia.read_text()
            self.assertEqual(len(literal.splitlines()), 101)
            self.assertIn(fallo, literal)
            self.assertNotIn(fallo, e.motivo)


class LaCopiaDelLogNoSigueEnlaces(unittest.TestCase):
    """La escritura de la copia no sigue enlaces: un `.log.tmp` del espacio
    que apunte a fuera no trunca nada externo, la copia queda como archivo
    regular dentro del espacio y un `.interno` enlazado se rechaza
    (h_ff7d6d39)."""

    def test_un_enlace_preexistente_en_el_temporal_no_escribe_fuera(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ck-enlace",
                _GhConCorridas,
                {
                    "corridas": {"windows": "failure"},
                    "logs_job": {"windows": LOG_WINDOWS},
                },
            )
            centinela = Path(tmp) / "host-file.txt"
            centinela.write_text("CONTENIDO_HOST")
            logs = r.espacio / ".interno" / "logs"
            logs.mkdir(parents=True)
            temporal = logs / "windows-job40000-run40000.log.tmp"
            os.symlink(centinela, temporal)

            e = vuelta("uno")

            self.assertNotEqual(e.estado, "terminado")
            self.assertIn("checks requeridos en rojo: windows", e.motivo)
            self.assertEqual(centinela.read_text(), "CONTENIDO_HOST")
            copia = logs / "windows-job40000-run40000.log"
            self.assertFalse(copia.is_symlink())
            self.assertTrue(copia.is_file())
            self.assertEqual(copia.read_text(), LOG_WINDOWS)
            self.assertIn(
                "copia literal: .interno/logs/windows-job40000-run40000.log",
                e.motivo,
            )

    def test_un_interno_enlazado_no_copia_nada(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ck-interno-enlace",
                _GhConCorridas,
                {
                    "corridas": {"windows": "failure"},
                    "logs_job": {"windows": LOG_WINDOWS},
                },
            )
            fuera = Path(tmp) / "dir-host"
            fuera.mkdir()
            interno = r.espacio / ".interno"
            interno.mkdir(parents=True, exist_ok=True)
            os.symlink(fuera, interno / "logs")

            e = vuelta("uno")

            self.assertNotEqual(e.estado, "terminado")
            self.assertIn("checks requeridos en rojo: windows", e.motivo)
            self.assertIn(LOG_WINDOWS, e.motivo)
            self.assertEqual(list(fuera.iterdir()), [])
            self.assertNotIn("copia literal:", e.motivo)


class DosJobsConElMismoSaneoNoSePisan(unittest.TestCase):
    """Dos jobs distintos de una misma corrida pueden saneo al mismo nombre:
    la identidad de la copia lleva el ID del job, así cada job conserva su
    propio log y el motivo referencia la copia correcta (h_c4902b32)."""

    def test_dos_jobs_de_una_corrida_conservan_sus_propios_logs(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            def log_con_fallo_en_medio(marca):
                lineas = [f"preparacion {i}" for i in range(100)]
                lineas.insert(60, marca)
                del lineas[101:]
                return "\n".join(lineas)

            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ck-colision",
                _GhConCorridas,
                {
                    "corridas": {
                        "tests (a/b)": {"estado": "failure", "run": 7, "id": 100001},
                        "tests (a-b)": {"estado": "failure", "run": 7, "id": 100002},
                    },
                    "logs_job": {
                        "tests (a/b)": log_con_fallo_en_medio("FALLO_DEL_JOB_1"),
                        "tests (a-b)": log_con_fallo_en_medio("FALLO_DEL_JOB_2"),
                    },
                },
            )
            e = vuelta("uno")

            self.assertNotEqual(e.estado, "terminado")
            self.assertIn(
                "checks requeridos en rojo: tests (a-b), tests (a/b)", e.motivo
            )
            self.assertIn("tests--a-b-job100001-run7.log", e.motivo)
            self.assertIn("tests--a-b-job100002-run7.log", e.motivo)
            copia1 = r.espacio / ".interno" / "logs" / "tests--a-b-job100001-run7.log"
            copia2 = r.espacio / ".interno" / "logs" / "tests--a-b-job100002-run7.log"
            self.assertIn("FALLO_DEL_JOB_1", copia1.read_text())
            self.assertIn("FALLO_DEL_JOB_2", copia2.read_text())


def _romper_push(espacio: Path) -> None:
    """F10: deja el remoto sin pushurl util y el resto intacto: el fetch y la
    lectura del head (ls-remote del bare) siguen funcionando, solo el push
    falla, con la rama del remoto en su sitio."""
    subprocess.run(
        ["git", "-C", str(espacio), "remote", "set-url",
         "--push", "origin", "/no/existe/como/remoto"],
        check=True, capture_output=True, text=True,
    )


class UnFalloDePushCualquieraNoEsCabezaDesviada(unittest.TestCase):
    """F10: un fallo de push cualquiera no es una cabeza desviada. Con la rama
    del remoto en su sitio (la punta coincide con el sha que se intenta
    publicar), el fallo cae en la espera generica del instrumento, sin
    inventar un desvio ni nombrar un sha vacio; y la desviacion real de la
    ronda de F6 nombra los dos shas, nunca uno vacio."""

    def test_un_push_rechazado_con_la_rama_en_su_sitio_espera_sin_desvio(self):
        """Push rechazado con punta == sha: espera generica, cero head_vigente.
        Con el DeepSeek en desacuerdo, el turno de entrega toma el camino largo
        y llega de verdad al push rechazado."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            class _GhConCorridasReceloso(_GhConCorridas):
                titulos = ("una observacion",)

            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ck-push-rechazo",
                _GhConCorridasReceloso,
                {"corridas": {"windows": "success"}},
            )
            e = vuelta("uno")
            self.assertNotEqual(e.estado, "terminado")
            repo = str(e.entrega.repo)
            sha_a = e.producto.sha_git
            self.assertEqual(vuelta.gh.ver(repo, e.pr_numero).head, sha_a)
            _romper_push(r.espacio)

            e = _paso_entrega(r.reg, r.fold(), vuelta.gh, ahora_iso())

            self.assertNotEqual(e.estado, "terminado")
            self.assertEqual(_pasos(r, "head_vigente"), [])
            self.assertNotIn("head vigente del PR #", e.motivo)
            self.assertIn("git push", e.motivo)

    def test_un_push_fallido_sin_sha_espera_sin_inventar_desvio(self):
        """Sin sha entregado en mano (remoto inalcanzable, credenciales, un
        enganche que niega el push) tampoco hay desvio que declarar."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ck-push-sin-sha",
                _GhConCorridas,
                {
                    "corridas": {"windows": "failure"},
                    "logs_job": {"windows": LOG_WINDOWS},
                },
            )
            e = vuelta("uno")
            self.assertEqual(e.fase_actual.fallas_checks, ("windows",))
            repo = str(e.entrega.repo)
            punta = vuelta.gh.ver(repo, e.pr_numero).head
            self.assertEqual(punta, e.pr_sha)

            with mock.patch.object(
                ciclo,
                "_commit_push",
                return_value=("", "git push fallo: sin credenciales del remoto"),
            ):
                e = _paso_entrega(r.reg, r.fold(), vuelta.gh, ahora_iso())

            self.assertNotEqual(e.estado, "terminado")
            self.assertEqual(_pasos(r, "head_vigente"), [])
            self.assertNotIn("head vigente del PR #", e.motivo)
            self.assertIn("git push", e.motivo)

    def test_un_push_fallido_de_un_commit_mas_nuevo_no_es_desvio(self):
        """El push propio de un sha local mas nuevo no aterriza y la punta del
        remoto sigue en el sha entregado: reintento generico, no desviacion.
        Comparar contra el sha local declaraba desvios falsos (F10)."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ck-push-atrasado",
                _GhConCorridas,
                {
                    "corridas": {"windows": "failure"},
                    "logs_job": {"windows": LOG_WINDOWS},
                },
            )
            e = vuelta("uno")
            self.assertEqual(e.fase_actual.fallas_checks, ("windows",))
            repo = str(e.entrega.repo)
            entregado = e.pr_sha
            self.assertEqual(vuelta.gh.ver(repo, e.pr_numero).head, entregado)

            with mock.patch.object(
                ciclo,
                "_commit_push",
                return_value=("b" * 40, "git push fallo: red inestable"),
            ):
                e = _paso_entrega(r.reg, r.fold(), vuelta.gh, ahora_iso())

            self.assertNotEqual(e.estado, "terminado")
            self.assertEqual(_pasos(r, "head_vigente"), [])
            self.assertNotIn("head vigente del PR #", e.motivo)
            self.assertIn("git push", e.motivo)


class CanceladoOActionRequiredNoFrenanEnRojo(unittest.TestCase):
    """F2: un check con conclusion cancelled, action_required o stale no es un
    check en rojo ni un check verde; queda en `faltan` (el PR espera sin
    devolver el trabajo en rojo ni abrir el cierre), mientras que failure,
    timed_out y startup_failure siguen frenando como rojo."""

    def test_cancelled_y_action_required_no_bloquean_en_rojo(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ck-cancelado",
                _GhConCorridas,
                {
                    "requeridos": ["windows", "aprobacion"],
                    "corridas": {
                        "windows": "success",
                        "previa": "cancelled",
                        "aprobacion": "action_required",
                        "vieja": "stale",
                    },
                },
            )
            e = vuelta("uno")
            self.assertNotEqual(e.estado, "terminado")
            self.assertEqual(e.fase_actual.fallas_checks, ())
            self.assertEqual(e.fase_actual.entrega_bloqueada_sha, "")
            self.assertNotIn("checks requeridos en rojo", e.motivo)
            self.assertIn("esperando checks requeridos del PR #", e.motivo)
            self.assertIn("aprobacion, previa, vieja", e.motivo)

    def test_requerido_en_cancelled_action_required_o_stale_queda_en_faltan(self):
        for conclusion in ("cancelled", "action_required", "stale"):
            with self.subTest(conclusion=conclusion):
                v = veredicto(
                    "a" * 40,
                    ("windows",),
                    [
                        {
                            "name": "windows",
                            "status": "completed",
                            "conclusion": conclusion,
                            "completed_at": "2026-11-02T21:46:00Z",
                            "id": 40000,
                            "html_url": "https://github.com/o/r/actions/runs/40000/job/40000",
                            "details_url": "https://github.com/o/r/actions/runs/40000/job/40000",
                        }
                    ],
                    "",
                )
                self.assertFalse(v.terminal)
                self.assertFalse(v.green)
                self.assertEqual(v.fallas, ())
                self.assertEqual(v.corridas_fallidas, ())
                self.assertEqual(v.faltan, ("windows",))
                if conclusion == "action_required":
                    self.assertFalse(v.github_termino)
                self.assertEqual(v.corridas[0]["run_id"], "")

    def test_cancelled_action_required_y_stale_no_disparan_relanza_saltada_ni_abortan_tick(self):
        import time
        from types import SimpleNamespace
        from motor import machine as m
        from motor.effects import Effects
        from motor.plan import parse_plan
        from motor.store import Store
        from tests.test_github_despliegue import GhGrabado, SHA, _run
        from tests.test_puro import PLAN, pol

        for conclusion in ("cancelled", "action_required", "stale"):
            with self.subTest(conclusion=conclusion), tempfile.TemporaryDirectory(
                ignore_cleanup_errors=True
            ) as tmp:
                gh = GhGrabado(
                    proteccion=json.dumps(["ci"]),
                    runs=json.dumps([_run("ci", conclusion=conclusion, rid=42)]),
                )
                store = Store(Path(tmp) / "motor.db")
                plan = parse_plan(PLAN)
                fx = Effects(
                    SimpleNamespace(store=store, gh=gh, checks_cada_s=0.0, policy=pol())
                )
                store.append(
                    "demo",
                    "A",
                    "ChecksEsperando",
                    {"sha": SHA, "faltan": ["ci"], "github_termino": True},
                    ts=time.time() - 3600,
                )
                snap = m.fold(plan, store.events(plan.id))
                snap.blocks["A"].pr = {"numero": 7}
                fx.poll_checks(plan, snap, m.PollChecks("A", SHA))
                self.assertEqual(gh.relanzadas, [])
                self.assertEqual(
                    [e for e in store.events("demo") if e.kind == "ChecksRelanzados"],
                    [],
                )

        class _GhRerunFallaUnaVez(GhGrabado):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self.fallar_rerun = True

            def _gh(self, *a):
                if a[0] == "api" and "/events" in a[1]:
                    return "2026-10-21T21:00:00Z"
                if a[:2] == ("run", "rerun") and self.fallar_rerun:
                    raise RuntimeError("gh: fallo transitorio o check externo")
                return super()._gh(*a)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            externo = {
                "name": "ci",
                "status": "completed",
                "conclusion": "skipped",
                "completed_at": "2026-10-21T19:00:00Z",
                "id": 99999,
                "html_url": "https://github.com/o/r/runs/99999",
                "details_url": "https://external.example/checks/99999",
            }
            gh = _GhRerunFallaUnaVez(
                proteccion=json.dumps(["ci"]),
                runs=json.dumps([externo]),
            )
            store = Store(Path(tmp) / "motor.db")
            plan = parse_plan(PLAN)
            fx = Effects(
                SimpleNamespace(store=store, gh=gh, checks_cada_s=0.0, policy=pol())
            )
            store.append(
                "demo",
                "A",
                "ChecksEsperando",
                {"sha": SHA, "faltan": ["ci"], "github_termino": True},
                ts=time.time() - 3600,
            )
            snap = m.fold(plan, store.events(plan.id))
            snap.blocks["A"].pr = {"numero": 7}
            fx.poll_checks(plan, snap, m.PollChecks("A", SHA))
            self.assertEqual(gh.relanzadas, [])
            self.assertEqual(
                [e for e in store.events("demo") if e.kind == "ChecksRelanzados"],
                [],
            )

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            salto = _run("ci", conclusion="skipped", rid=42)
            salto["completed_at"] = "2026-10-21T19:00:00Z"
            gh = _GhRerunFallaUnaVez(
                proteccion=json.dumps(["ci"]),
                runs=json.dumps([salto]),
            )
            store = Store(Path(tmp) / "motor.db")
            plan = parse_plan(PLAN)
            fx = Effects(
                SimpleNamespace(store=store, gh=gh, checks_cada_s=0.0, policy=pol())
            )
            store.append(
                "demo",
                "A",
                "ChecksEsperando",
                {"sha": SHA, "faltan": ["ci"], "github_termino": True},
                ts=time.time() - 3600,
            )
            snap = m.fold(plan, store.events(plan.id))
            snap.blocks["A"].pr = {"numero": 7}
            with self.assertRaises(RuntimeError):
                fx.poll_checks(plan, snap, m.PollChecks("A", SHA))
            self.assertEqual(
                [e for e in store.events("demo") if e.kind == "ChecksRelanzados"],
                [],
            )
            gh.fallar_rerun = False
            fx.poll_checks(plan, snap, m.PollChecks("A", SHA))
            self.assertEqual(gh.relanzadas, ["42"])
            self.assertEqual(
                len([e for e in store.events("demo") if e.kind == "ChecksRelanzados"]),
                1,
            )

        class _GhRerunRechazaDuplicado(GhGrabado):
            def _gh(self, *a):
                if a[0] == "api" and "/events" in a[1]:
                    return "2026-10-21T21:00:00Z"
                if a[:2] == ("run", "rerun"):
                    if a[2] == "99":
                        raise RuntimeError(
                            "gh: run 99 cannot be rerun; its workflow file may be broken"
                        )
                    lista = (
                        self.relanzadas_fallidas if "--failed" in a else self.relanzadas
                    )
                    if a[2] in lista:
                        raise RuntimeError(
                            f"gh: run {a[2]} cannot be rerun; already running"
                        )
                return super()._gh(*a)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            salto_ci = _run("ci", conclusion="skipped", rid=42)
            salto_ci["completed_at"] = "2026-10-21T19:00:00Z"
            salto_lint = _run("lint", conclusion="skipped", rid=42)
            salto_lint["id"] = 100043
            salto_lint["completed_at"] = "2026-10-21T19:00:00Z"
            gh = _GhRerunRechazaDuplicado(
                proteccion=json.dumps(["ci", "lint"]),
                runs=json.dumps([salto_ci, salto_lint]),
            )
            store = Store(Path(tmp) / "motor.db")
            plan = parse_plan(PLAN)
            fx = Effects(
                SimpleNamespace(store=store, gh=gh, checks_cada_s=0.0, policy=pol())
            )
            store.append(
                "demo",
                "A",
                "ChecksEsperando",
                {"sha": SHA, "faltan": ["ci", "lint"], "github_termino": True},
                ts=time.time() - 3600,
            )
            snap = m.fold(plan, store.events(plan.id))
            snap.blocks["A"].pr = {"numero": 7}
            fx.poll_checks(plan, snap, m.PollChecks("A", SHA))
            self.assertEqual(gh.relanzadas, ["42"])
            gh.relanzar_corrida("o/r", "42")
            self.assertEqual(gh.relanzadas, ["42"])
            with self.assertRaises(RuntimeError):
                gh.relanzar_corrida("o/r", "99")
            relanzados = [
                e for e in store.events("demo") if e.kind == "ChecksRelanzados"
            ]
            self.assertEqual(len(relanzados), 1)
            self.assertEqual(relanzados[0].data["run_ids"], ["42"])

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            roja_ci = _run("ci", conclusion="failure", rid=7)
            roja_lint = _run("lint", conclusion="failure", rid=7)
            roja_lint["id"] = 100008
            gh = _GhRerunRechazaDuplicado(
                proteccion=json.dumps(["ci", "lint"]),
                runs=json.dumps([roja_ci, roja_lint]),
            )
            store = Store(Path(tmp) / "motor.db")
            plan = parse_plan(PLAN)
            fx = Effects(
                SimpleNamespace(store=store, gh=gh, checks_cada_s=0.0, policy=pol())
            )
            t0 = time.time() - 100
            store.append(
                "demo",
                "A",
                "AttemptStarted",
                {"n": 1, "role": "implementar", "modelo": "glm", "proc": {}, "dir": "d"},
                ts=t0,
            )
            store.append(
                "demo",
                "A",
                "AttemptEnded",
                {"n": 1, "role": "implementar", "outcome": {"kind": "entregado", "sha": SHA}},
                ts=t0 + 1,
            )
            store.append(
                "demo",
                "A",
                "ChecksResult",
                {"sha": SHA, "green": False, "detalle": "fallo"},
                ts=t0 + 10,
            )
            store.append(
                "demo",
                "A",
                "AttemptStarted",
                {"n": 2, "role": "implementar", "modelo": "glm", "proc": {}, "dir": "d"},
                ts=t0 + 20,
            )
            store.append(
                "demo",
                "A",
                "AttemptEnded",
                {"n": 2, "role": "implementar", "outcome": {"kind": "entregado", "sha": SHA}},
                ts=t0 + 21,
            )
            snap = m.fold(plan, store.events(plan.id))
            snap.blocks["A"].pr = {"numero": 7}
            fx.poll_checks(plan, snap, m.PollChecks("A", SHA))
            self.assertEqual(gh.relanzadas_fallidas, ["7"])
            relanzados_fallidas = [
                e
                for e in store.events("demo")
                if e.kind == "ChecksRelanzados" and e.data.get("motivo") == "fallidas"
            ]
            self.assertEqual(len(relanzados_fallidas), 1)

    def test_timed_out_y_startup_failure_siguen_siendo_rojos(self):
        for conclusion in ("timed_out", "startup_failure"):
            with self.subTest(conclusion=conclusion):
                v = veredicto(
                    "a" * 40,
                    (),
                    [
                        {
                            "name": "windows",
                            "status": "completed",
                            "conclusion": conclusion,
                            "completed_at": "2026-11-02T21:46:00Z",
                            "id": 40000,
                            "html_url": "https://github.com/o/r/actions/runs/40000/job/40000",
                            "details_url": "https://github.com/o/r/actions/runs/40000/job/40000",
                        }
                    ],
                    "",
                )
                self.assertTrue(v.terminal)
                self.assertFalse(v.green)
                self.assertEqual(v.fallas, ("windows",))
                self.assertEqual(len(v.corridas_fallidas), 1)


class SondaEntornoSinAtajoDeAgente(unittest.TestCase):
    """#321 F3: _verificar_impedimento de entorno consulta la sonda aunque el
    agente anotado ya no este entre los candidatos del rol."""

    def test_un_agente_fuera_de_candidatas_no_levanta_el_impedimento_sin_sonda(self):
        from motor.encargos.registro import Registro

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp) / "base"
            reg = Registro(base)
            try:
                hora = ahora_iso()
                enc_id = ciclo.recibir(
                    reg,
                    "pedido",
                    "f3-sonda",
                    hora,
                    modelos_solicitados=(("responsable", "opencode", "glm-5"),),
                )["encargo"]
                espacio = base / "espacio" / enc_id
                espacio.mkdir(parents=True, exist_ok=True)
                ciclo._anotar_impedimento(
                    reg,
                    reg.fold(enc_id),
                    "entorno",
                    "falla del entorno",
                    "que el entorno funcione",
                    hora,
                    "agente-retirado",
                )
                enc = reg.fold(enc_id)
                self.assertEqual(enc.impedimento, "entorno")
                espacio_roto = Path(tmp) / "fuera"
                self.assertFalse(
                    ciclo._verificar_impedimento(
                        "entorno", enc, espacio_roto, base, reg
                    )
                )
                self.assertTrue(
                    ciclo._verificar_impedimento(
                        "entorno", enc, espacio, base, reg
                    )
                )
            finally:
                reg.close()


if __name__ == "__main__":
    unittest.main()
