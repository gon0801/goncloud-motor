"""Las entradas de reapertura conservan autoridad y firma del cierre."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor import __main__ as motor_cli
from motor.encargos import ciclo, cli
from motor.encargos.registro import Registro
from motor.integraciones import directores


class CliReabrir(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name) / "base"
        self.reg = Registro(self.base)
        self.addCleanup(self.reg.close)
        self.encargo = ciclo.recibir(
            self.reg, "recuperar la entrega", "pedido", ciclo.ahora_iso(),
            canal="hermes",
        )["encargo"]
        self.ordenes = []
        self.enterContext(mock.patch.object(cli, "ordenar", self.ordenar))
        self.enterContext(mock.patch.object(directores, "ordenar", self.ordenar))
        self.enterContext(mock.patch.dict(
            os.environ, {"MOTOR_ENCARGO_BASE": str(self.base)},
        ))

    def ordenar(self, reg, encargo, accion, clave, hora, autor="", *, fase="", cierre=""):
        self.ordenes.append({
            "encargo": encargo, "accion": accion, "clave": clave,
            "autor": autor, "fase": fase, "cierre": cierre,
        })
        return {"encargo": encargo, "estado": "detenido"}

    def correr(self, argv, mensaje=None):
        with (
            contextlib.redirect_stdout(io.StringIO()) as salida,
            contextlib.redirect_stderr(io.StringIO()),
            mock.patch("sys.stdin", io.StringIO(json.dumps(mensaje or {}))),
        ):
            try:
                codigo = motor_cli.main(argv)
            except SystemExit as exc:
                codigo = exc.code
        texto = salida.getvalue().strip()
        return codigo, json.loads(texto) if texto else {}

    def argumentos(self, *extra):
        return [
            "reabrir", self.encargo, "--base", str(self.base),
            "--clave", "recuperacion", *extra,
        ]

    def test_motor_reabrir_permite_inferir_el_cierre_local(self):
        codigo, salida = self.correr(self.argumentos())

        self.assertEqual(codigo, 0)
        self.assertEqual(salida["estado"], "detenido")
        self.assertEqual(self.ordenes, [{
            "encargo": self.encargo, "accion": "reabrir", "clave": "recuperacion",
            "autor": "", "fase": "", "cierre": "",
        }])

    def test_el_director_local_conserva_la_identidad_del_operador(self):
        codigo, salida = self.correr(["director", *self.argumentos()])

        self.assertEqual(codigo, 0)
        self.assertEqual(salida["estado"], "detenido")
        self.assertEqual(self.ordenes, [{
            "encargo": self.encargo, "accion": "reabrir", "clave": "recuperacion",
            "autor": "director:operador", "fase": "", "cierre": "",
        }])

    def test_un_director_autorizado_pasa_la_firma_exacta(self):
        codigo, _ = self.correr(["director", *self.argumentos(
            "--canal", "claw", "--fase", "motor-despliegue", "--cierre", "firma-1",
        )])

        self.assertEqual(codigo, 0)
        self.assertEqual(self.ordenes, [{
            "encargo": self.encargo, "accion": "reabrir", "clave": "recuperacion",
            "autor": "director:claw", "fase": "motor-despliegue", "cierre": "firma-1",
        }])

    def test_un_canal_ajeno_no_puede_reabrir(self):
        codigo, salida = self.correr([
            "director", *self.argumentos("--canal", "desconocido"),
        ])

        self.assertEqual(codigo, 2)
        self.assertTrue(salida.get("rechazada"), salida)
        self.assertIn("canal", salida["motivo"])
        self.assertEqual(self.ordenes, [])

    def test_el_transporte_rechaza_reaperturas_sin_firma_completa(self):
        for firma in (
            {}, {"fase": "motor-despliegue"}, {"cierre": "firma-1"},
            {"fase": "", "cierre": "firma-1"},
            {"fase": "motor-despliegue", "cierre": ""},
            {"fase": ["motor-despliegue"], "cierre": "firma-1"},
            {"fase": "motor-despliegue", "cierre": 1},
        ):
            with self.subTest(firma=firma):
                codigo, salida = self.correr(
                    ["encargos-aplicar", "--base", str(self.base)],
                    {"op": "reabrir", "encargo": self.encargo, "clave": "remota", **firma},
                )
                self.assertEqual(codigo, 2)
                self.assertIn("fase", salida.get("error", ""))
                self.assertIn("cierre", salida.get("error", ""))
                self.assertEqual(self.ordenes, [])

    def test_el_transporte_delega_la_firma_sin_inferir_otro_cierre(self):
        codigo, salida = self.correr(
            ["encargos-aplicar", "--base", str(self.base)],
            {
                "op": "reabrir", "encargo": self.encargo, "clave": "remota",
                "fase": "motor-despliegue", "cierre": "firma-remota",
            },
        )

        self.assertEqual(codigo, 0)
        self.assertEqual(salida["estado"], "detenido")
        self.assertEqual(self.ordenes, [{
            "encargo": self.encargo, "accion": "reabrir", "clave": "remota",
            "autor": "", "fase": "motor-despliegue", "cierre": "firma-remota",
        }])


if __name__ == "__main__":
    unittest.main()
