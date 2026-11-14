"""Una pasada que vuelve a observar un hecho de la entrega desde otro intento no
es una escritura rechazada: el hecho ya está guardado y `intento` solo dice quién
lo observó."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr
from datetime import datetime
from pathlib import Path
from unittest import mock

from motor.encargos import registro as modulo_registro
from motor.encargos.ciclo import _head_desviado, ahora_iso, barrer, recibir
from motor.encargos.registro import Registro
from tests.test_entrega_merge_pendiente import _politica_con_checks
from tests.test_flujo_revisiones import _entrega_pr, sonda_sin_aislamiento_del_host


def setUpModule():
    sonda_sin_aislamiento_del_host()


def _otro_turno(caso: unittest.TestCase, reg: Registro, ident: str, nombre: str) -> None:
    """El supervisor corrió un turno entre dos pasadas: el encargo sigue con
    otro intento vigente."""
    abierto = {"id": nombre, "pid": 1, "inicio": "ensayo", "host": "pc"}
    caso.assertEqual(
        reg.anotar(ident, {"kind": "intento_abierto", "data": abierto}, f"abierto-{nombre}"),
        "nuevo",
    )
    caso.assertEqual(
        reg.anotar(ident, {"kind": "intento_cerrado", "data": {}}, f"cerrado-{nombre}"), "nuevo"
    )
    caso.assertEqual(reg.fold(ident).intento, nombre)


@contextmanager
def _rechazos():
    errores = io.StringIO()
    with mock.patch.object(modulo_registro, "_CONTEO", modulo_registro._Conteo()), \
            redirect_stderr(errores):
        yield errores


def _pasos(reg: Registro, ident: str, paso: str) -> list[dict]:
    return [
        ev for ev in reg.eventos(ident)
        if ev["kind"] == "entrega_externa" and ev["data"].get("paso") == paso
    ]


class ElHeadDesviadoDesdeOtroIntento(unittest.TestCase):
    def test_el_aviso_repetido_tras_la_devolucion_no_es_un_rechazo(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            self.addCleanup(reg.close)
            hora = ahora_iso()
            ident = recibir(reg, "publica el PR", "head-otro-intento", hora)["encargo"]
            reg.anotar(
                ident,
                {"kind": "fases", "data": {"fases": [{"id": "a", "resultado": "algo concreto"}]}},
                "fases-ensayo",
            )
            _otro_turno(self, reg, ident, "intento-revisor")

            with _rechazos() as errores:
                e = _head_desviado(reg, reg.fold(ident), hora, 7, "a" * 40, "f" * 40)
                self.assertEqual(e.rol, "responsable")
                _otro_turno(self, reg, ident, "intento-responsable")
                e = _head_desviado(reg, reg.fold(ident), hora, 7, "a" * 40, "f" * 40)

            self.assertNotIn("rechazo=", errores.getvalue())
            self.assertEqual(e.estado, "esperando_reintento")
            self.assertIn("head", e.motivo)
            self.assertEqual(len(_pasos(reg, ident, "head_vigente")), 1)


class LaPasadaDeEntregaDesdeOtroIntento(unittest.TestCase):
    def test_el_commit_y_el_push_ya_guardados_no_son_un_rechazo_y_la_fase_integra(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory(ignore_cleanup_errors=True))
        vuelta, r = _entrega_pr(
            self, tmp, "entrega-otro-intento", _politica_con_checks, nivel="integrado"
        )
        e = vuelta("hola")
        self.assertEqual(e.estado, "esperando_reintento")
        commits = _pasos(r.reg, r.enc, "commit")
        self.assertEqual(len(commits), 1)
        _otro_turno(self, r.reg, r.enc, "intento-siguiente")
        vuelta.gh.bloquea = False
        vuelta.gh.api.requeridos = [{"name": "ci", "bucket": "pass", "state": "SUCCESS"}]
        epoch = datetime.fromisoformat(e.reanuda_en.replace("Z", "+00:00")).timestamp()

        with _rechazos() as errores:
            barrer(r.reg, e.reanuda_en, epoch, None, gh=vuelta.gh)

        self.assertNotIn("rechazo=", errores.getvalue())
        final = r.fold()
        self.assertEqual(final.estado, "terminado")
        self.assertEqual(final.nivel_alcanzado, "integrado")
        self.assertEqual(_pasos(r.reg, r.enc, "commit"), commits)
        self.assertEqual(len(_pasos(r.reg, r.enc, "push")), 1)


if __name__ == "__main__":
    unittest.main()
