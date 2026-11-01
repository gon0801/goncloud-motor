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
    "with open(base/'entregados.log','a') as f: f.write(os.environ['MOTOR_AVISO_ID']+'\\n')\n"
    "time.sleep(float((base/'demora').read_text()))\n"
    "if (base/'falla').exists(): sys.stderr.write('responde error'); sys.exit(1)\n"
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
    demora = 3.0

    def test_el_tope_no_mata_un_envio_ya_empezado_ni_duplica_el_mensaje(self):
        self.b.encargo("uno")
        salida = directores.drenar(self.b.reg, tope_s=1.0)
        aviso = salida["avisos"][0]
        self.assertTrue(aviso["entregado"], "el envío empezado termina con su propio timeout")
        self.assertEqual(aviso["estado"], "publicado")
        self.assertNotIn("proximo_intento", aviso)
        self.assertEqual(len(self.b.entregados()), 1)
        directores.drenar(self.b.reg, ahora=time.time() + 120)
        self.assertEqual(len(self.b.entregados()), 1, "el mensaje que ya salió no se manda otra vez")

    def test_el_tope_no_marca_caido_un_canal_sano(self):
        self.b.encargo("uno")
        self.b.encargo("dos")
        salida = directores.drenar(self.b.reg, tope_s=1.0)
        self.assertTrue(salida["cortada_por_tope"], "el segundo aviso espera a la pasada siguiente")
        self.assertEqual([a["entregado"] for a in salida["avisos"]], [True])
        for ruta in (self.b.base / "avisos").glob("*.json"):
            self.assertNotIn("fallos", json.loads(ruta.read_text()))


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

    def _tres_vueltas_del_supervisor(self) -> float:
        from motor.encargos.cli import main

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
        return duracion

    def test_la_vuelta_del_supervisor_no_depende_del_canal_ni_cuando_funciona(self):
        self.b.encargo("uno")
        (self.b.base / "demora").write_text("4")
        duracion = self._tres_vueltas_del_supervisor()
        self.assertLess(duracion, 3.0, "tres vueltas sin esperar los 4 s del transporte")
        self.assertEqual(len(self.b.entregados()), 1, "el aviso salió igual, desde el hilo")

    def test_la_vuelta_del_supervisor_no_espera_a_un_transporte_que_tarda_y_falla(self):
        self.b.encargo("uno")
        (self.b.base / "demora").write_text("4")
        (self.b.base / "falla").write_text("x")
        duracion = self._tres_vueltas_del_supervisor()
        self.assertLess(duracion, 3.0, "tres vueltas sin esperar los 4 s del transporte")
        aviso = next((self.b.base / "avisos").glob("*.json"))
        doc = json.loads(aviso.read_text())
        self.assertEqual(doc["estado"], "incierto")
        self.assertIn("proximo_intento", doc)


if __name__ == "__main__":
    unittest.main()
