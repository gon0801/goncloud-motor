"""La misma falla de git en la entrega no se reintenta en bucle.

Si el commit o el push de la misma versión aprobada falla dos veces seguidas
por la misma causa, el trabajo vuelve al responsable con la causa exacta. Una
falla de red o transitoria sigue esperando su reintento.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from unittest import mock

from motor.encargos import ciclo, resumen
from tests.test_flujo_revisiones import (
    _entrega_pr,
    _GhConDeepSeek,
    _lanzaria,
    sonda_sin_aislamiento_del_host,
)


def setUpModule():
    sonda_sin_aislamiento_del_host()


def _epoch(hora: str) -> float:
    return datetime.fromisoformat(hora.replace("Z", "+00:00")).timestamp()


def _barrido(r, gh, t: float):
    ciclo.barrer(r.reg, ciclo.ahora_iso(t), t, None, gh=gh)
    return r.fold()


class FalloRepetidoDeEntrega(unittest.TestCase):
    def test_la_misma_falla_de_git_dos_veces_devuelve_al_responsable(self):
        causa = "git push fallo: remote rejected: protected branch hook declined"
        llamadas = []

        def falla(enc, espacio):
            llamadas.append(enc.estado)
            return ("", causa)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "fallo-git-2", _GhConDeepSeek)
            with mock.patch.object(ciclo, "_commit_push", side_effect=falla):
                e1 = vuelta("hola")
                self.assertEqual(e1.estado, "esperando_reintento")
                self.assertTrue(e1.reanuda_en)
                self.assertEqual(e1.preguntas, ())
                self.assertEqual(len(r.eventos("espera")), 1)

                e2 = _barrido(r, vuelta.gh, _epoch(e1.reanuda_en))
                self.assertEqual(len(llamadas), 2)

                e3 = _barrido(r, vuelta.gh, _epoch(e1.reanuda_en) + 200)

            self.assertNotEqual(e2.estado, "esperando_reintento")
            self.assertNotEqual(e2.estado, "necesita_usuario")
            self.assertEqual(e2.estrategia, "investigar")
            self.assertIn("git push fallo", e2.motivo)
            self.assertIn("protected branch hook declined", e2.motivo)
            self.assertEqual(e2.preguntas, ())
            self.assertEqual(len(r.eventos("espera")), 1)
            self.assertFalse(
                any(ev["kind"] == "pregunta" for ev in r.reg.eventos(r.enc))
            )
            self.assertTrue(ciclo._aplica_entrega(e2))
            self.assertTrue(_lanzaria(e2))
            self.assertEqual(len(r.eventos("entrega_devuelta")), 1)
            self.assertEqual(len(r.eventos("nota_pr")), 1)
            self.assertIn(
                "el motor dejo de reintentar",
                r.eventos("nota_pr")[0]["data"]["texto"],
            )

            self.assertEqual(len(llamadas), 2)
            self.assertEqual(len(r.eventos("espera")), 1)
            self.assertEqual(e3.estado, "recibido")
            self.assertEqual(e3.estrategia, "investigar")

    def test_el_aviso_dice_que_el_motor_dejo_de_reintentar(self):
        causa = "git push fallo: remote rejected: protected branch hook declined"
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "fallo-git-aviso", _GhConDeepSeek)
            with mock.patch.object(ciclo, "_commit_push", return_value=("", causa)):
                e1 = vuelta("hola")
                eventos_e1 = list(r.reg.eventos(r.enc))
                e2 = _barrido(r, vuelta.gh, _epoch(e1.reanuda_en))

            alertas_antes = resumen._alertas(
                e1, eventos_e1, _epoch(e1.reanuda_en) - 1
            )
            self.assertFalse(
                any("dejo de reintentar" in alerta for alerta in alertas_antes),
                alertas_antes,
            )
            alertas = resumen._alertas(
                e2, r.reg.eventos(r.enc), _epoch(e1.reanuda_en) + 200
            )
            self.assertTrue(
                any(
                    "dejo de reintentar" in alerta
                    and "vuelve al responsable" in alerta
                    for alerta in alertas
                ),
                alertas,
            )
            self.assertIn("protected branch hook declined", " ".join(alertas))
            self.assertEqual(resumen._decisiones_de(e2), [])

    def test_el_aviso_con_causa_larga_dice_el_corte(self):
        causa = (
            "git push fallo: remote rejected: protected branch hook declined "
            "by repository policy at https://github.com/gon0801/goncloud-motor"
        )
        self.assertEqual(len(causa), 129)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "fallo-git-causa", _GhConDeepSeek)
            with mock.patch.object(ciclo, "_commit_push", return_value=("", causa)):
                e1 = vuelta("hola")
                e2 = _barrido(r, vuelta.gh, _epoch(e1.reanuda_en))

            alertas = resumen._alertas(
                e2, r.reg.eventos(r.enc), _epoch(e1.reanuda_en) + 200
            )
            alerta = next(
                a for a in alertas if "fallo de instrumento" in a
            )
            self.assertIn("git push fallo: remote rejected", alerta)
            self.assertIn("dejo de reintentar", alerta)
            self.assertIn("vuelve al responsable", alerta)

    def test_corregir_solo_el_repositorio_vuelve_a_entregar(self):
        causa = "git push fallo: remote rejected: protected branch hook declined"
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "fallo-git-repo", _GhConDeepSeek)
            with mock.patch.object(ciclo, "_commit_push", return_value=("", causa)):
                e1 = vuelta("hola")
                e2 = _barrido(r, vuelta.gh, _epoch(e1.reanuda_en))
            self.assertEqual(e2.estrategia, "investigar")
            firma = e2.producto.firma

            r.incorporar()
            tras_turno = r.fold()
            self.assertGreater(tras_turno.generacion, e2.generacion)
            self.assertEqual(tras_turno.producto.firma, firma)

            e3 = _barrido(r, vuelta.gh, _epoch(e1.reanuda_en) + 1)

            self.assertNotEqual(e3.estado, "esperando_reintento")
            self.assertEqual(e3.producto.firma, firma)
            self.assertTrue(e3.producto.sha_git)
            self.assertIn(e3.nivel_alcanzado, ("pr", "pr_abierto"))
            self.assertEqual(len(r.eventos("espera")), 1)

    def test_dos_clases_distintas_no_pierden_el_segundo_fallo(self):
        causa_add = "git add fallo: path is ignored"
        causa_push = "git push fallo: remote rejected"
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "fallo-dos-clases", _GhConDeepSeek)
            with mock.patch.object(
                ciclo,
                "_commit_push",
                side_effect=[("", causa_add), ("", causa_push)],
            ):
                e1 = vuelta("hola")
                self.assertEqual(e1.estado, "esperando_reintento")
                e2 = _barrido(r, vuelta.gh, _epoch(e1.reanuda_en))

            causas = [
                ev["data"]["causa"] for ev in r.eventos("instrumento_fallo")
            ]
            self.assertEqual(causas, [causa_add, causa_push])
            self.assertEqual(e2.estado, "esperando_reintento")
            self.assertEqual(len(r.eventos("espera")), 2)

    def test_una_falla_transitoria_sigue_reintentando(self):
        causa = (
            "git push fallo: fatal: unable to access 'https://github.com/o/r.git/': "
            "Could not resolve host: github.com"
        )
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "fallo-git-red", _GhConDeepSeek)
            with mock.patch.object(ciclo, "_commit_push", return_value=("", causa)):
                e1 = vuelta("hola")
                self.assertEqual(e1.estado, "esperando_reintento")

                with mock.patch.object(
                    ciclo, "_reanuda_en", return_value="2026-11-06T09:00:00Z"
                ):
                    e2 = _barrido(r, vuelta.gh, _epoch(e1.reanuda_en))

            self.assertEqual(e2.estado, "esperando_reintento")
            self.assertTrue(e2.reanuda_en)
            self.assertIn("Could not resolve host", e2.motivo)
            self.assertEqual(len(r.eventos("espera")), 2)
            self.assertFalse(_lanzaria(e2))


class ClasificacionDeFallas(unittest.TestCase):
    def test_un_rechazo_de_permisos_no_se_confunde_con_red(self):
        for causa in (
            "git push fallo: error: RPC failed; HTTP 403 from remote",
            "git push fallo: fatal: unable to access 'https://github.com/o/r.git/': "
            "The requested URL returned error: 403",
            "git push fallo: error: RPC failed; HTTP 413 from remote",
            "git push fallo: fatal: unable to access 'https://github.com/o/r.git/': "
            "The requested URL returned error: 413",
            "git push fallo: Permission denied (publickey)",
        ):
            self.assertFalse(ciclo._fallo_transitorio(causa), causa)
        for causa in (
            "git push fallo: fatal: unable to access 'https://github.com/o/r.git/': "
            "Could not resolve host: github.com",
            "git push fallo: error: RPC failed; curl 56 GnuTLS recv error: "
            "The remote end hung up unexpectedly",
            "git push fallo: error: RPC failed; curl 92 HTTP/2 stream 0 was "
            "not closed cleanly",
        ):
            self.assertTrue(ciclo._fallo_transitorio(causa), causa)


if __name__ == "__main__":
    unittest.main()
