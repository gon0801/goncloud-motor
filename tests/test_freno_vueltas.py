"""El freno de vueltas sin avance no para encargos por error.

Cinco casos del 1-nov (docs/flujo-revisiones.md, «El freno»). Los casos 3 y 4, la
lectura de DeepSeek, están en tests/test_lectura_deepseek.py. Aquí:

1. Doble conteo en un barrido (enc_1b8916e8a0bac627, 07:46Z): una vuelta contó dos
   veces, por la observación del supervisor y por la lectura de DeepSeek, y escaló
   sin dar el turno de diagnóstico.
2. Cuenta con una revisión externa pendiente (mismo encargo, 09:03Z): escaló con
   sin_progreso 4 mientras esperaba a DeepSeek sobre el commit nuevo, contando un F1
   viejo que DeepSeek ya había descartado.
5. Escala como «el mismo bloqueo» con hallazgos nuevos (enc_3f7de8a83bd2aae6, #152):
   tras la vuelta de diagnóstico de F4, la lectura trajo F5 y F6 nuevos y escaló."""

from __future__ import annotations

import tempfile
import time
import unittest
from unittest import mock

from motor.encargos import ciclo
from motor.encargos.aislamiento import SondaEntorno
from motor.encargos.ciclo import ahora_iso
from motor.encargos.dominio import (
    TOPE_VUELTAS_NUEVAS,
    bloqueantes_abiertos,
    revision_externa_pendiente,
)
from motor.github import ChecksVerdict, FakeGitHub, veredicto
from tests.test_entrega_misma_version import _barrido, _rerun_del_check
from tests.test_flujo_revisiones import _entrega_pr, _lanzaria
from tests.test_lectura_deepseek import _comentario, _hallazgo, _ids


def setUpModule():
    parche = mock.patch(
        "motor.encargos.aislamiento.sondear", return_value=SondaEntorno(True)
    )
    parche.start()
    unittest.addModuleCleanup(parche.stop)


class _GhPorCommit(FakeGitHub):
    """DeepSeek publica `textos[i]` sobre el i-ésimo commit que el motor le pregunta,
    o nada si `textos[i]` es None o falta: la revisión de ese commit aún no llegó."""

    def __init__(self, d):
        super().__init__(d)
        self.textos: list = []
        self.vistos: list[str] = []

    def comentarios_deepseek(self, repo, numero, sha=""):
        if sha not in self.vistos:
            self.vistos.append(sha)
        i = self.vistos.index(sha)
        texto = self.textos[i] if i < len(self.textos) else None
        return [{"texto": texto, "autor": "DeepSeek", "sha": sha}] if texto else []


def _devolver_el_trabajo(r, marca: str) -> None:
    """El propietario corrige, o el supervisor vuelve a dar el turno: el trabajo
    vuelve al responsable con lo que hay abierto."""
    e = r.fold()
    r.reg.anotar(
        r.enc,
        {
            "kind": "estrategia",
            "data": {
                "estrategia": "investigar",
                "rol": "responsable",
                "motivo": "volver a intentarlo",
                "siguiente": "corregir lo abierto",
                "hora": ahora_iso(),
            },
        },
        f"t-devolver:{marca}:{e.generacion}",
    )


