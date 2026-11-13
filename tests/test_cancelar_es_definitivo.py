"""Una cancelación del dueño no se convierte en una parada reanudable.

Mientras el encargo se detiene llegan otros hechos (un impedimento, un fallo
del instrumento, una sesión colgada) que cambian lo que se muestra. Ninguno
cambia la orden: el encargo cancelado termina cancelado y no se continúa.
"""

from __future__ import annotations

import unittest

from motor.encargos.dominio import Encargo, TransicionIlegal, reducir


def _ev(kind: str, **data) -> dict:
    return {"kind": kind, "data": data}


_IMPEDIMENTO = _ev(
    "impedimento", clase="instrumento:x", causa="pytest roto", condicion="pytest corre"
)

_INTERCALADOS = {
    "impedimento": [_IMPEDIMENTO],
    "instrumento_fallo": [
        _ev("instrumento_fallo", causa="lector falló", fuente="barrido")
    ],
    "colgada": [_ev("colgada", hecho="sin actividad", causa="x")],
    "impedimento_resuelto": [
        _IMPEDIMENTO,
        _ev("impedimento_resuelto", clase="instrumento:x", evidencia="ok"),
    ],
}


def _plegar(eventos: list[dict]) -> Encargo:
    estado = None
    for evento in eventos:
        estado = reducir(estado, evento)
    assert estado is not None
    return estado


def _entrega(**extra) -> dict:
    return _ev(
        "entrega", id="enc_x", clave="k", huella="h", texto="haz algo", hora="t0", **extra
    )


def _con_intento_y_orden(accion: str, **entrega) -> list[dict]:
    return [
        _entrega(**entrega),
        _ev("reserva", host="hostA", rol="responsable", hora="t"),
        _ev("intento_abierto", id="A", pid=1, inicio="x", host="hostA", rol="responsable"),
        _ev("orden", accion=accion),
    ]


_CIERRE = [_ev("intento_cerrado"), _ev("arbol", observacion="ausente")]


class CancelarEsDefinitivo(unittest.TestCase):
    def test_un_hecho_durante_la_detencion_no_vuelve_reanudable_la_cancelacion(self):
        for nombre, intercalados in _INTERCALADOS.items():
            with self.subTest(intercalado=nombre):
                e = _plegar(_con_intento_y_orden("cancelar") + intercalados + _CIERRE)
                self.assertEqual(e.estado, "cancelado")
                with self.assertRaises(TransicionIlegal):
                    reducir(e, _ev("orden", accion="continuar"))

    def test_una_parada_con_los_mismos_hechos_sigue_reanudable(self):  # no-roja-en-base: control; la parada ya era reanudable y debe seguirlo
        for nombre, intercalados in _INTERCALADOS.items():
            with self.subTest(intercalado=nombre):
                e = _plegar(_con_intento_y_orden("parar") + intercalados + _CIERRE)
                self.assertEqual(e.estado, "detenido")
                e = reducir(e, _ev("orden", accion="continuar"))
                self.assertEqual(e.estado, "recibido")
                self.assertTrue(e.intentos_nuevos)

    def test_parar_no_rebaja_una_cancelacion_en_curso(self):
        e = _plegar(_con_intento_y_orden("cancelar"))
        with self.assertRaises(TransicionIlegal):
            reducir(e, _ev("orden", accion="parar"))

    def test_un_resultado_directo_no_termina_lo_que_se_esta_cancelando(self):
        e = _plegar(
            _con_intento_y_orden("cancelar", via="directa") + [_ev("intento_cerrado")]
        )
        with self.assertRaises(TransicionIlegal):
            reducir(e, _ev("resultado_directo", evidencia="hecho"))

    def test_un_resultado_directo_termina_un_encargo_sin_cancelar(self):  # no-roja-en-base: control; la via directa ya cerraba con su resultado
        e = reducir(
            _plegar([_entrega(via="directa")]),
            _ev("resultado_directo", evidencia="hecho"),
        )
        self.assertEqual(e.estado, "terminado")

    def test_una_correccion_no_entra_a_un_encargo_que_se_esta_cancelando(self):
        for nombre, intercalados in _INTERCALADOS.items():
            with self.subTest(intercalado=nombre):
                e = _plegar(_con_intento_y_orden("cancelar") + intercalados)
                with self.assertRaises(TransicionIlegal):
                    reducir(e, _ev("ajuste", texto="otra cosa"))


if __name__ == "__main__":
    unittest.main()
