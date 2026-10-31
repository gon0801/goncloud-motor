"""Devolver el trabajo por un bloqueante de DeepSeek o de checks y recibir la
misma version no puede dejar el encargo quieto: el producto conserva su commit
y el freno aplica su escalera."""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
import unittest
from dataclasses import replace
from unittest import mock

from motor.encargos import ciclo
from motor.encargos.ciclo import _aplica_entrega, _paso_entrega, ahora_iso, barrer
from motor.github import FakeGitHub
from tests.test_flujo_revisiones import _entrega_pr, _GhConDeepSeek, _lanzaria


def _turno_sin_llm(r, gh):
    """Un barrido del supervisor: la entrega que aplica corre sin agente."""
    e = r.fold()
    if _aplica_entrega(e):
        e = _paso_entrega(r.reg, e, gh, ahora_iso())
    return e


class MismaVersionTrasDevolucion(unittest.TestCase):
    def test_deepseek_misma_version_recupera_sha_y_escala_sin_quedar_quieto(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "mv-ds", _GhConDeepSeek)
            e = vuelta("uno")
            self.assertEqual(e.estrategia, "investigar")
            commit = e.producto.sha_git
            self.assertTrue(commit)
            self.assertEqual(e.deepseek_sha, commit)

            r.incorporar()
            e = r.fold()
            self.assertEqual(e.producto.sha_git, commit)
            self.assertFalse(_aplica_entrega(e))
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertEqual(e.sin_progreso, 1)
            self.assertNotEqual(e.estado, "necesita_usuario")
            self.assertTrue(_lanzaria(e))
            antes = len(r.reg.eventos(r.enc))
            self.assertEqual(_turno_sin_llm(r, vuelta.gh).estrategia, e.estrategia)
            self.assertEqual(len(r.reg.eventos(r.enc)), antes)

            r.incorporar()
            e = r.fold()
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertIn("DeepSeek", e.motivo)
            self.assertIn("Se intento", e.motivo)
            self.assertFalse(_aplica_entrega(e))
            self.assertFalse(_lanzaria(e))

    def test_checks_en_rojo_misma_version_recupera_sha_y_escala(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "mv-ci", FakeGitHub,
                {"requeridos": ["ci"], "checks": {"ci": "exit 1"}},
            )
            e = vuelta("uno")
            self.assertEqual(e.estrategia, "investigar")
            commit = e.producto.sha_git
            self.assertTrue(commit)

            r.incorporar()
            e = r.fold()
            self.assertEqual(e.producto.sha_git, commit)
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertEqual(e.sin_progreso, 1)
            self.assertTrue(_lanzaria(e))

            r.incorporar()
            e = r.fold()
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertIn("checks requeridos en rojo: ci", e.motivo)
            self.assertIn("Se intento", e.motivo)
            self.assertFalse(_lanzaria(e))

    def test_una_version_distinta_no_hereda_el_commit(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "mv-otra", _GhConDeepSeek)
            e = vuelta("uno")
            commit = e.producto.sha_git
            e = vuelta("dos")
            self.assertNotEqual(e.producto.sha_git, commit)
            self.assertEqual(e.deepseek_sha, e.producto.sha_git)

    def test_misma_firma_con_otra_cabeza_de_git_no_hereda_el_commit(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "mv-cabeza", _GhConDeepSeek)
            vuelta("uno")
            subprocess.run(
                ["git", "-C", str(r.espacio), "reset", "--quiet", "--soft", "HEAD~1"],
                check=True,
            )
            r.incorporar()
            self.assertEqual(r.fold().producto.sha_git, "")


def _rerun_del_check(gh, repo, nombre, comando):
    """Vuelve a correr `nombre` sobre el mismo SHA con otro resultado: el
    veredicto guardado se olvida y el check pasa a ejecutar `comando`."""
    cfg = json.loads((gh.d / "repos.json").read_text())
    cfg[repo]["checks"][nombre] = comando
    (gh.d / "repos.json").write_text(json.dumps(cfg))
    estado = json.loads((gh.d / "github.json").read_text())
    estado["checks"] = {
        k: v for k, v in estado["checks"].items() if not k.endswith(f"#{nombre}")
    }
    (gh.d / "github.json").write_text(json.dumps(estado))


