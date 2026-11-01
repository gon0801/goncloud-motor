"""Un canal de avisos caído se ve en el estado del motor, con su último error, sin pasar por ese canal."""

import contextlib
import io
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from motor import doctor as doctor_mod
from motor import effects
from motor.encargos.registro import Registro
from motor.integraciones import canales, directores
from motor.integraciones.cli import main as director
from motor.senal import VARIABLE_ENCARGOS, Senal

HORA = "2026-11-01T10:00:00Z"
T0 = 1_000_000.0

TRANSPORTE = (
    "import os,sys,json\n"
    "from pathlib import Path\n"
    "base=Path(os.environ['MOTOR_AVISO_BASE'])\n"
    "sys.stdin.read()\n"
    "with open(base/'llamadas.log','a') as f: f.write(os.environ['MOTOR_AVISO_ID']+'\\n')\n"
    "modo=(base/'modo').read_text().strip()\n"
    "if modo=='caido':\n"
    " sys.stderr.write('no puedo leer el archivo del bot en https://api.telegram.org/bot123456789:ABCdefGhIJKlmNoPQRsTUVwxyz0123456789/sendMessage')\n"
    " sys.exit(1)\n"
    "if modo=='sin_recibo':\n"
    " sys.exit(0)\n"
    "print(json.dumps({'recibo':'ok-'+os.environ['MOTOR_AVISO_ID']}))\n"
)


class _Alto(BaseException):
    pass


class Banco(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name) / "base"
        self.reg = Registro(self.base)
        self.addCleanup(self.reg.close)
        script = Path(self._tmp.name) / "transporte.py"
        script.write_text(TRANSPORTE, encoding="utf-8")
        (self.base / "canal.json").write_text(
            json.dumps({"tg": {"comando": [sys.executable, "-B", str(script)], "idempotente": True}}),
            encoding="utf-8",
        )
        self.modo("caido")
        self.ident = directores.encargar(
            self.reg, "pedido", "uno", HORA, "hermes", "tg", "directa"
        )["encargo"]
        self.reg.anotar(self.ident, {"kind": "fases", "data": {
            "fases": [{"id": "A", "resultado": "fase comprobada"}], "hora": HORA,
        }}, "fase")

    def modo(self, valor: str) -> None:
        (self.base / "modo").write_text(valor)

    def estado(self) -> dict:
        return json.loads((self.base / "canales" / "tg.json").read_text())

    def llamadas(self) -> int:
        log = self.base / "llamadas.log"
        return len(log.read_text().split()) if log.exists() else 0


class EstadoDelCanal(Banco):
    def test_un_transporte_que_falla_deja_el_ultimo_error_sin_tokens_ni_direcciones(self):
        directores.drenar(self.reg, ahora=T0)
        estado = self.estado()
        self.assertEqual(estado["fallando_desde"], directores._iso(T0))
        self.assertEqual(estado["fallos"], 1)
        self.assertIn("no puedo leer el archivo del bot", estado["ultimo_error"])
        for secreto in ("telegram.org", "ABCdef", "123456789"):
            self.assertNotIn(secreto, estado["ultimo_error"])

    def test_solo_es_un_canal_caido_pasado_el_umbral(self):
        directores.drenar(self.reg, ahora=T0)
        self.assertEqual(canales.canales_caidos(self.base, ahora=T0 + 599), [])
        caidos = canales.canales_caidos(self.base, ahora=T0 + canales.UMBRAL_CAIDO_S)
        self.assertEqual([c["canal"] for c in caidos], ["tg"])
        self.assertEqual(caidos[0]["fallando_desde"], directores._iso(T0))
        self.assertIn("no puedo leer el archivo del bot", caidos[0]["ultimo_error"])

    def test_el_fallo_sostenido_conserva_el_inicio_y_cuenta_los_fallos(self):
        directores.drenar(self.reg, ahora=T0)
        directores.drenar(self.reg, ahora=T0 + 700)
        estado = self.estado()
        self.assertEqual(estado["fallando_desde"], directores._iso(T0))
        self.assertEqual(estado["fallos"], 2)
        self.assertEqual(estado["ultimo_intento"], directores._iso(T0 + 700))

    def test_al_volver_el_canal_deja_de_estar_caido(self):
        directores.drenar(self.reg, ahora=T0)
        directores.drenar(self.reg, ahora=T0 + 700)
        self.modo("ok")
        directores.drenar(self.reg, ahora=T0 + 10_000)
        self.assertEqual(canales.canales_caidos(self.base, ahora=T0 + 20_000), [])
        estado = self.estado()
        self.assertNotIn("fallando_desde", estado)
        self.assertEqual(estado["ultimo_exito"], directores._iso(T0 + 10_000))

    def test_un_mensaje_sin_recibo_no_marca_caido_el_canal(self):
        self.modo("sin_recibo")
        directores.drenar(self.reg, ahora=T0)
        directores.drenar(self.reg, ahora=T0 + 700)
        self.assertFalse((self.base / "canales" / "tg.json").exists())
        self.assertEqual(canales.canales_caidos(self.base, ahora=T0 + 5_000), [])


