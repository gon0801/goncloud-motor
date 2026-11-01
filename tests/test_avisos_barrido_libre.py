"""Los avisos no frenan el barrido: tope por pasada, y un hilo aparte para que la vuelta no espere al canal."""

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos.registro import Registro
from motor.integraciones import directores

HORA = "2026-11-01T10:00:00Z"

TRANSPORTE = (
    "import os,sys,json,time\n"
    "from pathlib import Path\n"
    "base=Path(os.environ['MOTOR_AVISO_BASE'])\n"
    "sys.stdin.read()\n"
    "time.sleep(float((base/'demora').read_text()))\n"
    "with open(base/'entregados.log','a') as f: f.write(os.environ['MOTOR_AVISO_ID']+'\\n')\n"
    "print(json.dumps({'recibo':'ok-'+os.environ['MOTOR_AVISO_ID']}))\n"
)


class _Alto(BaseException):
    pass


class Banco:
    def __init__(self, tmp: str, demora: float):
        self.base = Path(tmp) / "base"
        self.reg = Registro(self.base)
        script = Path(tmp) / "transporte.py"
        script.write_text(TRANSPORTE, encoding="utf-8")
        (self.base / "canal.json").write_text(
            json.dumps({"tg": {"comando": [sys.executable, "-B", str(script)], "idempotente": True}}),
            encoding="utf-8",
        )
        (self.base / "demora").write_text(str(demora))

    def encargo(self, clave: str) -> str:
        ident = directores.encargar(
            self.reg, f"pedido {clave}", clave, HORA, "hermes", "tg", "directa"
        )["encargo"]
        self.reg.anotar(ident, {"kind": "fases", "data": {
            "fases": [{"id": "A", "resultado": "fase comprobada"}], "hora": HORA,
        }}, f"fase-{clave}")
        return ident

    def entregados(self) -> list[str]:
        log = self.base / "entregados.log"
        return log.read_text().split() if log.exists() else []


class Base(unittest.TestCase):
    demora = 0.0

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.b = Banco(self._tmp.name, self.demora)
        self.addCleanup(self.b.reg.close)


class TopeDeLaPasada(Base):
    demora = 8.0

    def test_un_transporte_lento_no_alarga_la_pasada_mas_alla_del_tope(self):
        self.b.encargo("uno")
        inicio = time.monotonic()
        salida = directores.drenar(self.b.reg, tope_s=1.0)
        duracion = time.monotonic() - inicio
        self.assertLess(duracion, 5.0, "sin tope esperaría los 8 s del transporte")
        aviso = salida["avisos"][0]
        self.assertFalse(aviso["entregado"])
        self.assertEqual(aviso["estado"], "incierto")
        self.assertIn("timed out", aviso["motivo"])
        self.assertIn("proximo_intento", aviso)


class PasadaConTope(Base):
    demora = 0.3

    def test_pasado_el_tope_no_se_empiezan_avisos_nuevos_y_el_resto_sale_en_la_pasada_siguiente(self):
        for n in range(5):
            self.b.encargo(f"e{n}")
        primera = directores.drenar(self.b.reg, tope_s=0.5)
        enviados = len(self.b.entregados())
        self.assertTrue(primera["cortada_por_tope"])
        self.assertGreaterEqual(enviados, 1)
        self.assertLess(enviados, 5)
        segunda = directores.drenar(self.b.reg, ahora=time.time() + 10_000)
        self.assertFalse(segunda["cortada_por_tope"])
        entregados = self.b.entregados()
        self.assertEqual(len(entregados), 5)
        self.assertEqual(len(set(entregados)), 5, "ningún aviso se manda dos veces")


class SinTope(Base):
    def test_sin_tope_la_pasada_no_se_corta(self):
        self.b.encargo("uno")
        salida = directores.drenar(self.b.reg)
        self.assertFalse(salida["cortada_por_tope"])
        self.assertTrue(salida["avisos"][0]["entregado"])


class HiloAparte(Base):
    demora = 2.0

    def test_lanzar_no_espera_al_canal_y_hay_una_sola_pasada_viva(self):
        self.b.encargo("uno")
        drenador = directores.DrenadoAparte(self.b.base, tope_s=10.0)
        inicio = time.monotonic()
        self.assertTrue(drenador.lanzar())
        self.assertLess(time.monotonic() - inicio, 1.0, "lanzar vuelve de inmediato")
        self.assertFalse(drenador.lanzar(), "la pasada anterior sigue viva")
        drenador.esperar(20)
        self.assertEqual(drenador.ultimo_error, "")
        self.assertEqual(len(self.b.entregados()), 1)
        self.assertTrue(drenador.lanzar(), "terminada la pasada se puede lanzar otra")
        drenador.esperar(20)

    def test_la_vuelta_del_supervisor_no_depende_del_canal_ni_cuando_funciona(self):
        from motor.encargos.cli import main

        self.b.encargo("uno")
        (self.b.base / "demora").write_text("4")
        pausas = []

        def dormir(segundos):
            pausas.append(segundos)
            if len(pausas) >= 3:
                raise _Alto

        inicio = time.monotonic()
        with mock.patch("motor.encargos.cli.barrer", return_value=[]), mock.patch(
            "time.sleep", side_effect=dormir
        ):
            with self.assertRaises(_Alto):
                main(["supervisor", "--base", str(self.b.base), "--agente", "sleep", "--intervalo", "0.01"])
        duracion = time.monotonic() - inicio
        for hilo in threading.enumerate():
            if hilo.name == "drenado-avisos":
                hilo.join(20)
        self.assertLess(duracion, 3.0, "tres vueltas sin esperar los 4 s del transporte")
        self.assertEqual(len(self.b.entregados()), 1, "el aviso salió igual, desde el hilo")


if __name__ == "__main__":
    unittest.main()