def _barrido(r, gh, t=None):
    t = t if t is not None else time.time()
    barrer(r.reg, ahora_iso(t), t, None, gh=gh)
    return r.fold()


def _pasos(r, paso):
    return [
        ev for ev in r.reg.eventos(r.enc)
        if ev["kind"] == "entrega_externa" and ev["data"].get("paso") == paso
    ]


class CheckQueSeRecuperaEnElMismoSha(unittest.TestCase):
    """Un check requerido en rojo que pasa a verde en un re-run del mismo SHA
    se observa aunque la compuerta de la entrega siga cerrada para ese SHA."""

    def _en_rojo(self, tmp, clave):
        vuelta, r = _entrega_pr(
            self, tmp, clave, _GhConDeepSeek,
            {"requeridos": ["ci"], "checks": {"ci": "exit 1"}},
        )
        vuelta.gh.titulos = ()
        e = vuelta("uno")
        self.assertEqual(e.fase_actual.fallas_checks, ("ci",))
        self.assertEqual(e.fase_actual.entrega_bloqueada_sha, e.producto.sha_git)
        self.assertFalse(_aplica_entrega(e))
        return vuelta, r, str(r.fold().entrega.repo)

    def test_el_rerun_en_verde_se_anota_y_el_encargo_avanza(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, repo = self._en_rojo(tmp, "rr-verde")
            sha = r.fold().producto.sha_git

            t0 = time.time()
            e = _barrido(r, vuelta.gh, t0)
            self.assertEqual(e.fase_actual.fallas_checks, ("ci",))
            self.assertEqual(_pasos(r, "checks_verdes"), [])

            _rerun_del_check(vuelta.gh, repo, "ci", "exit 0")
            e = _barrido(r, vuelta.gh, t0 + ciclo._RELECTURA_CHECKS_S + 1)

            self.assertEqual(len(_pasos(r, "checks_verdes")), 1)
            self.assertEqual(_pasos(r, "checks_verdes")[0]["data"]["sha"], sha)
            self.assertEqual(e.fase_actual.fallas_checks, ())
            self.assertEqual(e.fase_actual.entrega_bloqueada_sha, "")
            self.assertEqual(e.sin_progreso, 0)
            self.assertNotEqual(e.estado, "necesita_usuario")
            self.assertEqual(e.nivel_alcanzado, "pr")

    def test_el_rerun_en_verde_antes_de_la_vuelta_no_cuenta_como_sin_avance(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, repo = self._en_rojo(tmp, "rr-vuelta")
            commit = r.fold().producto.sha_git

            r.incorporar()
            e = r.fold()
            self.assertEqual(e.sin_progreso, 1)
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")

            _rerun_del_check(vuelta.gh, repo, "ci", "exit 0")
            e = _barrido(r, vuelta.gh)
            self.assertEqual(e.fase_actual.fallas_checks, ())
            self.assertEqual(e.sin_progreso, 0)

            r.incorporar()
            e = r.fold()
            self.assertEqual(e.producto.sha_git, commit)
            self.assertEqual(e.sin_progreso, 0)
            self.assertNotEqual(e.estado, "necesita_usuario")
            self.assertEqual(e.nivel_alcanzado, "pr")

    def test_si_sigue_en_rojo_no_se_anota_nada_ni_se_vuelve_a_entregar(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, repo = self._en_rojo(tmp, "rr-rojo")
            antes = len(r.reg.eventos(r.enc))
            t0 = time.time()
            for i in range(3):
                e = _barrido(r, vuelta.gh, t0 + i)
            self.assertEqual(len(r.reg.eventos(r.enc)), antes)
            self.assertEqual(e.fase_actual.fallas_checks, ("ci",))
            self.assertFalse(_aplica_entrega(e))
            self.assertEqual(_pasos(r, "pr_listo")[0]["data"]["sha"], e.producto.sha_git)
            self.assertEqual(len(_pasos(r, "pr_listo")), 1)

    def _contar_lecturas(self, gh):
        lecturas = []
        original = gh.checks

        def checks(*args, **kwargs):
            lecturas.append(args[2])
            return original(*args, **kwargs)

        gh.checks = checks
        return lecturas

    def test_barridos_seguidos_con_el_check_en_rojo_no_son_lecturas_seguidas(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, repo = self._en_rojo(tmp, "rr-cuota")
            lecturas = self._contar_lecturas(vuelta.gh)
            t0 = time.time()
            for i in range(20):
                e = _barrido(r, vuelta.gh, t0 + i)
            self.assertEqual(len(lecturas), 1)
            self.assertEqual(lecturas[0], e.producto.sha_git)
            self.assertEqual(e.fase_actual.fallas_checks, ("ci",))

            _barrido(r, vuelta.gh, t0 + ciclo._RELECTURA_CHECKS_S + 1)
            self.assertEqual(len(lecturas), 2)

    def _incorporacion_pendiente(self, r):
        """El agente cerró y dejó su outcome, pero `_incorporar` aún no publica
        `incorporado`, como mientras corre el comprobador del supervisor."""
        e = r.fold()
        n = e.generacion + 1
        r.reg.anotar(
            r.enc, {"kind": "reserva", "data": {"rol": "responsable", "generacion": n}},
            f"t-reserva:{n}",
        )
        r.reg.anotar(r.enc, {"kind": "reserva_cerrada", "data": {}}, f"t-cerrada:{n}")
        r.reg.anotar(
            r.enc,
            {
                "kind": "estrategia",
                "data": {
                    "rol": "responsable", "estrategia": e.estrategia,
                    "motivo": e.motivo, "siguiente": e.siguiente,
                },
            },
            f"t-rol:{n}",
        )
        (r.d / "outcome.json").write_text("{}")
        (r.d / "incorporado").unlink(missing_ok=True)

    def test_incorporacion_pendiente_fuerza_una_sola_lectura_y_anota_el_verde_antes(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, repo = self._en_rojo(tmp, "rr-pendiente")
            lecturas = self._contar_lecturas(vuelta.gh)
            t0 = time.time()
            _barrido(r, vuelta.gh, t0)
            self.assertEqual(len(lecturas), 1)

            _rerun_del_check(vuelta.gh, repo, "ci", "exit 0")
            self._incorporacion_pendiente(r)
            vistos = []

            def incorporar_pendiente(registro, enc, *args, **kwargs):
                vistos.append(enc.fase_actual.fallas_checks)
                return enc

            with mock.patch.object(ciclo, "_incorporar", incorporar_pendiente):
                _barrido(r, vuelta.gh, t0 + 1)

            self.assertEqual(vistos, [()], "el verde se anota antes de contar la vuelta")
            self.assertEqual(len(_pasos(r, "checks_verdes")), 1)

    def test_el_forzado_no_se_consume_si_la_lectura_falla(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, repo = self._en_rojo(tmp, "rr-fallo")
            gh = vuelta.gh
            t0 = time.time()
            _barrido(r, gh, t0)
            _rerun_del_check(gh, repo, "ci", "exit 0")
            self._incorporacion_pendiente(r)

            intentos = []
            original = gh.checks
            falla = [True]

            def checks(*args, **kwargs):
                intentos.append(1)
                if falla[0]:
                    raise RuntimeError("gh: HTTP 502")
                return original(*args, **kwargs)

            gh.checks = checks
            vistos = []

            def incorporar_pendiente(registro, enc, *args, **kwargs):
                vistos.append(enc.fase_actual.fallas_checks)
                return enc

            marca = r.d / "checks_lectura.json"
            with mock.patch.object(ciclo, "_incorporar", incorporar_pendiente):
                _barrido(r, gh, t0 + 1)
                self.assertEqual(len(intentos), 1)
                self.assertIn("HTTP 502", json.loads(marca.read_text())["error"])
                self.assertEqual(json.loads(marca.read_text())["forzada"], "")
                for i in range(2, 15):
                    _barrido(r, gh, t0 + i)
                self.assertEqual(len(intentos), 1, "el fallo tambien espacia")

                falla[0] = False
                _barrido(r, gh, t0 + 16)

            self.assertEqual(vistos[0], ("ci",))
            self.assertEqual(vistos[-1], (), "el reintento forzado anota el verde")
            self.assertNotEqual(json.loads(marca.read_text())["forzada"], "")
            self.assertEqual(len(_pasos(r, "checks_verdes")), 1)

    def test_incorporacion_pendiente_con_el_check_en_rojo_no_lee_en_cada_barrido(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, repo = self._en_rojo(tmp, "rr-pend-rojo")
            lecturas = self._contar_lecturas(vuelta.gh)
            t0 = time.time()
            _barrido(r, vuelta.gh, t0)
            self._incorporacion_pendiente(r)
            with mock.patch.object(
                ciclo, "_incorporar", lambda registro, enc, *a, **k: enc
            ):
                for i in range(1, 21):
                    e = _barrido(r, vuelta.gh, t0 + i)
            self.assertEqual(len(lecturas), 2)
            self.assertEqual(e.fase_actual.fallas_checks, ("ci",))

            with mock.patch.object(
                ciclo, "_incorporar", lambda registro, enc, *a, **k: enc
            ):
                _barrido(r, vuelta.gh, t0 + ciclo._RELECTURA_CHECKS_S + 2)
            self.assertEqual(len(lecturas), 3)

    def test_con_el_agente_corriendo_no_se_lee_y_al_incorporar_se_fuerza(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, repo = self._en_rojo(tmp, "rr-forzar")
            lecturas = self._contar_lecturas(vuelta.gh)
            t0 = time.time()
            _barrido(r, vuelta.gh, t0)
            self.assertEqual(len(lecturas), 1)

            ciclo._reconciliar_checks(
                r.reg, replace(r.fold(), proc=object()), vuelta.gh, r.espacio,
                ahora_iso(), t0 + ciclo._RELECTURA_CHECKS_S + 1,
            )
            self.assertEqual(len(lecturas), 1)

            _rerun_del_check(vuelta.gh, repo, "ci", "exit 0")
            e = ciclo._reconciliar_checks(
                r.reg, r.fold(), vuelta.gh, r.espacio, ahora_iso(), t0 + 2,
                forzar=True,
            )
            self.assertEqual(len(lecturas), 2)
            self.assertEqual(e.fase_actual.fallas_checks, ())

    def test_un_check_que_vuelve_a_rojo_tras_el_verde_se_registra_otra_vez(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, repo = self._en_rojo(tmp, "rr-flap")
            espacio = r.espacio

            reloj = [time.time()]

            def reconciliar():
                reloj[0] += ciclo._RELECTURA_CHECKS_S + 1
                return ciclo._reconciliar_checks(
                    r.reg, r.fold(), vuelta.gh, espacio, ahora_iso(), reloj[0],
                )

            _rerun_del_check(vuelta.gh, repo, "ci", "exit 0")
            self.assertEqual(reconciliar().fase_actual.fallas_checks, ())

            _rerun_del_check(vuelta.gh, repo, "ci", "exit 1")
            e = _paso_entrega(r.reg, r.fold(), vuelta.gh, ahora_iso())
            self.assertEqual(e.fase_actual.fallas_checks, ("ci",))
            self.assertEqual(e.fase_actual.entrega_bloqueada_sha, e.producto.sha_git)

            _rerun_del_check(vuelta.gh, repo, "ci", "exit 0")
            e = reconciliar()
            self.assertEqual(len(_pasos(r, "checks_verdes")), 2)
            self.assertEqual(e.fase_actual.fallas_checks, ())
            self.assertEqual(e.fase_actual.entrega_bloqueada_sha, "")


if __name__ == "__main__":
    unittest.main()
