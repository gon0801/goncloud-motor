"""El envoltorio que corre a cada agente sin ventana."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(RAIZ), str(RAIZ / "proto")]
import sandbox  # noqa: E402

from motor import machine as m  # noqa: E402
from motor import runwrap  # noqa: E402
from motor.daemon import Daemon  # noqa: E402
from motor.plan import parse_plan  # noqa: E402


class CierreTrasResultadoTest(unittest.TestCase):
    def corre(
        self, script: str, sin_avance: float = 30, extra: tuple[str, ...] = ()
    ) -> tuple[dict, float]:
        o, dura, _ = self.corre_en(script, sin_avance, extra)
        return o, dura

    def corre_en(
        self, script: str, sin_avance: float = 30, extra: tuple[str, ...] = ()
    ) -> tuple[dict, float, Path]:
        t = Path(tempfile.mkdtemp(prefix="runwrap-"))
        d, cwd = t / "intento", t / "w"
        d.mkdir()
        cwd.mkdir()
        (t / "sesion.jsonl").write_text("")
        (t / "sesion").mkdir()
        (d / "pstack.json").write_text(
            json.dumps(
                {
                    "cli": "kimi",
                    "exigir": False,
                    "exigencia": {},
                    "registros": [str(t / "sesion.jsonl")],
                }
            )
        )
        inicio = time.time()
        runwrap.main(
            [
                "--dir", str(d), "--cwd", str(cwd), "--timeout", "60",
                "--sin-avance", str(sin_avance), "--cierre", "1", *extra,
                "--", sys.executable, "-c", script,
            ]
        )  # fmt: skip
        return json.loads((d / "outcome.json").read_text()), time.time() - inicio, d

    def test_entrego_y_no_sale_se_cierra_como_terminado(self):
        """INGESTA 01, bloque D: Grok dejaba result.json y su proceso no salía; a los 45 min se
        mataba "sin avance" y la entrega se perdía."""
        o, dura = self.corre(
            "import os, time, pathlib\n"
            "pathlib.Path(os.environ['MOTOR_ATTEMPT_DIR'], 'result.json').write_text("
            '\'{"bloqueado": "se quedó pegado"}\')\n'
            "time.sleep(60)\n"
        )
        self.assertLess(dura, 20)
        self.assertEqual(
            (o["exit"], o["no_progress"], o["cerrado_tras_resultado"]), (0, False, True)
        )

    PRELUDIO = (
        "import json, os, pathlib, time\n"
        "d = pathlib.Path(os.environ['MOTOR_ATTEMPT_DIR'])\n"
        "sesion = pathlib.Path(json.loads((d / 'pstack.json').read_text())['registros'][0])\n"
        "def veredicto(v, bloq=()):\n"
        "    (d / 'result.json').write_text(json.dumps({'sha': 'abc', 'veredicto': v,"
        " 'bloqueantes': [{'texto': b} for b in bloq], 'notas': []}))\n"
    )
    REVISOR = ("--entrega-sha", "abc")

    def test_subagentes_que_siguen_escribiendo_impiden_el_cierre(self):
        o, _ = self.corre(
            self.PRELUDIO + "sub = sesion.with_suffix('') / 'subagents'\n"
            "sub.mkdir()\n"
            "(sub / 'x.jsonl').write_text('a\\n')\n"
            "veredicto('APROBADO')\n"
            "fin = time.time() + 4\n"
            "while time.time() < fin:\n"
            "    for f in (sesion, sub / 'x.jsonl'):\n"
            "        f.open('a').write('l\\n')\n"
            "    time.sleep(0.3)\n",
            extra=self.REVISOR,
        )
        self.assertEqual((o["cerrado_tras_resultado"], o["exit"]), (False, 0))

    def test_llamada_sin_fin_impide_el_cierre_hasta_que_se_llena(self):
        """El paso de abierta a cerrada cuenta como avance: el cierre llega `--cierre` s después de
        la primera vuelta que ve `fin` lleno, y esa vuelta se mide por su propio reloj, no por la
        hora de `fin`. La vuelta de runwrap se estira a 1 s y cada una se anota al arrancar; el
        agente llena `fin` a mitad de vuelta (4,5 s después de que el motor congeló su entrega,
        que marca una vuelta). Sin `abierta_antes`, `ultimo_cambio_p` queda una vuelta atrás y el
        cierre salta en la misma vuelta que ve `fin` lleno, a menos de 1 s de ella; al código
        bueno le falta una vuelta entera, 1 s o más, sea cual sea la fase en que `fin` caiga."""
        real = time.sleep
        vueltas = []

        def vuelta_de_1s(s):
            if s != 0.2:
                real(s)
                return
            real(1.0)
            vueltas.append(time.time())

        with unittest.mock.patch.object(runwrap.time, "sleep", vuelta_de_1s):
            o, _, d = self.corre_en(
                self.PRELUDIO + "veredicto('APROBADO')\n"
                "(d / 'llamadas').mkdir()\n"
                "f = d / 'llamadas' / 'x.json'\n"
                "for _ in range(2000):\n"
                "    if (d / 'entrega.json').exists():\n"
                "        break\n"
                "    time.sleep(0.005)\n"
                "else:\n"
                "    raise SystemExit(2)\n"
                "base = time.time()\n"
                "f.write_text(json.dumps({'fin': None, 'pid': os.getpid()}))\n"
                "while time.time() < base + 4.5:\n"
                "    time.sleep(0.02)\n"
                "f.write_text(json.dumps({'fin': time.time(), 'pid': os.getpid()}))\n"
                "time.sleep(60)\n",
                extra=self.REVISOR,
            )
        self.assertIs(o["cerrado_tras_resultado"], True)
        fin = json.loads((d / "llamadas" / "x.json").read_text())["fin"]
        primera = min(v for v in vueltas if v > fin)
        self.assertGreaterEqual(
            (d / "outcome.json").stat().st_mtime - primera, 1.0
        )  # da tiempo de leer lo que contestó el panel

    def test_pid_invalido_en_la_llamada_no_cosecha_al_agente(self):
        """Un registro con pid -1 nunca llega a `pid_vivo`: sin la guarda, su `waitpid(-1)`
        recogería al agente y el outcome diría `exit 0` habiendo salido con 3."""
        o, _ = self.corre(
            self.PRELUDIO + "veredicto('APROBADO')\n"
            "(d / 'llamadas').mkdir()\n"
            "(d / 'llamadas' / 'x.json').write_text(json.dumps({'fin': None, 'pid': -1}))\n"
            "raise SystemExit(3)\n",
            extra=self.REVISOR,
        )
        self.assertEqual(o["exit"], 3)

    def collect_de(self, script: str, sin_avance: float):
        """Corre el agente bajo runwrap y pasa su intento por `Effects.collect` del daemon."""
        _, _, d = self.corre_en(script, sin_avance=sin_avance, extra=self.REVISOR)
        home, plan_md = sandbox.create(d.parent / "banco")
        daemon = Daemon(home)
        daemon.store.append(
            "demo",
            "",
            "PlanLoaded",
            {"plan": parse_plan(plan_md.read_text()).to_dict(), "por": "t"},
        )
        att = m.Attempt(
            n=1,
            role="revisar",
            modelo="glm",
            started=0.0,
            proc={},
            dir=str(d),
            sha="abc",
        )
        plan = daemon.plans()[0]
        return daemon.fx.collect(plan, daemon._fold(plan), "A", att), d, daemon

    def test_llamada_abierta_con_shim_vivo_no_se_pierde_por_sin_avance(self):
        """Un shim colgado retiene el cierre hasta que `--sin-avance` corta de verdad; la entrega
        válida ya está congelada y `collect` la rescata como veredicto."""
        salida, d, _ = self.collect_de(
            self.PRELUDIO + "veredicto('APROBADO')\n"
            "(d / 'llamadas').mkdir()\n"
            "(d / 'llamadas' / 'x.json').write_text(json.dumps({'fin': None, 'pid': os.getpid()}))\n"
            "time.sleep(60)\n",
            sin_avance=3,
        )
        self.assertEqual(salida.get("kind"), "veredicto", salida)
        o = json.loads((d / "outcome.json").read_text())
        self.assertEqual((o["no_progress"], o["cerrado_tras_resultado"]), (True, False))
        self.assertEqual(
            json.loads((d / "entrega.json").read_text())["veredicto"], "APROBADO"
        )

    def test_el_rescate_le_entrega_json_no_un_result_pisado(self):
        """B.1 con B.2: `result.json` pisado con algo inválido después de congelar la entrega no
        tumba el veredicto; el rescate lee `entrega.json`, no `result.json`."""
        salida, d, _ = self.collect_de(
            self.PRELUDIO + "veredicto('APROBADO')\n"
            "(d / 'llamadas').mkdir()\n"
            "(d / 'llamadas' / 'x.json').write_text(json.dumps({'fin': None, 'pid': os.getpid()}))\n"
            "for _ in range(100):\n"
            "    if (d / 'entrega.json').exists():\n"
            "        break\n"
            "    time.sleep(0.05)\n"
            "else:\n"
            "    raise SystemExit(2)\n"
            "(d / 'result.json').write_text(json.dumps({'sha': None}))\n"
            "time.sleep(60)\n",
            sin_avance=3,
        )
        self.assertEqual(salida.get("kind"), "veredicto", salida)
        self.assertEqual(
            json.loads((d / "entrega.json").read_text())["veredicto"], "APROBADO"
        )
        self.assertIsNone(json.loads((d / "result.ajeno.json").read_text())["sha"])

    def test_veredicto_entregado_y_salida_distinta_de_cero_no_se_pierde(self):
        """Mientras el cierre se retiene, el CLI sale con un API Error: el veredicto válido sigue valiendo."""
        salida, d, _ = self.collect_de(
            self.PRELUDIO + "veredicto('APROBADO')\n"
            "fin = time.time() + 3\n"
            "while time.time() < fin:\n"
            "    sesion.open('a').write('l\\n')\n"
            "    time.sleep(0.3)\n"
            "print('API Error: 529 overloaded')\n"
            "raise SystemExit(1)\n",
            sin_avance=30,
        )
        o = json.loads((d / "outcome.json").read_text())
        self.assertEqual(o["exit"], 1)
        self.assertEqual(salida.get("kind"), "veredicto", salida)

    def test_revisor_sin_entrega_que_sale_por_cuota_anota_el_cooldown(self):
        """El lado negativo del rescate: sin entrega congelada no hay `terminado` que lo salte, y
        el corte por cuota sigue anotando el `Cooldown` de la cuenta."""
        salida, _, daemon = self.collect_de(
            'print("You\'ve hit your usage limit")\nraise SystemExit(1)\n',
            sin_avance=30,
        )
        self.assertEqual(salida.get("kind"), "cuota", salida)
        self.assertTrue(
            any(e.kind == "Cooldown" for e in daemon.store.events("demo")), salida
        )

    def test_el_shim_registra_su_pid_mientras_corre(self):
        t = Path(tempfile.mkdtemp(prefix="runwrap-shim-"))
        d, home, bin_ = t / "intento", t / "home", t / "bin"
        for c in (d, home, bin_):
            c.mkdir()
        (d / "cupos.json").write_text(
            json.dumps({"home": str(home), "rutas": {}, "topes": {}})
        )
        real = bin_ / "kimi"
        real.write_text(
            '#!/bin/sh\ncat "$MOTOR_ATTEMPT_DIR"/llamadas/*.json > "$MOTOR_ATTEMPT_DIR"/visto.json\n'
        )
        real.chmod(0o755)
        guard = Path(runwrap.__file__).parent / "shims" / "llamada_guard.py"
        env = {
            **os.environ,
            "MOTOR_ATTEMPT_DIR": str(d),
            "PATH": str(bin_) + os.pathsep + os.environ["PATH"],
        }
        env.pop("MOTOR_LLAMADA", None)
        r = subprocess.run(
            [sys.executable, str(guard), "kimi"],
            env=env,
            capture_output=True,
            text=True,
        )
        visto = d / "visto.json"
        self.assertTrue(visto.is_file(), r.stderr)
        rec = json.loads(visto.read_text())
        self.assertIsInstance(rec["pid"], int)
        self.assertIsNone(rec["fin"])

    def test_llamada_con_shim_muerto_no_impide_el_cierre(self):
        muerto = subprocess.Popen([sys.executable, "-c", "pass"])
        muerto.wait()
        o, dura = self.corre(
            self.PRELUDIO + "veredicto('APROBADO')\n"
            "(d / 'llamadas').mkdir()\n"
            f"(d / 'llamadas' / 'x.json').write_text(json.dumps({{'fin': None, 'pid': {muerto.pid}}}))\n"
            "time.sleep(60)\n",
            extra=self.REVISOR,
        )
        self.assertIs(o["cerrado_tras_resultado"], True)
        self.assertLess(dura, 20)

    def test_solo_los_subagentes_escriben_y_impiden_el_cierre(self):
        o, _ = self.corre(
            self.PRELUDIO + "sub = sesion.with_suffix('') / 'subagents'\n"
            "sub.mkdir()\n"
            "(sub / 'x.jsonl').write_text('a\\n')\n"
            "veredicto('APROBADO')\n"
            "fin = time.time() + 4\n"
            "while time.time() < fin:\n"
            "    (sub / 'x.jsonl').open('a').write('l\\n')\n"
            "    time.sleep(0.3)\n",
            extra=self.REVISOR,
        )
        self.assertIs(o["cerrado_tras_resultado"], False)

    def test_resultado_quieto_sin_nada_mas_se_cierra_con_pstack_json(self):
        o, dura = self.corre(
            self.PRELUDIO + "veredicto('APROBADO')\ntime.sleep(60)\n",
            extra=self.REVISOR,
        )
        self.assertIs(o["cerrado_tras_resultado"], True)
        self.assertLess(dura, 20)

    def test_el_veredicto_final_reemplaza_al_provisional(self):
        _, _, d = self.corre_en(
            self.PRELUDIO + "veredicto('APROBADO')\n"
            "fin = time.time() + 4\n"
            "while time.time() < fin:\n"
            "    sesion.open('a').write('l\\n')\n"
            "    time.sleep(0.3)\n"
            "veredicto('CAMBIOS', ['falla'])\n",
            extra=self.REVISOR,
        )
        final = json.loads((d / "entrega.json").read_text())
        self.assertEqual(final["veredicto"], "CAMBIOS")

    def test_sin_resultado_sigue_esperando_al_agente(self):
        o, _ = self.corre("import time; time.sleep(3)")
        self.assertEqual((o["exit"], o["cerrado_tras_resultado"]), (0, False))

    def test_colgado_sin_resultado_sigue_siendo_sin_avance(self):
        o, _ = self.corre("import time; time.sleep(60)", sin_avance=2)
        self.assertEqual((o["no_progress"], o["cerrado_tras_resultado"]), (True, False))

    def test_agente_que_no_escribe_nada_no_toco_la_carpeta(self):
        o, _ = self.corre("print('nada')")
        self.assertIs(o["toco_carpeta"], False)

    def test_un_cambio_de_un_segundo_cuenta_aunque_salga_antes_del_escaneo(self):
        o, dura = self.corre("open('nuevo.txt', 'w').write('x')")
        self.assertLess(dura, 2)
        self.assertIs(o["toco_carpeta"], True)

    def test_sin_shims_quita_toda_carpeta_con_llamada_guard(self):
        t = Path(tempfile.mkdtemp(prefix="runwrap-shims-"))
        ajena, otra = t / "shims-de-otro-motor", t / "bin"
        for c in (ajena, otra):
            c.mkdir()
        (ajena / "llamada_guard.py").write_text("")
        for c in (ajena, otra):
            f = c / "claude"
            f.write_text("#!/bin/sh\n")
            f.chmod(0o755)
        cmd = runwrap._sin_shims(
            ["claude"], {"PATH": os.pathsep.join([str(ajena), str(otra)])}
        )
        self.assertEqual(cmd, [str(otra / "claude")])

    def test_mtime_salta_carpetas_pesadas(self):
        t = Path(tempfile.mkdtemp(prefix="runwrap-mtime-"))
        (t / "real.txt").write_text("x")
        os.utime(t / "real.txt", (1000, 1000))
        for nombre in (".git", ".venv", "node_modules", "__pycache__"):
            d = t / nombre
            d.mkdir()
            (d / "x.txt").write_text("x")
            os.utime(d / "x.txt", (2000, 2000))
        self.assertEqual(runwrap._mtime_arbol(t), 1000)

    def test_avance_se_revisa_cada_10s_no_cada_02(self):
        llamadas = []
        real = runwrap._mtime_arbol

        def cuenta(raiz):
            llamadas.append(1)
            return real(raiz)

        with unittest.mock.patch.object(runwrap, "_mtime_arbol", cuenta):
            self.corre("import time; time.sleep(3)")
        self.assertLessEqual(len(llamadas), 2)

    def test_foto_colgada_no_apaga_el_timeout(self):
        t = Path(tempfile.mkdtemp(prefix="runwrap-foto-"))
        d, cwd = t / "intento", t / "w"
        d.mkdir()
        cwd.mkdir()
        inicio = time.time()
        with unittest.mock.patch.object(
            runwrap, "foto_wip", lambda *a, **k: time.sleep(30)
        ):
            runwrap.main(
                [
                    "--dir", str(d), "--cwd", str(cwd), "--timeout", "2",
                    "--sin-avance", "60", "--cierre", "1", "--wip-rama", "wip",
                    "--wip-cada", "1",
                    "--", sys.executable, "-c", "import time; time.sleep(60)",
                ]
            )  # fmt: skip
        o = json.loads((d / "outcome.json").read_text())
        self.assertTrue(o["timed_out"])
        self.assertLess(time.time() - inicio, 15)


if __name__ == "__main__":
    unittest.main()
