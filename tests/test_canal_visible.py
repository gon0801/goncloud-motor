"""Un canal de avisos caído se ve en el estado del motor, con su último error, sin pasar por ese canal."""

from __future__ import annotations

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
from motor.senal import Senal

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
    " sys.stderr.write('no puedo leer el archivo del bot en https://api.telegram.org/bot123456789:' + 'A'*10 + 'b'*25 + '/sendMessage')\n"
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
        for secreto in ("telegram.org", "AAAAAAAAAA", "123456789"):
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

    def _con_conciliacion(self, respuesta: str) -> None:
        """El canal declara `conciliacion`: un script que contesta `respuesta` (JSON) y sale 0."""
        script = Path(self._tmp.name) / "conciliacion.py"
        script.write_text(f"import sys\nsys.stdin.read()\nprint({respuesta!r})\n", encoding="utf-8")
        cfg = json.loads((self.base / "canal.json").read_text())
        cfg["tg"]["conciliacion"] = [sys.executable, "-B", str(script)]
        (self.base / "canal.json").write_text(json.dumps(cfg), encoding="utf-8")

    def test_una_conciliacion_sin_veredicto_no_oculta_un_canal_caido_pasado_el_umbral(self):
        # Transporte de envío roto + conciliación que contesta sin decidir: el aviso se difiere sin enviar, el canal no
        # recupera ni falla de nuevo, y sigue figurando caído (ocultar un canal caído es peor que mostrar tarde uno recuperado).
        for respuesta in ('{"estado": "desconocido"}', "esto no es json", '{"ausente": false}'):
            with self.subTest(respuesta=respuesta):
                self.modo("caido")
                self._con_conciliacion(respuesta)
                directores.drenar(self.reg, ahora=T0)
                inicio = self.estado()["fallando_desde"]
                directores.drenar(self.reg, ahora=T0 + 700)
                self.assertEqual(self.estado()["fallando_desde"], inicio, "la conciliación sin veredicto no borra el estado")
                caidos = canales.canales_caidos(self.base, ahora=T0 + 700)
                self.assertEqual([c["canal"] for c in caidos], ["tg"], "pasado el umbral el canal sigue visible")
                (self.base / "canales" / "tg.json").unlink()
                for ruta in (self.base / "avisos").glob("*.json"):
                    ruta.unlink()

    def test_un_envio_que_sale_bien_pero_sin_recibo_no_recupera_un_canal_caido(self):
        self.modo("caido")
        directores.drenar(self.reg, ahora=T0)
        inicio = self.estado()["fallando_desde"]
        self.modo("sin_recibo")
        directores.drenar(self.reg, ahora=T0 + 700)
        self.assertEqual(self.llamadas(), 2, "el reintento sí salió")
        self.assertEqual(self.estado()["fallando_desde"], inicio, "sin recibo no hay entrega probada")
        self.assertEqual([c["canal"] for c in canales.canales_caidos(self.base, ahora=T0 + 700)], ["tg"])
        self.modo("ok")
        directores.drenar(self.reg, ahora=T0 + 20_000)
        self.assertNotIn("fallando_desde", self.estado(), "un envío con recibo sí lo recupera")
        self.assertEqual(canales.canales_caidos(self.base, ahora=T0 + 30_000), [])

    def _aviso_nuevo(self) -> None:
        """Deja el aviso como si fuera nuevo (sin archivo de aviso ni entrega), que es cuando corre el hook `despues`."""
        for ruta in (self.base / "avisos").glob("*.json"):
            ruta.unlink()
        for ruta in (self.base / "canal" / "tg").glob("*"):
            ruta.unlink()

    def _motivo_del_aviso(self) -> str:
        return json.loads(next((self.base / "avisos").glob("*.json")).read_text()).get("motivo", "")

    def test_con_cortar_despues_solo_un_envio_con_recibo_recupera_el_canal(self):
        hook = "envio hecho sin recibo; falta conciliar"
        self.modo("caido")
        directores.drenar(self.reg, ahora=T0)
        inicio = self.estado()["fallando_desde"]
        llamadas = self.llamadas()

        self._aviso_nuevo()
        self.modo("sin_recibo")
        directores.drenar(self.reg, cortar="despues", ahora=T0 + 700)
        self.assertEqual(self.llamadas(), llamadas + 1, "el hook envió")
        self.assertEqual(self._motivo_del_aviso(), hook, "corrió la rama del hook, no la del reintento")
        self.assertEqual(self.estado()["fallando_desde"], inicio, "el hook no recibió recibo: no hay entrega probada")
        self.assertEqual([c["canal"] for c in canales.canales_caidos(self.base, ahora=T0 + 700)], ["tg"])

        self._aviso_nuevo()
        self.modo("ok")
        directores.drenar(self.reg, cortar="despues", ahora=T0 + 20_000)
        self.assertEqual(self._motivo_del_aviso(), hook, "otra vez la rama del hook")
        self.assertNotIn("fallando_desde", self.estado(), "el hook sí recibió recibo")

    def test_con_cortar_despues_un_transporte_que_cae_marca_el_canal(self):
        self.modo("caido")
        directores.drenar(self.reg, cortar="despues", ahora=T0)
        self.assertEqual(self._motivo_del_aviso(), "envio hecho sin recibo; falta conciliar")
        self.assertEqual(self.estado()["fallando_desde"], directores._iso(T0))


    def test_una_conciliacion_que_halla_el_aviso_recupera_el_canal(self):
        self.modo("caido")
        self._con_conciliacion('{"recibo": "r-1"}')
        directores.drenar(self.reg, ahora=T0)
        self.assertIn("fallando_desde", self.estado())
        directores.drenar(self.reg, ahora=T0 + 700)
        self.assertNotIn("fallando_desde", self.estado(), "'hallado' es prueba de entrega")
        self.assertEqual(canales.canales_caidos(self.base, ahora=T0 + 5_000), [])

    def test_con_ausente_decide_el_envio_que_sigue_en_la_misma_pasada(self):
        self._con_conciliacion('{"ausente": true}')
        self.modo("caido")
        directores.drenar(self.reg, ahora=T0)
        directores.drenar(self.reg, ahora=T0 + 700)
        estado = self.estado()
        self.assertIn("fallando_desde", estado, "el envío que sigue a 'ausente' falla: gana el fallo")
        self.assertEqual(estado["fallos"], 2)
        self.modo("ok")
        directores.drenar(self.reg, ahora=T0 + 20_000)
        self.assertNotIn("fallando_desde", self.estado(), "el envío con recibo recupera el canal")

    def test_una_conciliacion_que_falla_no_marca_el_canal_como_recuperado(self):
        script = Path(self._tmp.name) / "conciliacion_rota.py"
        script.write_text("import sys\nsys.stdin.read()\nsys.exit(7)\n", encoding="utf-8")
        cfg = json.loads((self.base / "canal.json").read_text())
        cfg["tg"]["conciliacion"] = [sys.executable, "-B", str(script)]
        (self.base / "canal.json").write_text(json.dumps(cfg), encoding="utf-8")
        self.modo("caido")
        directores.drenar(self.reg, ahora=T0)
        directores.drenar(self.reg, ahora=T0 + 4_000)
        estado = self.estado()
        self.assertIn("fallando_desde", estado)
        self.assertEqual(estado["fallos"], 2)


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
        # Los secretos de prueba se arman en la ejecución: ningún literal con forma de llave entra al repo.
        clave = "abcDEF" + "1234567890" + "abcd"
        opaco = "A" * 26 + "b" * 6 + "0123456789"
        solo_letras = "q" * 26 + "R" * 9
        telegram = "123456789:" + "A" + "x" * 34
        secretos = {
            f"Authorization: Bearer {clave}": clave,
            "password: hunter2": "hunter2",
            "falló con api_key=abc123 y reintentó": "abc123",
            f"sk-{opaco}": opaco,
            f"clave sin numeros: {solo_letras}": solo_letras,
            f"bot {telegram}": telegram.split(":")[1],
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

    def _kuma(self, donde: Path | None = None):
        """Un Kuma falso; deja su dirección en MOTOR_KUMA_URL del secretos.env de `donde` (por defecto la base)."""
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
        carpeta = donde or self.base
        carpeta.mkdir(parents=True, exist_ok=True)
        secretos = carpeta / "secretos.env"
        secretos.write_text(
            f"MOTOR_TELEGRAM_TOKEN=prueba\nMOTOR_KUMA_URL=http://127.0.0.1:{servidor.server_port}/api/push/tk\n"
        )
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


    def _supervisor_arranca(self, casa: Path) -> tuple[list, str]:
        """Arranca `motor supervisor` (bucle) con la Senal real y Path.home en `casa`. Antes de detener el arranque
        deja que la Senal haga un intento; devuelve las instancias creadas y lo que salió por stderr."""
        from motor.encargos.cli import main
        from motor.senal import Senal as SenalReal

        instancias = []

        def arranca(senal):
            instancias.append(senal)
            senal.una_vez()
            raise _Alto

        err = io.StringIO()
        with mock.patch.object(SenalReal, "arranca", arranca), mock.patch(
            "motor.encargos.cli.barrer", return_value=[]
        ), mock.patch("pathlib.Path.home", return_value=casa), contextlib.redirect_stderr(err):
            with self.assertRaises(_Alto):
                main(["supervisor", "--base", str(self.base), "--agente", "sleep"])
        return instancias, err.getvalue()

    def test_el_supervisor_toma_la_direccion_de_kuma_de_su_base(self):
        pedidos = self._kuma()
        casa = Path(self._tmp.name) / "casa"
        casa.mkdir()
        instancias, err = self._supervisor_arranca(casa)
        self.assertEqual(len(instancias), 1)
        self.assertEqual(instancias[0].home, self.base.resolve())
        self.assertEqual(pedidos, ["/api/push/tk?status=up&msg=0%20planes"])
        self.assertNotIn("sin MOTOR_KUMA_URL", err)

    def test_el_supervisor_nunca_lee_el_home_del_motor_de_planes(self):
        casa = Path(self._tmp.name) / "casa"
        pedidos_del_daemon = self._kuma(casa / ".motor")
        self.assertFalse((self.base / "secretos.env").exists(), "la base no trae la dirección")
        instancias, err = self._supervisor_arranca(casa)
        self.assertEqual(pedidos_del_daemon, [], "la dirección de ~/.motor es del daemon de planes")
        self.assertFalse(hasattr(instancias[0], "_respaldo"), "no hay respaldo en ~/.motor")
        self.assertIn("sin MOTOR_KUMA_URL", err)

    def test_sin_la_variable_el_supervisor_avisa_en_el_log_y_no_manda_senal(self):
        casa = Path(self._tmp.name) / "casa"
        casa.mkdir()
        canales.registrar_pasada(self.base, time.time() - 1000, {"tg": "no puedo leer el archivo del bot"}, set())
        instancias, err = self._supervisor_arranca(casa)
        self.assertIn("sin MOTOR_KUMA_URL en el secretos.env de la base", err)
        self.assertIn("el supervisor de encargos no manda señal a Kuma", err)
        self.assertIn("canal tg caído desde", err, "el canal caído también queda en el log")

    def test_un_secretos_ilegible_en_la_base_cuenta_como_sin_direccion(self):
        casa = Path(self._tmp.name) / "casa"
        casa.mkdir()
        pedidos = self._kuma()
        (self.base / "secretos.env").chmod(0o644)
        _, err = self._supervisor_arranca(casa)
        self.assertEqual(pedidos, [])
        self.assertIn("sin MOTOR_KUMA_URL", err)

    def test_con_un_canal_caido_el_supervisor_manda_down_por_esa_misma_direccion(self):
        pedidos = self._kuma()
        casa = Path(self._tmp.name) / "casa"
        casa.mkdir()
        canales.registrar_pasada(self.base, time.time() - 1000, {"tg": "no puedo leer el archivo del bot"}, set())
        self._supervisor_arranca(casa)
        consulta = urllib.parse.parse_qs(urllib.parse.urlsplit(pedidos[-1]).query)
        self.assertTrue(pedidos[-1].startswith("/api/push/tk?"), "la misma dirección, sin una nueva")
        self.assertEqual(consulta["status"], ["down"])
        self.assertIn("canal tg caído desde", consulta["msg"][0])
        self.assertIn("no puedo leer el archivo del bot", consulta["msg"][0])

    def test_la_direccion_de_kuma_no_llega_al_entorno_de_los_agentes_ni_de_las_comprobaciones(self):
        secretos = (("MOTOR_KUMA_URL", "http://kuma/push/daemon"), ("MOTOR_TELEGRAM_TOKEN", "prueba"))
        mq = SimpleNamespace(env=(), secretos=secretos, path_extra=())
        env = effects.entorno_maquina(mq)
        servicio = doctor_mod.entorno_del_servicio(mq, SimpleNamespace(env=(), modelo="m"), self.base / "r.json")
        for visto in (env, servicio):
            self.assertNotIn("MOTOR_KUMA_URL", visto)
            self.assertEqual(visto["MOTOR_TELEGRAM_TOKEN"], "prueba", "los demás secretos siguen como antes")

    def test_sin_canales_la_senal_no_cambia(self):
        pedidos = self._kuma()
        self.assertTrue(Senal(self.base, lambda: {}).una_vez())
        self.assertEqual(pedidos[-1], "/api/push/tk?status=up&msg=0%20planes")


if __name__ == "__main__":
    unittest.main()