class Robustez(Banco):
    def test_un_fallos_no_numerico_en_el_estado_no_tumba_ver_ni_avisar(self):
        ruta = self.base / "canales" / "tg.json"
        ruta.parent.mkdir(parents=True)
        ruta.write_text(json.dumps({
            "canal": "tg", "fallando_desde": directores._iso(T0), "fallos": "muchos",
            "ultimo_error": "se cortó",
        }))
        caidos = canales.canales_caidos(self.base, ahora=T0 + 700)
        self.assertEqual([(c["canal"], c["fallos"]) for c in caidos], [("tg", 0)])
        directores.drenar(self.reg, ahora=T0 + 700)
        self.assertEqual(self.estado()["fallos"], 1)

    def test_un_estado_ilegible_se_ignora(self):
        ruta = self.base / "canales" / "tg.json"
        ruta.parent.mkdir(parents=True)
        ruta.write_text("{no es json")
        self.assertEqual(canales.canales_caidos(self.base, ahora=T0), [])

    def test_con_cortar_despues_el_estado_del_canal_tambien_se_actualiza(self):
        directores.drenar(self.reg, cortar="despues", ahora=T0)
        self.assertEqual(self.llamadas(), 1)
        self.assertEqual(self.estado()["fallando_desde"], directores._iso(T0))
        self.assertIn("no puedo leer el archivo del bot", self.estado()["ultimo_error"])

    def test_la_redaccion_cubre_claves_y_cadenas_opacas_sin_borrar_el_mensaje(self):
        secretos = {
            "Authorization: Bearer abcDEF1234567890abcd": "abcDEF1234567890abcd",
            "password: hunter2": "hunter2",
            "falló con api_key=abc123 y reintentó": "abc123",
            "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef0123456789": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            "clave sin numeros: abcdefghijklmnopqrstuvwxyzABCDEFGHIJ": "abcdefghijklmnopqrstuvwxyz",
            "bot 123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw": "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
            "ver https://api.telegram.org/bot1/sendMessage?token=zzz": "telegram.org",
        }
        for texto, secreto in secretos.items():
            with self.subTest(texto=texto):
                self.assertNotIn(secreto, canales.redactar(texto))
        self.assertEqual(
            canales.redactar("el transporte salio 1: no puedo leer el archivo del bot"),
            "el transporte salio 1: no puedo leer el archivo del bot",
        )

    def test_sin_direccion_de_kuma_un_canal_caido_se_deja_dicho_en_el_log_una_vez(self):
        canales.registrar_pasada(self.base, time.time() - 1000, {"tg": "no puedo leer el archivo del bot"}, set())
        senal = Senal(self.base, lambda: {}, canales=lambda: canales.canales_caidos(self.base))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertFalse(senal.una_vez(), "sin MOTOR_KUMA_URL no sale la señal")
            self.assertFalse(senal.una_vez())
        lineas = [x for x in err.getvalue().splitlines() if "canal tg caído desde" in x]
        self.assertEqual(len(lineas), 1, "una sola vez por periodo")
        self.assertIn("no puedo leer el archivo del bot", lineas[0])
        self.assertIn("sin MOTOR_KUMA_URL", lineas[0])


