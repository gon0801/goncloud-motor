"""El motor da señal de vida a Kuma mientras avanza y la apaga cuando se cuelga (MOTOR 03, bloque C)."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "proto"))
import sandbox  # noqa: E402

from motor import doctor as doctor_mod  # noqa: E402
from motor import effects, senal  # noqa: E402
from motor.__main__ import doctor  # noqa: E402
from motor.conf import load_maquina  # noqa: E402
from motor.daemon import Daemon  # noqa: E402
from motor.plan import parse_plan  # noqa: E402
from motor.store import Store  # noqa: E402

TOKEN = "tokensecreto123"


class KumaFalso:
    """Servidor HTTP que anota cada GET; `traba` hace que no conteste hasta soltarlo."""

    def __init__(self):
        self.pedidos: list[tuple[float, str]] = []
        self.traba = threading.Event()
        self.traba.set()
        kuma = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                kuma.pedidos.append((time.monotonic(), self.path))
                kuma.traba.wait(10)
                if self.path.startswith("/redir"):
                    self.send_response(302)
                    self.send_header("Location", "/api/push/otro")
                else:
                    self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/api/push/{TOKEN}"

    def cierra(self):
        self.traba.set()
        self.srv.shutdown()
        self.srv.server_close()


class Banco(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="motor-senal-"))
        self.home, plan_md = sandbox.create(self.tmp / "b")
        self.plan = parse_plan(plan_md.read_text(encoding="utf-8")).to_dict()
        self.kuma = KumaFalso()
        self.addCleanup(self.kuma.cierra)
        self.d = Daemon(self.home)
        self.addCleanup(self.d._candado.close)
        self.d.tick = lambda now=None: False
        self.tiempos(senal_cada_s=0.1, senal_silencio_min_s=5, repro_tope_s=0.5)

    def tiempos(self, **t):
        self.d.cx.conf.tiempos.update(t)

    def secretos(self, url: str | None, modo: int = 0o600):
        f = self.home / "secretos.env"
        lineas = ["MOTOR_TELEGRAM_TOKEN=prueba"]
        if url is not None:
            lineas.append(f"MOTOR_KUMA_URL={url}")
        f.write_text("\n".join(lineas) + "\n")
        f.chmod(modo)

    def corre(self, tope: float) -> tuple[int, str]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = self.d.run(0.02, False, tope)
        return rc, err.getvalue()

    def en_hilo(self, tope: float):
        t = threading.Thread(target=self.corre, args=(tope,))
        t.start()
        self.addCleanup(t.join)
        return t


class SenalTest(Banco):
    def test_con_la_direccion_manda_una_senal_por_cada_periodo(self):
        self.secretos(self.kuma.url)
        self.d.store.append("demo", "", "PlanLoaded", {"plan": self.plan, "por": "t"})
        self.corre(0.55)
        n = len(self.kuma.pedidos)
        self.assertTrue(3 <= n <= 8, n)
        self.assertTrue(
            all(
                p == f"/api/push/{TOKEN}?status=up&msg=1%20planes"
                for _, p in self.kuma.pedidos
            ),
            self.kuma.pedidos[:2],
        )

    def test_sin_la_direccion_no_hace_nada(self):
        self.secretos(None)
        self.corre(0.4)
        self.assertEqual(self.kuma.pedidos, [])

    def test_agregar_la_direccion_en_caliente_surte_efecto_sin_reiniciar(self):
        self.secretos(None)
        self.en_hilo(0.8)
        time.sleep(0.3)
        self.assertEqual(self.kuma.pedidos, [])
        self.secretos(self.kuma.url)
        time.sleep(0.35)
        self.assertGreaterEqual(len(self.kuma.pedidos), 2)

    def test_quitar_la_direccion_en_caliente_apaga_la_senal(self):
        self.secretos(self.kuma.url)
        self.en_hilo(0.9)
        time.sleep(0.3)
        self.secretos(None)
        time.sleep(0.15)
        n = len(self.kuma.pedidos)
        time.sleep(0.3)
        self.assertEqual(len(self.kuma.pedidos), n)

    def test_una_vuelta_larga_con_reproducciones_que_terminan_sigue_dando_senal(self):
        self.secretos(self.kuma.url)
        self.tiempos(senal_silencio_min_s=0.3, repro_tope_s=0.05)
        d = self.d
        d.cx.ws.copia = lambda *a, **k: self.tmp
        d.fx.repo_conf = lambda snap, repo: effects.RepoConf()
        corridas = []

        def run(*a, **k):
            time.sleep(0.2)
            corridas.append(1)
            return subprocess.CompletedProcess([], 0, "", "")

        self.addCleanup(setattr, effects, "_run", effects._run)
        effects._run = run

        def vuelta_larga(now=None):
            blk = type("B", (), {"repo": "r", "id": "A"})()
            plan = type("P", (), {"id": "demo"})()
            for _ in range(6):
                d.fx._corre_en(plan, None, blk, "sha", "repro-base", "true")
            return False

        d.tick = vuelta_larga
        self.corre(1.0)
        self.assertEqual(len(corridas), 6)
        # la vuelta dura 1.2 s, cuatro veces el silencio de 0.3 s
        self.assertGreaterEqual(len(self.kuma.pedidos), 6)

    def test_una_vuelta_sin_avance_apaga_la_senal_y_vuelve_cuando_el_daemon_avanza(
        self,
    ):
        self.secretos(self.kuma.url)
        self.tiempos(senal_silencio_min_s=0.3, repro_tope_s=0.05)
        colgada, suelta = threading.Event(), threading.Event()
        vueltas = []

        def tick(now=None):
            vueltas.append(1)
            if len(vueltas) == 3:
                colgada.set()
                suelta.wait(5)
            return False

        self.d.tick = tick
        self.en_hilo(5)
        self.assertTrue(colgada.wait(5))
        time.sleep(0.7)
        n = len(self.kuma.pedidos)
        time.sleep(0.5)
        self.assertEqual(
            len(self.kuma.pedidos), n, "colgado más allá del silencio: sin señal"
        )
        suelta.set()
        time.sleep(0.5)
        self.assertGreater(
            len(self.kuma.pedidos), n, "el daemon avanzó: vuelve la señal"
        )

    def test_el_silencio_es_el_mayor_entre_900_y_dos_veces_el_tope_de_reproduccion(
        self,
    ):
        self.assertEqual(senal.silencio_s({}), 900)
        self.assertEqual(senal.silencio_s({"repro_tope_s": 300}), 900)
        self.assertEqual(senal.silencio_s({"repro_tope_s": 600}), 1200)

    def test_un_servidor_que_no_contesta_no_frena_la_vuelta(self):
        self.secretos(self.kuma.url)
        self.kuma.traba.clear()
        inicio = time.monotonic()
        rc, _ = self.corre(0.4)
        self.assertEqual(rc, 3)
        self.assertLess(time.monotonic() - inicio, 2)
        self.assertTrue(self.kuma.pedidos)

    def test_un_mensaje_con_espacios_no_truena(self):
        self.secretos(self.kuma.url)
        s = senal.Senal(self.home, lambda: self.d.cx.conf.tiempos)
        s.avanzo(3)
        self.assertTrue(s.una_vez())
        self.assertTrue(self.kuma.pedidos[0][1].endswith("status=up&msg=3%20planes"))

    def test_una_redireccion_no_se_sigue(self):
        s = senal.Senal(self.home, lambda: self.d.cx.conf.tiempos)
        self.secretos(self.kuma.url.replace("/api/push/" + TOKEN, "/redir"))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(s.una_vez())
        self.assertEqual([p for _, p in self.kuma.pedidos][0][:6], "/redir")
        self.assertEqual(len(self.kuma.pedidos), 1)


class SecretoTest(Banco):
    def test_la_direccion_no_aparece_ni_en_el_log_ni_en_effectfailed_ni_en_doctor(self):
        puerto_muerto = f"http://127.0.0.1:1/api/push/{TOKEN}"
        self.secretos(puerto_muerto)
        _, log = self.corre(0.4)
        self.assertIn("la señal a Kuma falló (", log)
        self.assertNotIn(TOKEN, log)
        self.assertNotIn("127.0.0.1", log)
        self.assertEqual(
            log.count("la señal a Kuma falló"), 1, "a lo mucho una vez por hora"
        )
        eventos = Store(self.home / "motor.db").events()
        self.assertNotIn(TOKEN, json.dumps([e.data for e in eventos]))
        out = doctor(self.home, programadas_sistema=False)
        self.assertEqual(out["senal"], "señal a Kuma: configurada")
        self.assertNotIn(TOKEN, json.dumps(out, ensure_ascii=False))

    def test_secretos_con_permisos_abiertos_no_detiene_nada_y_no_filtra(self):
        self.secretos(f"http://127.0.0.1:1/{TOKEN}", modo=0o644)
        rc, log = self.corre(0.3)
        self.assertEqual(rc, 3)
        self.assertIn("ConfError", log)
        self.assertNotIn(TOKEN, log)
        self.assertNotIn("permisos", log)

    def test_doctor_sin_la_variable_dice_sin_configurar(self):
        self.secretos(None)
        out = doctor(self.home, programadas_sistema=False)
        self.assertEqual(out["senal"], "señal a Kuma: sin configurar")

    def test_un_agente_no_ve_la_variable(self):
        self.secretos(self.kuma.url)
        mq = load_maquina(self.home)
        self.assertIn("MOTOR_KUMA_URL", dict(mq.secretos))
        self.assertNotIn("MOTOR_KUMA_URL", self.d.fx.env_base())
        with unittest_env(MOTOR_KUMA_URL=self.kuma.url):
            self.assertNotIn("MOTOR_KUMA_URL", self.d.fx.env_base())
        ruta = next(iter(self.d.cx.conf.rutas.values()))
        env = doctor_mod.entorno_del_servicio(mq, ruta, self.tmp / "r.json")
        self.assertNotIn("MOTOR_KUMA_URL", env)


@contextlib.contextmanager
def unittest_env(**kv):
    import os

    antes = {k: os.environ.get(k) for k in kv}
    os.environ.update(kv)
    try:
        yield
    finally:
        for k, v in antes.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