class UnaVueltaCuentaUnaVez(unittest.TestCase):
    def _en_espera_del_commit_nuevo(self, tmp, clave):
        """F1 abierto del commit viejo; el responsable entregó otro commit y DeepSeek
        aún no lo revisó (es el estado de enc_1b8916e8a0bac627 a las 07:46Z)."""
        vuelta, r = _entrega_pr(self, tmp, clave, _GhPorCommit)
        gh = vuelta.gh
        gh.textos = [_comentario(_hallazgo("F1"))]
        e = vuelta("uno")
        self.assertEqual(_ids(e), ["F1"])
        viejo = e.producto.sha_git
        e = vuelta("dos")
        nuevo = e.producto.sha_git
        self.assertNotEqual(viejo, nuevo)
        self.assertEqual(e.deepseek_sha, viejo)
        self.assertEqual(e.estado, "esperando_reintento")
        return vuelta, r, gh

    def test_observacion_y_lectura_de_deepseek_en_un_barrido_cuentan_una_vez(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, gh = self._en_espera_del_commit_nuevo(tmp, "fr-1")
            gh.textos = [_comentario(_hallazgo("F1")), _comentario(_hallazgo("F1"))]
            _devolver_el_trabajo(r, "uno")

            # Un barrido: el supervisor incorpora la vuelta del responsable (misma
            # versión) y, ya con la revisión de DeepSeek del commit nuevo, la lee.
            r.incorporar()
            e = r.fold()
            if ciclo._aplica_entrega(e):
                e = ciclo._paso_entrega(r.reg, e, gh, ahora_iso())

            self.assertEqual(e.sin_progreso, 1)
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertNotEqual(e.estado, "necesita_usuario")

    def test_dos_fuentes_de_la_misma_vuelta_no_suman(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "fr-1b", _GhPorCommit)
            vuelta.gh.textos = [_comentario(_hallazgo("F1"))]
            e = vuelta("uno")
            hora = ahora_iso()
            ciclo._sin_avance(r.reg, e, "F1", "fuente uno", hora, "uno")
            ciclo._sin_avance(r.reg, r.fold(), "F1", "fuente dos", hora, "dos")
            e = r.fold()
            self.assertEqual(e.sin_progreso, 1)
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertNotEqual(e.estado, "necesita_usuario")

    def test_la_vuelta_siguiente_si_cuenta_y_escala(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "fr-1c", _GhPorCommit)
            vuelta.gh.textos = [_comentario(_hallazgo("F1"))]
            vuelta("uno")
            r.incorporar()
            self.assertEqual(r.fold().sin_progreso, 1)
            r.incorporar()
            e = r.fold()
            self.assertEqual(e.estado, "necesita_usuario")


class SinContarConUnaRevisionExternaPendiente(unittest.TestCase):
    def test_con_deepseek_pendiente_del_commit_nuevo_el_freno_no_escala(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "fr-2", _GhPorCommit)
            gh = vuelta.gh
            gh.textos = [_comentario(_hallazgo("F1"))]
            vuelta("uno")
            e = vuelta("dos")
            self.assertEqual(e.estado, "esperando_reintento")
            # F1 sigue abierto en el estado aunque DeepSeek ya lo descartó: el motor
            # no ha leído el commit nuevo, que es donde está el descarte.
            self.assertEqual(_ids(e), ["F1"])

            for i in range(4):
                _devolver_el_trabajo(r, f"p{i}")
                r.incorporar()
                e = r.fold()
                self.assertEqual(e.sin_progreso, 0, i)
                self.assertNotEqual(e.estado, "necesita_usuario", i)
                self.assertNotEqual(e.estrategia, "diagnosticar_bloqueo", i)
            self.assertEqual(
                [ev for ev in r.reg.eventos(r.enc) if ev["kind"] == "sin_progreso"], []
            )
            self.assertTrue(revision_externa_pendiente(e))

            # DeepSeek descarta F1 sobre el commit nuevo: la lectura lo cierra y la
            # fase sigue sin pasar por el freno.
            gh.textos = [_comentario(_hallazgo("F1")), _comentario()]
            e = ciclo._paso_entrega(r.reg, r.fold(), gh, ahora_iso())
            self.assertEqual(_ids(e), [])
            self.assertEqual(e.sin_progreso, 0)
            self.assertEqual(e.nivel_alcanzado, "pr")

    def test_con_los_checks_corriendo_el_freno_no_cuenta(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "fr-2c", _GhPorCommit,
                {"requeridos": ["ci"], "checks": {"ci": "exit 1"}},
            )
            gh = vuelta.gh
            e = vuelta("uno")
            sha = e.producto.sha_git
            self.assertEqual(e.fase_actual.fallas_checks, ("ci",))
            self.assertFalse(revision_externa_pendiente(e))

            real = gh.checks
            gh.checks = lambda *a, **k: ChecksVerdict(
                sha=sha, terminal=False, green=False, faltan=("ci",), fallas=(),
                en_marcha=("ci",),
            )
            t0 = time.time()
            e = _barrido(r, gh, t0)

            for i in range(3):
                _devolver_el_trabajo(r, f"c{i}")
                r.incorporar()
                e = r.fold()
                self.assertEqual(e.sin_progreso, 0, i)
                self.assertNotEqual(e.estado, "necesita_usuario", i)
            self.assertEqual(e.fase_actual.checks_esperando_sha, sha)
            self.assertTrue(revision_externa_pendiente(e))

            # La corrida que esperaba termina en rojo: ya no hay nada pendiente y la
            # vuelta siguiente cuenta.
            gh.checks = real
            e = _barrido(r, gh, t0 + ciclo._RELECTURA_CHECKS_S + 1)
            self.assertEqual(e.fase_actual.checks_esperando_sha, "")
            self.assertFalse(revision_externa_pendiente(e))
            _devolver_el_trabajo(r, "c-fin")
            r.incorporar()
            e = r.fold()
            self.assertEqual(e.sin_progreso, 1)
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")


    def test_la_corrida_que_termina_en_rojo_refresca_los_checks_en_rojo(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "fr-2d", _GhPorCommit,
                {
                    "requeridos": ["ci", "lint"],
                    "checks": {"ci": "exit 1", "lint": "exit 1"},
                },
            )
            gh = vuelta.gh
            e = vuelta("uno")
            sha = e.producto.sha_git
            self.assertEqual(e.fase_actual.fallas_checks, ("ci", "lint"))
            repo = str(e.entrega.repo)

            real = gh.checks
            gh.checks = lambda *a, **k: ChecksVerdict(
                sha=sha, terminal=False, green=False, faltan=("ci", "lint"), fallas=(),
                en_marcha=("ci", "lint"),
            )
            t0 = time.time()
            e = _barrido(r, gh, t0)
            self.assertEqual(e.fase_actual.checks_esperando_sha, sha)

            # La corrida nueva termina: lint pasa a verde y ci sigue en rojo.
            gh.checks = real
            _rerun_del_check(gh, repo, "lint", "exit 0")
            e = _barrido(r, gh, t0 + ciclo._RELECTURA_CHECKS_S + 1)
            self.assertEqual(e.fase_actual.checks_esperando_sha, "")
            self.assertEqual(e.fase_actual.fallas_checks, ("ci",))
            self.assertEqual(e.fase_actual.entrega_bloqueada_sha, sha)
            self.assertIn("check:lint", e.fase_actual.cerrados)
            self.assertEqual(e.sin_progreso, 0)

    def test_una_lectura_a_mitad_de_corrida_no_se_toma_por_terminada(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "fr-2f", _GhPorCommit,
                {
                    "requeridos": ["ci", "lint"],
                    "checks": {"ci": "exit 1", "lint": "exit 1"},
                },
            )
            gh = vuelta.gh
            e = vuelta("uno")
            sha = e.producto.sha_git
            self.assertEqual(e.fase_actual.fallas_checks, ("ci", "lint"))

            # Corrida nueva a medias: ci ya salió en rojo, lint todavía corre.
            gh.checks = lambda *a, **k: ChecksVerdict(
                sha=sha, terminal=True, green=False, faltan=("lint",), fallas=("ci",),
                en_marcha=("lint",),
            )
            t0 = time.time()
            e = _barrido(r, gh, t0)
            self.assertEqual(e.fase_actual.fallas_checks, ("ci", "lint"))
            self.assertEqual(e.fase_actual.checks_esperando_sha, sha)
            self.assertNotIn("check:lint", e.fase_actual.cerrados)
            self.assertEqual(e.sin_progreso, 0)
            self.assertTrue(revision_externa_pendiente(e))

            # Termina: lint pasa y ci sigue rojo. Ahora sí se refresca.
            gh.checks = lambda *a, **k: ChecksVerdict(
                sha=sha, terminal=True, green=False, faltan=(), fallas=("ci",),
                github_termino=True,
            )
            e = _barrido(r, gh, t0 + ciclo._RELECTURA_CHECKS_S + 1)
            self.assertEqual(e.fase_actual.fallas_checks, ("ci",))
            self.assertEqual(e.fase_actual.checks_esperando_sha, "")
            self.assertIn("check:lint", e.fase_actual.cerrados)

    def test_un_requerido_con_estado_legacy_pendiente_cuenta_como_en_marcha(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "fr-2h", _GhPorCommit,
                {
                    "requeridos": ["ci", "lint"],
                    "checks": {"ci": "exit 1", "lint": "exit 1"},
                },
            )
            gh = vuelta.gh
            e = vuelta("uno")
            sha = e.producto.sha_git
            self.assertEqual(e.fase_actual.fallas_checks, ("ci", "lint"))

            # ci terminó en rojo; lint se reporta como estado legacy «pending», sin check run.
            gh.checks = lambda *a, **k: veredicto(
                sha, ("ci", "lint"),
                [{"name": "ci", "status": "completed", "conclusion": "failure"}],
                "",
                [{"context": "lint", "state": "pending"}],
            )
            e = _barrido(r, gh, time.time())
            self.assertEqual(e.fase_actual.fallas_checks, ("ci", "lint"))
            self.assertEqual(e.fase_actual.checks_esperando_sha, sha)
            self.assertNotIn("check:lint", e.fase_actual.cerrados)

    def test_una_corrida_ajena_a_los_requeridos_no_retiene_el_refresco(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "fr-2g", _GhPorCommit,
                {
                    "requeridos": ["ci", "lint"],
                    "checks": {"ci": "exit 1", "lint": "exit 1"},
                },
            )
            gh = vuelta.gh
            e = vuelta("uno")
            sha = e.producto.sha_git
            self.assertEqual(e.fase_actual.fallas_checks, ("ci", "lint"))

            # Los requeridos terminaron (lint pasa, ci sigue en rojo), pero GitHub
            # todavía corre otro workflow que no es requerido: github_termino es falso.
            gh.checks = lambda *a, **k: ChecksVerdict(
                sha=sha, terminal=True, green=False, faltan=(), fallas=("ci",),
                github_termino=False,
            )
            e = _barrido(r, gh, time.time())
            self.assertEqual(e.fase_actual.fallas_checks, ("ci",))
            self.assertIn("check:lint", e.fase_actual.cerrados)
            self.assertEqual(e.fase_actual.checks_esperando_sha, "")

    def test_una_corrida_que_termina_con_otros_checks_en_rojo_sin_haberla_visto_pendiente_tambien_refresca(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "fr-2e", _GhPorCommit,
                {
                    "requeridos": ["ci", "lint"],
                    "checks": {"ci": "exit 1", "lint": "exit 1"},
                },
            )
            gh = vuelta.gh
            e = vuelta("uno")
            self.assertEqual(e.fase_actual.fallas_checks, ("ci", "lint"))
            _rerun_del_check(gh, str(e.entrega.repo), "ci", "exit 0")
            e = _barrido(r, gh, time.time())
            self.assertEqual(e.fase_actual.fallas_checks, ("lint",))


class CommitNuevoConElNivelPrYaAlcanzado(unittest.TestCase):
    """`_sha_final_sin_leer`: con el nivel pr ya alcanzado, un commit que DeepSeek aún no
    leyó no cierra por el atajo de `_paso_entrega`: se lee primero."""

    def test_el_commit_nuevo_se_lee_antes_de_cerrar(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "fr-sha", _GhPorCommit)
            gh = vuelta.gh
            gh.textos = [_comentario(), _comentario()]
            real = gh.comentarios_deepseek
            llamadas = []

            def falla_la_segunda(repo, numero, sha=""):
                llamadas.append(sha)
                if len(llamadas) == 2:
                    raise RuntimeError("gh api: 502")
                return real(repo, numero, sha)

            gh.comentarios_deepseek = falla_la_segunda
            e = vuelta("uno")
            viejo = e.producto.sha_git
            # La comprobación previa falló: el nivel pr está alcanzado y la fase sigue abierta.
            self.assertEqual(e.nivel_alcanzado, "pr")
            self.assertNotEqual(e.estado, "terminado")

            gh.comentarios_deepseek = real
            e = vuelta("dos")
            nuevo = e.producto.sha_git
            self.assertNotEqual(viejo, nuevo)
            self.assertEqual(e.deepseek_sha, nuevo)
            self.assertEqual(e.estado, "terminado")
            self.assertEqual(gh.vistos, [viejo, nuevo])


class EsperaDeChecksEnLaEntrega(unittest.TestCase):
    """`_paso_entrega` anota `checks_esperando` solo si algún requerido está en marcha."""

    def _entrega_con_checks(self, tmp, clave, en_marcha):
        vuelta, r = _entrega_pr(
            self, tmp, clave, _GhPorCommit, {"requeridos": ["ci"], "checks": {"ci": "exit 0"}}
        )
        vuelta.gh.checks = lambda _repo, *a, **k: ChecksVerdict(
            sha=a[1] if len(a) > 1 else "", terminal=False, green=False, faltan=("ci",),
            fallas=(), en_marcha=en_marcha,
        )
        return vuelta, r

    def test_con_un_requerido_en_marcha_la_entrega_anota_la_espera(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = self._entrega_con_checks(tmp, "fr-esp-a", ("ci",))
            e = vuelta("uno")
            sha = e.producto.sha_git
            self.assertEqual(e.estado, "esperando_reintento")
            self.assertIn("esperando checks requeridos", e.motivo)
            self.assertEqual(e.fase_actual.checks_esperando_sha, sha)
            self.assertTrue(revision_externa_pendiente(e))
            pasos = [
                ev for ev in r.reg.eventos(r.enc)
                if ev["kind"] == "entrega_externa"
                and ev["data"].get("paso") == "checks_esperando"
            ]
            self.assertEqual(len(pasos), 1)

    def test_un_requerido_que_nadie_corre_no_anota_la_espera_de_checks(self):
        # La entrega espera a la vista, pero sin `checks_esperando`: no hay corrida que
        # esperar. (El freno sigue sin contar aqui por DeepSeek, que aun no leyo el commit;
        # que un requerido que nadie corre no suspende el conteo se prueba abajo.)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = self._entrega_con_checks(tmp, "fr-esp-b", ())
            e = vuelta("uno")
            self.assertEqual(e.estado, "esperando_reintento")
            self.assertIn("esperando checks requeridos", e.motivo)
            self.assertEqual(e.fase_actual.checks_esperando_sha, "")
            self.assertEqual(
                [
                    ev for ev in r.reg.eventos(r.enc)
                    if ev["kind"] == "entrega_externa"
                    and ev["data"].get("paso") == "checks_esperando"
                ],
                [],
            )

    def test_un_requerido_que_nadie_corre_no_suspende_el_conteo_de_vueltas(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(
                self, tmp, "fr-esp-c", _GhPorCommit,
                {"requeridos": ["ci", "fantasma"], "checks": {"ci": "exit 1"}},
            )
            gh = vuelta.gh
            e = vuelta("uno")
            sha = e.producto.sha_git
            self.assertEqual(e.fase_actual.fallas_checks, ("ci",))

            # Una lectura sin checks en rojo ni corridas: `fantasma` falta y nadie lo corre.
            gh.checks = lambda *a, **k: ChecksVerdict(
                sha=sha, terminal=False, green=False, faltan=("fantasma",), fallas=(),
                en_marcha=(),
            )
            e = _barrido(r, gh, time.time())
            self.assertEqual(e.fase_actual.checks_esperando_sha, "")
            self.assertFalse(revision_externa_pendiente(e))

            # La vuelta sin cambios cuenta: el freno se ejecuta, no espera lo que no corre.
            _devolver_el_trabajo(r, "fantasma")
            r.incorporar()
            e = r.fold()
            self.assertEqual(e.sin_progreso, 1)
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertNotEqual(e.estado, "esperando_reintento")


class ChecksEnMarcha(unittest.TestCase):
    """`veredicto.en_marcha`: los requeridos que faltan y están corriendo o en cola."""

    def test_una_corrida_sin_completar_esta_en_marcha(self):
        v = veredicto(
            "s", ("ci",), [{"name": "ci", "status": "in_progress", "conclusion": None}], "",
        )
        self.assertEqual((v.faltan, v.en_marcha), (("ci",), ("ci",)))

    def test_un_estado_legacy_pendiente_esta_en_marcha(self):
        v = veredicto("s", ("ci",), [], "", [{"context": "ci", "state": "pending"}])
        self.assertEqual((v.faltan, v.en_marcha), (("ci",), ("ci",)))

    def test_un_requerido_que_nadie_corre_no_esta_en_marcha(self):
        v = veredicto("s", ("ci",), [], "", [])
        self.assertEqual((v.faltan, v.en_marcha), (("ci",), ()))

    def test_un_requerido_en_rojo_o_terminado_no_esta_en_marcha(self):
        v = veredicto(
            "s", ("ci", "lint"),
            [
                {"name": "ci", "status": "completed", "conclusion": "failure"},
                {"name": "lint", "status": "completed", "conclusion": "success"},
            ],
            "",
        )
        self.assertEqual((v.fallas, v.faltan, v.en_marcha), (("ci",), (), ()))


class UnBloqueanteNuevoEsInformacionNueva(unittest.TestCase):
    def _tras_el_diagnostico_de_f4(self, tmp, clave):
        vuelta, r = _entrega_pr(self, tmp, clave, _GhPorCommit)
        gh = vuelta.gh
        gh.textos = [_comentario(_hallazgo("F4"))]
        e = vuelta("uno")
        self.assertEqual(_ids(e), ["F4"])
        r.incorporar()
        e = r.fold()
        self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
        self.assertEqual(e.sin_progreso, 1)
        return vuelta, r, gh

    def test_f5_y_f6_nuevos_tras_el_diagnostico_vuelven_al_responsable(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, gh = self._tras_el_diagnostico_de_f4(tmp, "fr-5")
            gh.textos = [
                _comentario(_hallazgo("F4")),
                _comentario(_hallazgo("F4"), _hallazgo("F5", "high"), _hallazgo("F6", "medium")),
            ]
            e = vuelta("dos")
            self.assertEqual(_ids(e), ["F4", "F5", "F6"])
            self.assertNotEqual(e.estado, "necesita_usuario")
            self.assertEqual(e.estrategia, "investigar")
            self.assertEqual(e.sin_progreso, 0)
            self.assertEqual(e.vueltas_nuevas, 1)
            self.assertTrue(_lanzaria(e))

    def test_los_mismos_bloqueantes_tras_el_diagnostico_siguen_escalando(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, gh = self._tras_el_diagnostico_de_f4(tmp, "fr-5b")
            r.incorporar()
            e = r.fold()
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertFalse(_lanzaria(e))

    def test_lo_nuevo_y_luego_lo_mismo_recorre_la_escalera_otra_vez(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r, gh = self._tras_el_diagnostico_de_f4(tmp, "fr-5c")
            gh.textos = [
                _comentario(_hallazgo("F4")),
                _comentario(_hallazgo("F4"), _hallazgo("F5", "high")),
            ]
            e = vuelta("dos")
            self.assertEqual(e.estrategia, "investigar")
            r.incorporar()
            e = r.fold()
            self.assertEqual(e.estrategia, "diagnosticar_bloqueo")
            self.assertEqual(e.sin_progreso, 1)
            r.incorporar()
            self.assertEqual(r.fold().estado, "necesita_usuario")

    def test_una_serie_que_siempre_trae_hallazgos_nuevos_tiene_tope_por_fase(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "fr-5d", _GhPorCommit)
            gh = vuelta.gh
            acumulados = [_hallazgo("F0")]
            gh.textos = [_comentario(*acumulados)]
            e = vuelta("v0")
            self.assertEqual(e.estrategia, "investigar")
            for i in range(1, 1 + TOPE_VUELTAS_NUEVAS):
                acumulados = acumulados + [_hallazgo(f"F{i}")]
                gh.textos.append(_comentario(*acumulados))
                e = vuelta(f"v{i}")
                self.assertEqual(e.estrategia, "investigar", i)
                self.assertEqual(e.sin_progreso, 0, i)
                self.assertNotEqual(e.estado, "necesita_usuario", i)
            self.assertEqual(e.vueltas_nuevas, TOPE_VUELTAS_NUEVAS)

            acumulados = acumulados + [_hallazgo("F99")]
            gh.textos.append(_comentario(*acumulados))
            e = vuelta("v-tope")
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertIn("vueltas seguidas", e.motivo)
            self.assertFalse(_lanzaria(e))

    def test_cerrar_un_bloqueante_reinicia_la_serie(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "fr-5e", _GhPorCommit)
            gh = vuelta.gh
            gh.textos = [
                _comentario(_hallazgo("F0")),
                _comentario(_hallazgo("F0"), _hallazgo("F1")),
                _comentario(_hallazgo("F1")),
            ]
            vuelta("v0")
            e = vuelta("v1")
            self.assertEqual(e.vueltas_nuevas, 1)
            e = vuelta("v2")
            self.assertEqual(_ids(e), ["F1"])
            self.assertEqual(e.vueltas_nuevas, 0)
            self.assertEqual(e.sin_progreso, 0)


if __name__ == "__main__":
    unittest.main()