class VerYSenal(Banco):
    def _ver(self) -> dict:
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            rc = director(["ver", self.ident, "--base", str(self.base)])
        self.assertEqual(rc, 0)
        return json.loads(salida.getvalue())

    def test_motor_director_ver_muestra_el_canal_caido_sin_llamar_al_canal(self):
        self.assertNotIn("canales_caidos", self._ver())
        canales.registrar_pasada(self.base, time.time() - 1000, {"tg": "se cortó la conexión"}, set())
        salida = self._ver()
        self.assertEqual([c["canal"] for c in salida["canales_caidos"]], ["tg"])
        self.assertEqual(salida["canales_caidos"][0]["ultimo_error"], "se cortó la conexión")
        self.assertEqual(self.llamadas(), 0, "ver no usa el transporte del canal caído")

    def test_un_canal_que_falla_hace_poco_todavia_no_se_muestra(self):
        canales.registrar_pasada(self.base, time.time() - 30, {"tg": "se cortó"}, set())
        self.assertNotIn("canales_caidos", self._ver())

    def _kuma(self, *variables):
        pedidos = []

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                pedidos.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        servidor = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
        self.addCleanup(servidor.server_close)
        self.addCleanup(servidor.shutdown)
        secretos = self.base / "secretos.env"
        lineas = ["MOTOR_TELEGRAM_TOKEN=prueba"]
        for variable in variables or ("MOTOR_KUMA_URL",):
            token = "tk" if variable == "MOTOR_KUMA_URL" else "tk-encargos"
            lineas.append(f"{variable}=http://127.0.0.1:{servidor.server_port}/api/push/{token}")
        secretos.write_text("\n".join(lineas) + "\n")
        secretos.chmod(0o600)
        return pedidos

    def test_la_senal_a_kuma_sale_down_con_el_ultimo_error_cuando_el_canal_esta_caido(self):
        pedidos = self._kuma()
        senal = Senal(self.base, lambda: {}, canales=lambda: canales.canales_caidos(self.base))
        self.assertTrue(senal.una_vez())
        consulta = urllib.parse.parse_qs(urllib.parse.urlsplit(pedidos[-1]).query)
        self.assertEqual(consulta["status"], ["up"])
        self.assertEqual(consulta["msg"], ["0 planes"])

        canales.registrar_pasada(self.base, time.time() - 1000, {"tg": "no puedo leer el archivo del bot"}, set())
        self.assertTrue(senal.una_vez())
        consulta = urllib.parse.parse_qs(urllib.parse.urlsplit(pedidos[-1]).query)
        self.assertEqual(consulta["status"], ["down"])
        self.assertIn("canal tg caído desde", consulta["msg"][0])
        self.assertIn("no puedo leer el archivo del bot", consulta["msg"][0])
        self.assertEqual(self.llamadas(), 0, "la señal no usa el transporte del canal caído")

    def _supervisor(self, **extra) -> Senal:
        return Senal(
            self.base,
            lambda: {},
            canales=lambda: canales.canales_caidos(self.base),
            variable=VARIABLE_ENCARGOS,
            **extra,
        )

    def test_el_supervisor_no_usa_la_direccion_del_daemon_de_planes(self):
        pedidos = self._kuma("MOTOR_KUMA_URL")
        canales.registrar_pasada(self.base, time.time() - 1000, {"tg": "x"}, set())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertFalse(self._supervisor().una_vez(), "con la dirección del daemon no manda nada")
        self.assertEqual(pedidos, [])
        self.assertIn("sin MOTOR_KUMA_ENCARGOS_URL", err.getvalue())
        self.assertTrue(Senal(self.base, lambda: {}).una_vez(), "el daemon sigue con la suya")
        self.assertEqual(pedidos, ["/api/push/tk?status=up&msg=0%20planes"])

    def test_cada_proceso_manda_a_su_propio_monitor_y_no_se_pisan(self):
        pedidos = self._kuma("MOTOR_KUMA_URL", "MOTOR_KUMA_ENCARGOS_URL")
        canales.registrar_pasada(self.base, time.time() - 1000, {"tg": "x"}, set())
        daemon = Senal(self.base, lambda: {})
        supervisor = self._supervisor()
        for _ in range(2):
            self.assertTrue(daemon.una_vez())
            self.assertTrue(supervisor.una_vez())
        del_daemon = [p for p in pedidos if p.startswith("/api/push/tk?")]
        del_supervisor = [p for p in pedidos if p.startswith("/api/push/tk-encargos?")]
        self.assertEqual(len(del_daemon), 2)
        self.assertEqual(len(del_supervisor), 2)
        self.assertTrue(all("status=up" in p for p in del_daemon), "el monitor del daemon no ve el canal caído")
        self.assertTrue(all("status=down" in p for p in del_supervisor))

    def test_la_direccion_del_supervisor_puede_estar_en_el_home_instalado(self):
        pedidos = self._kuma("MOTOR_KUMA_ENCARGOS_URL")
        instalado = Path(self._tmp.name) / "motor-instalado"
        instalado.mkdir()
        (instalado / "secretos.env").write_text((self.base / "secretos.env").read_text())
        (instalado / "secretos.env").chmod(0o600)
        (self.base / "secretos.env").unlink()
        canales.registrar_pasada(self.base, time.time() - 1000, {"tg": "se cortó"}, set())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertFalse(self._supervisor().una_vez(), "sin respaldo no hay dirección")
        self.assertEqual(pedidos, [])
        self.assertTrue(self._supervisor(respaldo=instalado).una_vez())
        consulta = urllib.parse.parse_qs(urllib.parse.urlsplit(pedidos[-1]).query)
        self.assertEqual(consulta["status"], ["down"])
        self.assertIn("se cortó", consulta["msg"][0])

    def test_el_supervisor_cablea_su_propia_variable_y_el_home_instalado_como_respaldo(self):
        from motor.encargos.cli import main

        capturado = {}

        class SenalFalsa:
            def __init__(self, home, tiempos, **kwargs):
                capturado.update(home=home, **kwargs)

            def arranca(self):
                raise _Alto

        with mock.patch("motor.senal.Senal", SenalFalsa), mock.patch(
            "motor.encargos.cli.barrer", return_value=[]
        ):
            with self.assertRaises(_Alto):
                main(["supervisor", "--base", str(self.base), "--agente", "sleep"])
        self.assertEqual(capturado["home"], self.base.resolve())
        self.assertEqual(capturado["variable"], "MOTOR_KUMA_ENCARGOS_URL")
        self.assertNotEqual(capturado["variable"], "MOTOR_KUMA_URL")
        self.assertEqual(capturado["respaldo"], Path.home() / ".motor")
        self.assertEqual(capturado["canales"](), [])

    def test_ninguna_direccion_de_kuma_llega_al_entorno_de_los_agentes_ni_de_las_comprobaciones(self):
        secretos = (
            ("MOTOR_KUMA_URL", "http://kuma/push/daemon"),
            ("MOTOR_KUMA_ENCARGOS_URL", "http://kuma/push/encargos"),
            ("MOTOR_TELEGRAM_TOKEN", "prueba"),
        )
        mq = SimpleNamespace(env=(), secretos=secretos, path_extra=())
        env = effects.entorno_maquina(mq)
        servicio = doctor_mod.entorno_del_servicio(mq, SimpleNamespace(env=(), modelo="m"), self.base / "r.json")
        for visto in (env, servicio):
            self.assertNotIn("MOTOR_KUMA_URL", visto)
            self.assertNotIn("MOTOR_KUMA_ENCARGOS_URL", visto)
            self.assertEqual(visto["MOTOR_TELEGRAM_TOKEN"], "prueba", "los demás secretos siguen como antes")

    def test_sin_canales_la_senal_no_cambia(self):
        pedidos = self._kuma()
        self.assertTrue(Senal(self.base, lambda: {}).una_vez())
        self.assertEqual(pedidos[-1], "/api/push/tk?status=up&msg=0%20planes")


if __name__ == "__main__":
    unittest.main()
