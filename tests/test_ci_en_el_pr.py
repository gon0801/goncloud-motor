"""El CI deja de ser compuerta: la entrega `pr` termina con la aprobación y el
CI corre en el PR real. Se lee cuando termina; un rojo de prueba vuelve al
responsable, uno de tubería se relanza una sola vez y solo avisa si es fijo,
uno incierto recomienda una revisión, y un pendiente o verde no detiene nada.
El aviso es para alguien no técnico: dice qué falló, de quién es y qué hacer."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace

from motor.encargos import ciclo
from motor.encargos.ciclo import ahora_iso
from motor.encargos.dominio import (
    TransicionIlegal,
    con_fase,
    prompt_responsable,
    reducir,
)
from motor.integraciones import directores
from tests.test_flujo_revisiones import (
    _entrega_pr,
    sonda_sin_aislamiento_del_host,
)

from tests.dobles import github_con_corridas


LOG_BATERIA = "ERROR: 3 failed, 0 passed in tests/test_bateria.py"
LOG_GATE = "leaks found: 13\nfinding: generic-api-key en docs/viejo.md"
LOG_INCIERTO = "fallo ambiguo sin diagnóstico"

# Formato real de `gh run view --job <id> --log`: job, paso y línea por tabuladores.
LOG_GATE_PRUEBA_ROJA = "\n".join(
    f"gate\t{paso}\t{linea}"
    for paso, linea in (
        ("Set up job", "Current runner version: '2.329.0'"),
        ("Set up job", "Secret source: Actions"),
        ("Gitleaks", "##[group]Run v=8.30.1"),
        ("Gitleaks", 't="gitleaks_${v}_linux_x64.tar.gz"'),
        ("Gitleaks", "INF no leaks found"),
        ("Pruebas (una vez)", "FAILED tests/test_schema_docs.py::test_conteo - assert 87 == 88"),
        ("Pruebas (una vez)", "1 failed, 5060 passed, 1 warning in 718.16s (0:11:58)"),
        ("Pruebas (una vez)", "##[error]Process completed with exit code 1."),
        ("Stop containers", "CHECK (estado IN ('planeado', 'applied', 'failed'))"),
    )
)
LOG_BATERIA_CON_SECRETOS = "\n".join(
    f"bateria-parte-1\t{paso}\t{linea}"
    for paso, linea in (
        ("Set up job", "Secret source: Actions"),
        ("Parte de la bateria", "test_secretos_con_permisos_abiertos_espera_y_sale ... ok"),
        ("Parte de la bateria", "test_un_agente_no_ve_la_variable (tests.test_senal.SecretoTest) ... ok"),
        ("Parte de la bateria", "FAIL: test_el_juicio_caido_tres_veces_tambien_se_omite"),
        ("Parte de la bateria", '  File "/home/runner/work/goncloud-motor/goncloud-motor/tests/test_motor_sin_personas.py", line 148'),
        ("Parte de la bateria", "AssertionError: True is not false"),
        ("Parte de la bateria", "FAILED (failures=2, skipped=1)"),
        ("Parte de la bateria", "##[error]Process completed with exit code 1."),
    )
)
# Más largo que el recorte del log (_TOPE_LOG_JOB): el hallazgo de Gitleaks queda
# en el tramo que el recorte omite.
LOG_GATE_LARGO_CON_HALLAZGOS = "\n".join(
    [f"gate\tSet up job\tlinea de preparacion {i}" for i in range(120)]
    + [
        "gate\tGitleaks\tFinding:     REDACTED",
        "gate\tGitleaks\tRuleID:      generic-api-key",
        "gate\tGitleaks\tWRN leaks found: 3",
        "gate\tGitleaks\t##[error]Process completed with exit code 1.",
    ]
    + [f"gate\tPost Run actions/checkout@v7\tlimpieza {i}" for i in range(60)]
)


def _log_paso(paso: str, *lineas: str) -> str:
    return "\n".join(
        [f"gate\tSet up job\tSecret source: Actions"]
        + [f"gate\t{paso}\t{linea}" for linea in lineas]
        + [f"gate\t{paso}\t##[error]Process completed with exit code 1."]
    )


LOG_GATE_CON_HALLAZGOS = "\n".join(
    f"gate\t{paso}\t{linea}"
    for paso, linea in (
        ("Set up job", "Secret source: Actions"),
        ("Gitleaks", "Finding:     REDACTED"),
        ("Gitleaks", "RuleID:      generic-api-key"),
        ("Gitleaks", "WRN leaks found: 3"),
        ("Gitleaks", "##[error]Process completed with exit code 1."),
    )
)

_INICIO = time.time() + 3600
_VACIO = "2026-11-03T21:46:00Z"


def _hora(paso: int = 0) -> str:
    return ahora_iso(_INICIO + 61 * paso)


def setUpModule():
    sonda_sin_aislamiento_del_host()


def _segunda_corrida(vuelta, repo: str, corridas: dict, logs: dict | None = None):
    ruta = vuelta.gh.d / "repos.json"
    cfg = json.loads(ruta.read_text())
    entrada = cfg[str(repo)]
    entrada["corridas_tras_reintento"] = corridas
    if logs is not None:
        entrada["logs_job"] = logs
    ruta.write_text(json.dumps(cfg))


def _reintento_empezado(vuelta, repo: str, empezado: bool) -> None:
    ruta = vuelta.gh.d / "repos.json"
    cfg = json.loads(ruta.read_text())
    cfg[str(repo)]["reintento_no_empezado"] = not empezado
    ruta.write_text(json.dumps(cfg))


def _avisos(e):
    return [h for h in directores.hechos_de_avance(e) if h["tipo"] == "ci_rojo"]


def _estados_ci(r):
    return [
        ev["data"]["estado"]
        for ev in r.reg.eventos(r.enc)
        if ev["kind"] == "ci_pr"
    ]


def _sondeos(r):
    return [
        ev for ev in r.reg.eventos(r.enc) if ev["kind"] == "ci_pr_sondeo"
    ]


class EntregaSinCiAntesDelPr(unittest.TestCase):
    def test_termina_con_la_aprobacion_sin_pedir_ci(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "ci-pr-1", github_con_corridas)
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
                github_con_corridas,
                {"corridas": {"bateria": "failure"}, "logs_job": {"bateria": LOG_BATERIA}},
            )
            e = vuelta("hola\n")
            self.assertEqual(e.estado, "terminado")
            numero = e.pr_numero

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora())
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

            e2 = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(1))
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
                github_con_corridas,
                {"corridas": {"bateria": "failure"}, "logs_job": {"bateria": LOG_BATERIA}},
            )
            e = vuelta("hola\n")
            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora())
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


class FoldDeEventosRetirados(unittest.TestCase):
    def test_un_registro_viejo_con_ci_producto_o_ci_revision_rojo_sigue_plegando(self):  # no-roja-en-base: solo cambió el doble de GitHub; el comportamiento probado ya estaba en la base
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "ci-pr-fold", github_con_corridas)
            e = vuelta("hola\n")
            for kind, data, ukey in (
                ("ci_producto", {"version": "vieja", "fallas": []}, "legado-ci-producto"),
                ("ci_revision_rojo", {"job": "bateria"}, "legado-ci-revision-rojo"),
            ):
                r.reg.db.execute(
                    "INSERT INTO events (ts, encargo, kind, data, ukey) "
                    "VALUES (?,?,?,?,?)",
                    (_hora(), r.enc, kind, json.dumps(data), ukey),
                )
            plegado = r.reg.fold(r.enc)
            self.assertEqual(plegado.estado, e.estado)
            self.assertEqual(plegado.nivel_alcanzado, e.nivel_alcanzado)


class RojoDeTuberiaSeRelanzaUnaVez(unittest.TestCase):
    def test_pasajero_pasa_en_la_segunda_y_no_avisa(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-3",
                github_con_corridas,
                {"corridas": {"gate": "failure"}, "logs_job": {"gate": LOG_GATE}},
            )
            e = vuelta("hola\n")
            repo = e.entrega.repo

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora())
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.fase_actual.ci_pr_estado, "reintentando")
            self.assertEqual(len(vuelta.gh.relanzadas), 1)
            self.assertEqual(_avisos(e), [])

            _segunda_corrida(vuelta, repo, {"gate": "success"})
            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(1))
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.fase_actual.ci_pr_estado, "verde")
            self.assertEqual(_avisos(e), [])
            self.assertEqual(len(vuelta.gh.relanzadas), 1)
            self.assertEqual(_estados_ci(r), ["reintentando", "verde"])

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(2))
            self.assertEqual(len(vuelta.gh.relanzadas), 1)
            self.assertEqual(_estados_ci(r), ["reintentando", "verde"])

    def test_permanente_falla_otra_vez_y_avisa_en_llano(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-4",
                github_con_corridas,
                {"corridas": {"gate": "failure"}, "logs_job": {"gate": LOG_GATE}},
            )
            e = vuelta("hola\n")
            repo = e.entrega.repo

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora())
            self.assertEqual(e.fase_actual.ci_pr_estado, "reintentando")
            self.assertEqual(len(vuelta.gh.relanzadas), 1)

            _segunda_corrida(vuelta, repo, {"gate": "failure"}, {"gate": LOG_GATE})
            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(1))
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

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(2))
            self.assertEqual(len(vuelta.gh.relanzadas), 1)
            self.assertEqual(_estados_ci(r), ["reintentando", "rojo"])
            self.assertEqual(len(_avisos(e)), 1)

    def test_con_un_log_largo_el_aviso_conserva_la_causa_de_secretos(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            logs = {"gate": LOG_GATE_LARGO_CON_HALLAZGOS}
            vuelta, r = _entrega_pr(
                self, tmp, "ci-pr-largo", github_con_corridas,
                {"corridas": {"gate": "failure"}, "logs_job": logs},
            )
            e = vuelta("hola\n")
            repo = e.entrega.repo
            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora())
            _segunda_corrida(vuelta, repo, {"gate": "failure"}, logs)
            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(1))
            self.assertEqual(e.fase_actual.ci_pr_clase, "tuberia")
            avisos = _avisos(e)
            self.assertEqual(len(avisos), 1)
            self.assertIn("control de secretos", avisos[0]["texto"])

    def test_no_se_declara_permanente_antes_de_que_empiece_el_rerun(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-rerun",
                github_con_corridas,
                {"corridas": {"gate": "failure"}, "logs_job": {"gate": LOG_GATE}},
            )
            e = vuelta("hola\n")
            repo = e.entrega.repo

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora())
            self.assertEqual(e.fase_actual.ci_pr_estado, "reintentando")
            self.assertEqual(len(vuelta.gh.relanzadas), 1)

            _segunda_corrida(vuelta, repo, {"gate": "failure"}, {"gate": LOG_GATE})
            _reintento_empezado(vuelta, repo, False)
            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(1))
            self.assertEqual(e.fase_actual.ci_pr_estado, "reintentando")
            self.assertEqual(_avisos(e), [])
            self.assertEqual(_estados_ci(r), ["reintentando"])

            _reintento_empezado(vuelta, repo, True)
            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(2))
            self.assertEqual(e.fase_actual.ci_pr_estado, "rojo")
            self.assertEqual(e.fase_actual.ci_pr_reintento, "permanente")
            self.assertEqual(len(vuelta.gh.relanzadas), 1)


class ElReintentoSinEmpezarNoSondeaCadaBarrido(unittest.TestCase):
    def test_un_error_de_lectura_al_vencer_concede_una_lectura_mas(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-error-limite",
                github_con_corridas,
                {"corridas": {"bateria": "in_progress"}},
            )
            e = vuelta("hola\n")
            repo = e.entrega.repo
            vuelta.gh.checks_llamadas = 0

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora())
            self.assertEqual(_estados_ci(r), [])
            vuelta.gh.fallar_checks(repo, 2)

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(90))
            self.assertEqual(_estados_ci(r), [])
            self.assertEqual(vuelta.gh.checks_llamadas, 2)

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(91))
            self.assertEqual(_estados_ci(r), ["sin_resultado"])
            self.assertEqual(vuelta.gh.checks_llamadas, 3)

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(92))
            self.assertEqual(vuelta.gh.checks_llamadas, 3)
            self.assertEqual(_estados_ci(r), ["sin_resultado"])

    def test_el_resultado_sobrevive_a_un_error_de_lectura_en_el_limite(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-error-limite-verde",
                github_con_corridas,
                {"corridas": {"bateria": "in_progress"}},
            )
            e = vuelta("hola\n")
            repo = e.entrega.repo
            vuelta.gh.checks_llamadas = 0

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora())
            self.assertEqual(_estados_ci(r), [])
            vuelta.gh.fallar_checks(repo, 1)

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(90))
            self.assertEqual(_estados_ci(r), [])
            self.assertEqual(vuelta.gh.checks_llamadas, 2)

            ruta = vuelta.gh.d / "repos.json"
            cfg = json.loads(ruta.read_text())
            cfg[str(repo)]["corridas_sha"] = {e.pr_sha: {"bateria": "success"}}
            ruta.write_text(json.dumps(cfg))
            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(91))
            self.assertEqual(_estados_ci(r), ["verde"])
            self.assertEqual(vuelta.gh.checks_llamadas, 3)

    def test_al_vencer_el_plazo_termina_en_sin_resultado(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-rerun-tarde",
                github_con_corridas,
                {"corridas": {"gate": "failure"}, "logs_job": {"gate": LOG_GATE}},
            )
            e = vuelta("hola\n")
            repo = e.entrega.repo
            vuelta.gh.checks_llamadas = 0

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora())
            self.assertEqual(e.fase_actual.ci_pr_estado, "reintentando")
            self.assertEqual(vuelta.gh.checks_llamadas, 1)

            _reintento_empezado(vuelta, repo, False)
            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(90))
            self.assertEqual(e.fase_actual.ci_pr_estado, "sin_resultado")
            self.assertEqual(_avisos(e), [])
            self.assertEqual(_estados_ci(r), ["reintentando", "sin_resultado"])
            self.assertEqual(vuelta.gh.checks_llamadas, 2)

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(100))
            self.assertEqual(vuelta.gh.checks_llamadas, 2)
            self.assertEqual(_estados_ci(r), ["reintentando", "sin_resultado"])


class CancelacionTambienSeRelanzaUnaVez(unittest.TestCase):
    def test_sin_run_id_no_se_declara_problema_fijo(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-sin-run-id",
                github_con_corridas,
                {"corridas": {"bateria": "cancelled"}, "sin_run_id": True},
            )
            e = vuelta("hola\n")
            repo = e.entrega.repo

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora())
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.fase_actual.ci_pr_estado, "rojo")
            self.assertEqual(e.fase_actual.ci_pr_clase, "tuberia")
            self.assertEqual(e.fase_actual.ci_pr_reintento, "permanente")
            self.assertEqual(vuelta.gh.relanzadas, [])
            avisos = _avisos(e)
            self.assertEqual(len(avisos), 1)
            texto = avisos[0]["texto"]
            self.assertIn("tubería", texto)
            self.assertIn("No se pudo relanzar", texto)
            self.assertNotIn("problema fijo", texto)

    def test_la_cancelacion_pasajera_pasa_en_la_segunda_y_no_avisa(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-cancel-1",
                github_con_corridas,
                {"corridas": {"bateria": "cancelled"}},
            )
            e = vuelta("hola\n")
            repo = e.entrega.repo

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora())
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.fase_actual.ci_pr_estado, "reintentando")
            self.assertEqual(len(vuelta.gh.relanzadas), 1)
            self.assertEqual(_avisos(e), [])

            _segunda_corrida(vuelta, repo, {"bateria": "success"})
            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(1))
            self.assertEqual(e.fase_actual.ci_pr_estado, "verde")
            self.assertEqual(_avisos(e), [])
            self.assertEqual(len(vuelta.gh.relanzadas), 1)

    def test_la_cancelacion_permanente_avisa_tras_el_unico_reintento(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-cancel-2",
                github_con_corridas,
                {"corridas": {"bateria": "cancelled"}},
            )
            e = vuelta("hola\n")
            repo = e.entrega.repo

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora())
            self.assertEqual(e.fase_actual.ci_pr_estado, "reintentando")
            self.assertEqual(len(vuelta.gh.relanzadas), 1)

            _segunda_corrida(vuelta, repo, {"bateria": "cancelled"})
            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(1))
            self.assertEqual(e.fase_actual.ci_pr_estado, "rojo")
            self.assertEqual(e.fase_actual.ci_pr_clase, "tuberia")
            self.assertEqual(e.fase_actual.ci_pr_reintento, "permanente")
            self.assertEqual(len(vuelta.gh.relanzadas), 1)
            avisos = _avisos(e)
            self.assertEqual(len(avisos), 1)
            self.assertIn("tubería", avisos[0]["texto"])
            self.assertIn("se canceló", avisos[0]["texto"])

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(2))
            self.assertEqual(len(vuelta.gh.relanzadas), 1)


class RojoInciertoNoDevuelveNiReintenta(unittest.TestCase):
    def test_avisa_que_no_puede_clasificar_y_recomienda_a_claude(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-5",
                github_con_corridas,
                {
                    "corridas": {"bateria": "failure"},
                    "logs_job": {"bateria": LOG_INCIERTO},
                },
            )
            e = vuelta("hola\n")
            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora())
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.fase_actual.ci_pr_estado, "rojo")
            self.assertEqual(e.fase_actual.ci_pr_clase, "incierto")
            self.assertEqual(vuelta.gh.relanzadas, [])
            avisos = _avisos(e)
            self.assertEqual(len(avisos), 1)
            texto = avisos[0]["texto"]
            self.assertIn("no puedo saber con certeza", texto)
            self.assertIn("Claude", texto)


class ElSondeoSeEspacia(unittest.TestCase):
    def test_dos_lecturas_seguidas_no_llaman_dos_veces_a_github(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-sondeo",
                github_con_corridas,
                {"corridas": {"bateria": "in_progress"}},
            )
            e = vuelta("hola\n")
            vuelta.gh.checks_llamadas = 0

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora())
            self.assertEqual(vuelta.gh.checks_llamadas, 1)
            self.assertEqual(_estados_ci(r), [])

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora())
            self.assertEqual(vuelta.gh.checks_llamadas, 1)

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(1))
            self.assertEqual(vuelta.gh.checks_llamadas, 2)
            self.assertEqual(_sondeos(r), [])

    def test_el_sondeo_termina_si_el_ci_no_llega(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-sondeo-tope",
                github_con_corridas,
                {"corridas": {"bateria": "in_progress"}},
            )
            e = vuelta("hola\n")
            vuelta.gh.checks_llamadas = 0

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora())
            self.assertEqual(vuelta.gh.checks_llamadas, 1)

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(90))
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.fase_actual.ci_pr_estado, "sin_resultado")
            self.assertEqual(_avisos(e), [])
            self.assertEqual(_estados_ci(r), ["sin_resultado"])
            self.assertEqual(vuelta.gh.checks_llamadas, 2)

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(100))
            self.assertEqual(vuelta.gh.checks_llamadas, 2)
            self.assertEqual(_sondeos(r), [])

    def test_un_ci_terminado_al_vencer_el_plazo_se_lee(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-sondeo-final",
                github_con_corridas,
                {"corridas": {"bateria": "in_progress"}},
            )
            e = vuelta("hola\n")
            vuelta.gh.checks_llamadas = 0

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora())
            self.assertEqual(vuelta.gh.checks_llamadas, 1)
            self.assertEqual(e.fase_actual.ci_pr_estado, "")

            cfg = json.loads((vuelta.gh.d / "repos.json").read_text())
            cfg[str(e.entrega.repo)]["corridas"] = {"bateria": "success"}
            (vuelta.gh.d / "repos.json").write_text(json.dumps(cfg))

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(90))
            self.assertEqual(e.fase_actual.ci_pr_estado, "verde")
            self.assertEqual(_estados_ci(r), ["verde"])
            self.assertEqual(vuelta.gh.checks_llamadas, 2)


class UnErrorDeLecturaNoPierdeElResultado(unittest.TestCase):
    def test_tras_un_error_se_vuelve_a_leer_y_se_procesa(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-error",
                github_con_corridas,
                {"corridas": {"bateria": "failure"}, "logs_job": {"bateria": LOG_BATERIA}},
            )
            e = vuelta("hola\n")
            vuelta.gh.fallar_checks(e.entrega.repo, 1)

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora())
            self.assertEqual(_estados_ci(r), [])
            self.assertEqual(e.estado, "terminado")

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(1))
            self.assertEqual(e.fase_actual.ci_pr_estado, "rojo")
            self.assertEqual(_estados_ci(r), ["rojo"])


class ElBarridoRevisaElCiDelPr(unittest.TestCase):
    def test_barrer_lee_el_ci_del_pr_entregado(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-barrido",
                github_con_corridas,
                {"corridas": {"bateria": "failure"}, "logs_job": {"bateria": LOG_BATERIA}},
            )
            e = vuelta("hola\n")
            self.assertEqual(e.estado, "terminado")

            ciclo.barrer(
                r.reg, _hora(), time.time(), lambda *a: None, gh=vuelta.gh
            )
            self.assertEqual(_estados_ci(r), ["rojo"])
            self.assertNotEqual(r.fold().estado, "terminado")


class UnRojoNoRebajaUnEncargoIntegrado(unittest.TestCase):
    def test_el_ci_rojo_de_un_integrado_no_vuelve_a_pr_abierto(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-integrado",
                github_con_corridas,
                {"corridas": {"bateria": "failure"}, "logs_job": {"bateria": LOG_BATERIA}},
            )
            e = vuelta("hola\n")
            r.reg.anotar(
                r.enc,
                {
                    "kind": "entrega_externa",
                    "data": {
                        "fase": e.fase_activa,
                        "intento": e.intento,
                        "paso": "integrado",
                        "repo": e.entrega.repo,
                        "numero": e.pr_numero,
                        "sha": e.pr_sha,
                        "recibo": e.pr_sha,
                        "hora": _hora(),
                    },
                },
                f"integrado-{r.enc}",
            )
            e = r.fold()
            self.assertEqual(e.nivel_alcanzado, "integrado")

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(1))
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.nivel_alcanzado, "integrado")
            self.assertEqual(e.fase_actual.ci_pr_estado, "rojo")
            avisos = _avisos(e)
            self.assertEqual(len(avisos), 1)
            self.assertIn("abrir un encargo para corregirlo", avisos[0]["texto"])
            self.assertNotIn("ya se mandó a corregir", avisos[0]["texto"])


class ElCierrePorAtajoConciliaLaCabeza(unittest.TestCase):
    def test_un_head_abierto_distinto_no_se_cierra_con_el_sha_guardado(self):  # no-roja-en-base: solo cambió el doble de GitHub; el comportamiento probado ya estaba en la base
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "ci-pr-head", github_con_corridas)
            e = vuelta("hola\n")
            fase = e.fase_actual
            producto = fase.producto
            if producto is not None:
                producto = replace(producto, sha_git=producto.sha_git or e.pr_sha)
            fase2 = replace(
                fase,
                estado="entrega",
                ultimo_paso_entrega="deepseek_leido",
                deepseek_sha=e.pr_sha,
                producto=producto,
            )
            e2 = replace(
                e,
                estado="recibido",
                fases=tuple(fase2 if f.id == fase2.id else f for f in e.fases),
            )
            otro = "f" * 40
            vuelta.gh.ver = lambda repo, numero: SimpleNamespace(
                estado="abierto", head=otro
            )

            salida = ciclo._paso_entrega(r.reg, e2, vuelta.gh, _hora())
            self.assertEqual(salida.rol, "responsable")
            self.assertIn("head", salida.motivo)


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

    def test_una_prueba_roja_tras_gitleaks_verde_es_codigo(self):
        # Rojo real de Orbit (run 41958894995): el log completo del job trae la
        # cabecera de GitHub y el paso de Gitleaks que pasó.
        self.assertEqual(
            ciclo._clasificar_fallo_ci("gate", "failure", LOG_GATE_PRUEBA_ROJA),
            "codigo",
        )

    def test_nombres_de_prueba_con_secretos_no_son_tuberia(self):
        # Rojo real del motor (run 41961774402, bateria-parte-1).
        self.assertEqual(
            ciclo._clasificar_fallo_ci(
                "bateria-parte-1", "failure", LOG_BATERIA_CON_SECRETOS
            ),
            "codigo",
        )

    def test_una_prueba_que_menciona_cobros_o_limites_sigue_siendo_codigo(self):
        for linea in (
            "E   assert 'billing' in respuesta",
            "E   RuntimeError: rate limit del cliente simulado",
            "E   assert espera == '15 minutes'",
        ):
            with self.subTest(linea=linea):
                log = _log_paso(
                    "Pruebas (una vez)",
                    "FAILED tests/test_cliente.py::test_x - " + linea,
                    linea,
                    "1 failed, 10 passed in 3.00s",
                )
                self.assertEqual(
                    ciclo._clasificar_fallo_ci("gate", "failure", log), "codigo"
                )

    def test_errores_de_codigo_con_palabras_de_tuberia_son_codigo(self):
        # Formato real: cada línea del log lleva su hora delante.
        for lineas in (
            ("ERROR: test_x (tests.test_cobro.Cobro.test_x)", "    return minutes - 5",
             "TypeError: unsupported operand", "Ran 12 tests in 0.40s", "FAILED (errors=1)"),
            ("ModuleNotFoundError: No module named 'app.payment'", "FAILED (errors=1)"),
            ("ERROR collecting tests/test_orbit.py", "E   ImportError: orbit.billing",
             "1 error in 0.50s"),
            ("E   TimeoutError: timed out", "1 passed, 1 error in 2.00s"),
        ):
            with self.subTest(lineas=lineas[-1]):
                log = _log_paso(
                    "Pruebas",
                    *(f"2026-11-05T03:14:11.7190750Z {x}" for x in lineas),
                )
                self.assertEqual(
                    ciclo._clasificar_fallo_ci("gate", "failure", log), "codigo"
                )

    def test_una_caida_de_red_que_tumba_pruebas_es_tuberia(self):
        log = _log_paso(
            "Pruebas",
            "2026-11-05T03:14:11Z E   ConnectionResetError: [Errno 104] Connection reset by peer",
            "2026-11-05T03:14:11Z 3 failed, 40 passed in 9.00s",
        )
        self.assertEqual(ciclo._clasificar_fallo_ci("gate", "failure", log), "tuberia")

    def test_un_hallazgo_de_gitleaks_sin_el_conteo_es_tuberia(self):
        log = _log_paso(
            "Gitleaks",
            "2026-11-05T03:02:05Z RuleID:      generic-api-key",
            "2026-11-05T03:02:05Z File:        docs/viejo.md",
        )
        self.assertEqual(ciclo._clasificar_fallo_ci("gate", "failure", log), "tuberia")

    def test_cero_fallas_y_una_subida_caida_es_tuberia(self):
        log = _log_paso(
            "Subir resultados",
            "2026-11-05T03:14:11Z Tests: 0 failed, 12 passed",
            "2026-11-05T03:14:12Z curl: upload timed out",
        )
        self.assertEqual(ciclo._clasificar_fallo_ci("gate", "failure", log), "tuberia")

    def test_pruebas_verdes_y_una_subida_caida_es_tuberia(self):
        log = _log_paso(
            "Probar y subir",
            "2026-11-05T03:14:11Z Ran 12 tests in 0.400s",
            "2026-11-05T03:14:11Z OK",
            "2026-11-05T03:14:12Z curl: upload timed out",
        )
        self.assertEqual(ciclo._clasificar_fallo_ci("gate", "failure", log), "tuberia")

    def test_un_limite_de_tiempo_del_paso_no_es_falta_de_minutos(self):
        log = _log_paso(
            "Pruebas", "##[error]The action has timed out after 15 minutes."
        )
        self.assertEqual(
            ciclo._causa_tuberia_en_llano(
                [{"nombre": "gate", "conclusion": "failure"}], log
            ),
            "se agotó el tiempo límite de la corrida",
        )

    def test_caidas_de_red_o_servicios_son_tuberia(self):
        for paso, linea in (
            ("Gitleaks", "curl: (22) The requested URL returned error: 503"),
            ("Run actions/checkout@v7", "error: RPC failed; HTTP 502 curl 22"),
            ("Run actions/checkout@v7", "fatal: early EOF"),
            ("Set up job", "##[error]Failed to download action 'x'. Error: 500"),
            ("Initialize containers", "Failed to initialize, postgres service is unhealthy."),
            ("Set up Python", "ERROR: Could not install packages due to an OSError: "
             "HTTPSConnectionPool(host='files.pythonhosted.org'): Max retries exceeded"),
            ("Gitleaks", "sha256sum: WARNING: 1 computed checksum did NOT match"),
        ):
            with self.subTest(linea=linea):
                self.assertEqual(
                    ciclo._clasificar_fallo_ci("gate", "failure", _log_paso(paso, linea)),
                    "tuberia",
                )

    def test_gitleaks_con_hallazgos_sigue_siendo_tuberia(self):
        self.assertEqual(
            ciclo._clasificar_fallo_ci("gate", "failure", LOG_GATE_CON_HALLAZGOS),
            "tuberia",
        )
        self.assertEqual(
            ciclo._causa_tuberia_en_llano(
                [{"nombre": "gate", "conclusion": "failure"}], LOG_GATE_CON_HALLAZGOS
            ),
            "el control de secretos encontró claves viejas en el repositorio",
        )


class CorreccionTrasTerminar(unittest.TestCase):
    def test_una_correccion_con_el_pr_abierto_reabre_en_la_misma_rama(self):
        # Caso real: el operador pidió arreglar dos pruebas del PR #393 del motor
        # y el encargo, ya terminado, rechazó la corrección (rec_4e0e48b70680).
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "corr-terminado", github_con_corridas,
                {"corridas": {"gate": "success"}},
            )
            self.addCleanup(r.reg.close)
            e = vuelta("hola\n")
            self.assertEqual(e.estado, "terminado")
            pr = e.pr_numero
            self.assertTrue(pr)

            salida = ciclo.corregir(
                r.reg, r.enc, "arregla las dos pruebas rojas", "c-1", _hora(),
                gh=vuelta.gh,
            )

            self.assertFalse(salida.get("rechazada"), salida.get("motivo"))
            e = r.reg.fold(r.enc)
            self.assertEqual(e.estado, "recibido")
            self.assertEqual(e.rol, "responsable")
            self.assertEqual(e.nivel_alcanzado, "pr_abierto")
            self.assertEqual(e.pr_numero, pr)
            self.assertIn("arregla las dos pruebas rojas", e.ajustes)

    def test_una_correccion_con_el_pr_ya_mergeado_se_rechaza(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "corr-mergeado", github_con_corridas,
                {"corridas": {"gate": "success"}},
            )
            self.addCleanup(r.reg.close)
            e = vuelta("hola\n")
            self.assertEqual(e.estado, "terminado")
            vuelta.gh.ver = lambda repo, numero: SimpleNamespace(
                estado="mergeado", head=e.pr_sha
            )
            salida = ciclo.corregir(
                r.reg, r.enc, "otra", "c-2", _hora(), gh=vuelta.gh
            )
            self.assertTrue(salida.get("rechazada"))
            self.assertIn("mergeado", salida["motivo"])
            self.assertEqual(r.reg.fold(r.enc).estado, "terminado")

    def test_sin_poder_leer_el_pr_la_correccion_se_rechaza(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "corr-sin-gh", github_con_corridas,
                {"corridas": {"gate": "success"}},
            )
            self.addCleanup(r.reg.close)
            vuelta("hola\n")
            salida = ciclo.corregir(r.reg, r.enc, "otra", "c-3", _hora(), gh=None)
            self.assertTrue(salida.get("rechazada"))
            self.assertEqual(r.reg.fold(r.enc).estado, "terminado")

    def test_sin_puerto_explicito_usa_el_github_del_registro(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "corr-puerto", github_con_corridas,
                {"corridas": {"gate": "success"}},
            )
            self.addCleanup(r.reg.close)
            vuelta("hola\n")
            (r.reg.base / "github.json").write_text(
                json.dumps({"falso": str(vuelta.gh.d)})
            )
            salida = ciclo.corregir(r.reg, r.enc, "arregla", "c-4", _hora())
            self.assertFalse(salida.get("rechazada"), salida.get("motivo"))
            self.assertEqual(r.reg.fold(r.enc).estado, "recibido")

    def test_si_github_falla_al_leer_el_pr_se_rechaza(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "corr-gh-falla", github_con_corridas,
                {"corridas": {"gate": "success"}},
            )
            self.addCleanup(r.reg.close)
            vuelta("hola\n")

            def caido(repo, numero):
                raise RuntimeError("gh pr view: HTTP 502")

            vuelta.gh.ver = caido
            salida = ciclo.corregir(r.reg, r.enc, "otra", "c-5", _hora(), gh=vuelta.gh)
            self.assertTrue(salida.get("rechazada"))
            self.assertIn("no se pudo leer", salida["motivo"])
            self.assertEqual(r.reg.fold(r.enc).estado, "terminado")

    def test_una_correccion_tras_integrar_se_rechaza(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "corr-integrado", github_con_corridas,
                {"corridas": {"gate": "success"}},
            )
            self.addCleanup(r.reg.close)
            e = vuelta("hola\n")
            self.assertEqual(e.estado, "terminado")
            integrado = con_fase(
                e, replace(e.fase_actual, ultimo_paso_entrega="integrado")
            )
            with self.assertRaises(TransicionIlegal):
                reducir(
                    integrado,
                    {"kind": "ajuste", "data": {"texto": "otra", "recibo": "x"}},
                )


class PendienteOVerdeNoDetiene(unittest.TestCase):
    def test_pendiente_no_hace_nada_y_verde_cierra_sin_devolver(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self,
                tmp,
                "ci-pr-6",
                github_con_corridas,
                {"corridas": {"bateria": "in_progress"}},
            )
            e = vuelta("hola\n")
            self.assertEqual(e.estado, "terminado")

            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora())
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(_estados_ci(r), [])

            cfg = json.loads((vuelta.gh.d / "repos.json").read_text())
            cfg[str(e.entrega.repo)]["corridas"] = {"bateria": "success"}
            (vuelta.gh.d / "repos.json").write_text(json.dumps(cfg))
            e = ciclo._revisar_ci_del_pr(r.reg, e, vuelta.gh, _hora(1))
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(e.fase_actual.ci_pr_estado, "verde")
            self.assertEqual(_estados_ci(r), ["verde"])


if __name__ == "__main__":
    unittest.main()
