"""Un error al atender un encargo se queda en ese encargo.

El 1-nov un `shutil.Error` de un encargo subió por `barrer` hasta el `while True` del
supervisor y terminó el proceso de toda la máquina. Estos casos recorren `barrer` y el
bucle real con un fallo inyectado en la atención de un encargo.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import ciclo
from motor.encargos.ciclo import (
    ahora_iso,
    barrer,
    corregir,
    ordenar,
    recibir,
)
from motor.encargos.registro import Registro

TOPE_FALLOS_BARRIDO = getattr(ciclo, "TOPE_FALLOS_BARRIDO", 3)


class Banco:
    def __init__(self, tmp: str):
        self.reg = Registro(Path(tmp) / "base")
        self.hora = ahora_iso()
        self.malo = recibir(self.reg, "encargo que falla", "malo", self.hora)["encargo"]
        self.bueno = recibir(self.reg, "encargo sano", "bueno", self.hora)["encargo"]
        self.atendidos: list[str] = []
        original = ciclo._mirar_autoridad
        self.rompe = True
        self.error = PermissionError

        def mirar(registro, enc, hora):
            self.atendidos.append(enc.id)
            if enc.id == self.malo and self.rompe:
                if self.error is PermissionError:
                    raise PermissionError(13, "Permission denied", "objects/ab/cd")
                raise self.error(5, "Input/output error", "objects/ab/cd")
            return original(registro, enc, hora)

        self.parche = mock.patch.object(ciclo, "_mirar_autoridad", side_effect=mirar)

    def __enter__(self):
        self.parche.start()
        return self

    def __exit__(self, *_):
        self.parche.stop()
        self.reg.close()

    def vuelta(self):
        return barrer(self.reg, ahora_iso(), time.time(), None)

    def eventos(self, ident: str, kind: str) -> list[dict]:
        return [e for e in self.reg.eventos(ident) if e["kind"] == kind]

    def parados(self, ident: str) -> list[dict]:
        """Los impedimentos del barrido; el host puede sumar otros, como `entorno`."""
        return [
            e for e in self.eventos(ident, "impedimento")
            if e["data"]["clase"].startswith("barrido:")
        ]


class ContencionDelBarrido(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        ciclo._RACHA_BARRIDO.clear()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_un_encargo_que_lanza_no_detiene_a_los_demas(self):
        with Banco(self.tmp) as b:
            informes = b.vuelta()
            self.assertEqual({i["encargo"] for i in informes}, {b.malo, b.bueno})
            self.assertIn(b.bueno, b.atendidos)
            (fallo,) = b.eventos(b.malo, "instrumento_fallo")
            self.assertEqual(fallo["data"]["fuente"], "barrido")
            self.assertEqual(fallo["data"]["clase"], "barrido:PermissionError")
            self.assertIn("Permission denied", fallo["data"]["causa"])
            self.assertEqual(b.eventos(b.bueno, "instrumento_fallo"), [])

    def test_el_mismo_error_no_se_repite_sin_fin(self):
        with Banco(self.tmp) as b:
            for _ in range(TOPE_FALLOS_BARRIDO + 4):
                b.vuelta()
            self.assertEqual(b.atendidos.count(b.malo), TOPE_FALLOS_BARRIDO)
            self.assertEqual(len(b.eventos(b.malo, "instrumento_fallo")), TOPE_FALLOS_BARRIDO)
            (parado,) = b.parados(b.malo)
            self.assertEqual(parado["data"]["clase"], "barrido:PermissionError")
            self.assertFalse(parado["data"]["verificable"])
            self.assertGreater(b.atendidos.count(b.bueno), TOPE_FALLOS_BARRIDO)

    def test_fallos_sueltos_entre_vueltas_sanas_no_aparcan_el_encargo(self):
        with Banco(self.tmp) as b:
            for _ in range(TOPE_FALLOS_BARRIDO + 2):
                b.rompe = True
                b.vuelta()
                b.rompe = False
                b.vuelta()
                b.vuelta()
            self.assertEqual(len(b.eventos(b.malo, "instrumento_fallo")), TOPE_FALLOS_BARRIDO + 2)
            self.assertEqual(b.parados(b.malo), [])

    def test_errores_de_clases_distintas_no_suman_una_racha(self):
        with Banco(self.tmp) as b:
            for error in (PermissionError, OSError, PermissionError, OSError):
                b.error = error
                b.vuelta()
            self.assertEqual(b.parados(b.malo), [])
            b.error = OSError
            b.vuelta()
            b.vuelta()
            (parado,) = b.parados(b.malo)
            self.assertEqual(parado["data"]["clase"], "barrido:OSError")

    def test_un_ajuste_del_operador_lo_vuelve_a_atender(self):
        with Banco(self.tmp) as b:
            for _ in range(TOPE_FALLOS_BARRIDO + 1):
                b.vuelta()
            antes = b.atendidos.count(b.malo)
            b.rompe = False
            corregir(b.reg, b.malo, "ya se arregló el permiso", "ajuste-1", ahora_iso())
            self.assertEqual(b.reg.fold(b.malo).impedimentos, ())
            b.vuelta()
            self.assertEqual(b.atendidos.count(b.malo), antes + 1)

    def test_tras_un_ajuste_la_cuenta_vuelve_a_empezar(self):
        with Banco(self.tmp) as b:
            for _ in range(TOPE_FALLOS_BARRIDO):
                b.vuelta()
            corregir(b.reg, b.malo, "probemos otra vez", "ajuste-1", ahora_iso())
            b.vuelta()
            self.assertEqual(len(b.parados(b.malo)), 1)
            for _ in range(TOPE_FALLOS_BARRIDO):
                b.vuelta()
            self.assertEqual(len(b.parados(b.malo)), 2)

    def test_un_encargo_aparcado_se_puede_cancelar(self):
        with Banco(self.tmp) as b:
            for _ in range(TOPE_FALLOS_BARRIDO):
                b.vuelta()
            antes = len(b.reg.eventos(b.malo))
            ordenar(b.reg, b.malo, "cancelar", "cancelar-1", ahora_iso())
            b.vuelta()
            b.vuelta()
            self.assertEqual(b.reg.fold(b.malo).estado in ("deteniendo", "cancelado"), True)
            nuevos = b.reg.eventos(b.malo)[antes:]
            self.assertEqual([e for e in nuevos if e["kind"] == "instrumento_fallo"], [])


class BucleDelSupervisor(unittest.TestCase):
    def test_un_paso_que_falla_no_termina_el_proceso_ni_cuenta_como_avance(self):
        from motor.encargos import cli

        with tempfile.TemporaryDirectory() as tmp:
            reg = Registro(Path(tmp) / "base")
            args = argparse.Namespace(
                base=reg.base, una_vez=False, intervalo=0.0, umbral=30.0,
                agente="sleep", respaldo=[],
            )
            senal = mock.MagicMock()
            with mock.patch("motor.senal.Senal", return_value=senal), mock.patch.object(
                cli, "barrer", side_effect=[RuntimeError("se cayó el paso"), [], KeyboardInterrupt()]
            ) as paso, mock.patch.object(cli.time, "sleep"):
                with self.assertRaises(KeyboardInterrupt):
                    cli._supervisor(reg, args)
            self.assertEqual(paso.call_count, 3)
            senal.avanzo.assert_called_once()
            senal.para.assert_called_once()
            reg.close()


if __name__ == "__main__":
    unittest.main()
