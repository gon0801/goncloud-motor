"""Un encargo abierto que no espera con hora, no esta detenido y no tiene nada
en curso no puede quedarse quieto sin decirlo: tras varias vueltas seguidas sin
evento deja `quietud` con la causa y pasa a necesita_usuario."""

from __future__ import annotations

import tempfile
import time
import unittest
from unittest import mock

from motor.encargos.ciclo import (
    _QUIETUD_S,
    _QUIETUD_VUELTAS,
    _aplica_entrega,
    _paso_entrega,
    ahora_iso,
    barrer,
    marcar_espera,
)
from tests.test_flujo_revisiones import (
    Recorrido,
    _entrega_pr,
    _GhConDeepSeek,
    _lanzaria,
    sonda_sin_aislamiento_del_host,
)

PASO_S = 30


def setUpModule():
    sonda_sin_aislamiento_del_host()


def _barrido(r, gh, t):
    barrer(r.reg, ahora_iso(t), t, None, gh=gh)
    return r.fold()


def _barrido_quieto(r, gh, t):
    """La entrega ya vuelve a lanzar cuando su paso no tiene efecto; para probar
    el detector de quietud se deja ese lanzamiento fuera de la decision."""
    with mock.patch(
        "motor.encargos.ciclo._decision",
        return_value={"matar": False, "lanzar": False, "eventos": []},
    ):
        return _barrido(r, gh, t)


def _quieto_como_en_el_incidente(caso, tmp):
    """DeepSeek dejo un bloqueante sobre el commit y el producto volvio sin
    sha_git, como lo anotaba el codigo anterior: `_aplica_entrega` da verdadero,
    la entrega no anota nada nuevo y ningun agente se lanza."""
    vuelta, r = _entrega_pr(caso, tmp, "qt-inc", _GhConDeepSeek)
    e = vuelta("uno")
    manifiesto = [list(x) for x in e.producto.manifiesto]
    r.reg.anotar(
        r.enc,
        {
            "kind": "producto_listo",
            "data": {
                "version": e.producto.firma,
                "manifiesto": manifiesto,
                "fase": e.fase_activa,
                "intento": e.intento,
                "hora": ahora_iso(),
            },
        },
        "t-producto-sin-sha",
    )
    e = r.fold()
    caso.assertEqual(e.producto.sha_git, "")
    caso.assertTrue(_aplica_entrega(e))
    antes = len(r.reg.eventos(r.enc))
    _paso_entrega(r.reg, e, vuelta.gh, ahora_iso())
    caso.assertEqual(len(r.reg.eventos(r.enc)), antes, "la entrega no anota nada")
    return vuelta, r


class QuietudDelEncargo(unittest.TestCase):
    def test_el_incidente_termina_en_necesita_usuario_con_la_causa(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _quieto_como_en_el_incidente(self, tmp)
            t = time.time()
            e = _barrido_quieto(r, vuelta.gh, t)
            self.assertNotEqual(e.estado, "necesita_usuario")
            for i in range(1, _QUIETUD_S // PASO_S):
                e = _barrido_quieto(r, vuelta.gh, t + i * PASO_S)
                self.assertNotEqual(e.estado, "necesita_usuario", f"vuelta {i}")
            self.assertEqual(r.eventos("quietud"), [])

            e = _barrido_quieto(r, vuelta.gh, t + _QUIETUD_S)
            self.assertEqual(e.estado, "necesita_usuario")
            [evento] = r.eventos("quietud")
            causa = evento["data"]["causa"]
            self.assertIn("sin ningun evento nuevo", causa)
            self.assertIn("sha_git vacio", causa)
            self.assertIn("la entrega se considera pendiente: si", causa)
            self.assertIn("Se intento", causa)
            self.assertEqual(e.motivo, causa)
            self.assertIn(causa, e.preguntas)
            self.assertGreaterEqual(evento["data"]["vueltas"], _QUIETUD_VUELTAS)

            total = len(r.reg.eventos(r.enc))
            _barrido_quieto(r, vuelta.gh, t + _QUIETUD_S + PASO_S)
            self.assertEqual(len(r.reg.eventos(r.enc)), total)
            self.assertFalse(_lanzaria(r.fold()))

    def test_un_evento_nuevo_reinicia_la_cuenta(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _quieto_como_en_el_incidente(self, tmp)
            t = time.time()
            for i in range(0, _QUIETUD_S - PASO_S, PASO_S):
                _barrido_quieto(r, vuelta.gh, t + i)
            r.reg.anotar(
                r.enc,
                {"kind": "procedimiento", "data": {"nota": "algo cambio", "hora": ahora_iso()}},
                "t-evento-intermedio",
            )
            reinicio = t + _QUIETUD_S - PASO_S
            e = _barrido_quieto(r, vuelta.gh, reinicio)
            e = _barrido_quieto(r, vuelta.gh, reinicio + _QUIETUD_S - PASO_S)
            self.assertNotEqual(e.estado, "necesita_usuario")
            self.assertEqual(r.eventos("quietud"), [])
            e = _barrido_quieto(r, vuelta.gh, reinicio + _QUIETUD_S)
            self.assertEqual(e.estado, "necesita_usuario")
            self.assertEqual(len(r.eventos("quietud")), 1)

    def test_un_hueco_largo_entre_vueltas_no_cuenta_como_vueltas_seguidas(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _quieto_como_en_el_incidente(self, tmp)
            t = time.time()
            _barrido(r, vuelta.gh, t)
            _barrido(r, vuelta.gh, t + PASO_S)
            e = _barrido(r, vuelta.gh, t + 5 * _QUIETUD_S)
            self.assertNotEqual(e.estado, "necesita_usuario")
            self.assertEqual(r.eventos("quietud"), [])

    def test_una_espera_con_hora_no_es_quietud(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _entrega_pr(self, tmp, "qt-espera", _GhConDeepSeek)
            vuelta("uno")
            t = time.time()
            hora = ahora_iso(t)
            marcar_espera(r.reg, r.enc, "GitHub caido", ahora_iso(t + 10 * _QUIETUD_S), hora)
            for i in range(0, 3 * _QUIETUD_S, PASO_S):
                e = _barrido(r, vuelta.gh, t + i)
            self.assertEqual(e.estado, "esperando_reintento")
            self.assertEqual(r.eventos("quietud"), [])

    def test_con_lanzamiento_pendiente_no_es_quietud(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "qt-lanza", "agrega saludo.txt con hola")
            self.addCleanup(r.reg.close)
            self.assertTrue(_lanzaria(r.fold()))
            t = time.time()
            for i in range(0, 3 * _QUIETUD_S, PASO_S):
                e = _barrido(r, None, t + i)
            self.assertNotEqual(e.estado, "necesita_usuario")
            self.assertEqual(r.eventos("quietud"), [])

    def test_sin_el_detector_el_incidente_sigue_quieto(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vuelta, r = _quieto_como_en_el_incidente(self, tmp)

            t = time.time()
            with mock.patch(
                "motor.encargos.ciclo._vigilar_quietud", side_effect=lambda *a: a[1]
            ):
                for i in range(0, 3 * _QUIETUD_S, PASO_S):
                    e = _barrido_quieto(r, vuelta.gh, t + i)
            self.assertNotEqual(e.estado, "necesita_usuario")
            self.assertEqual(r.eventos("quietud"), [])


if __name__ == "__main__":
    unittest.main()
