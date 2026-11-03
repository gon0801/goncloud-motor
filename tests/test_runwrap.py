"""El envoltorio que corre a cada agente sin ventana."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
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


class _Reloj:
    """El reloj de runwrap avanza en su sleep. El hijo sigue en tiempo real.

    Hasta que el hijo escribe result.json, el reloj casi no avanza. Si se
    adelantara al mtime real, el cierre vería la entrega ya vieja y cerraría
    en la misma vuelta en que aparece.
    """

    def __init__(self, intento: Path, vuelta: float | None, ancla: bool):
        # Se guardan antes del parche: si no, dormir se llama a si mismo y el
        # ancla mediria el reloj falso, que nunca llega a 0,8 s.
        self._dormir = time.sleep
        self._wall = time.time
        self.t = self._wall()
        self.intento = intento
        self.vuelta = vuelta
        self.ancla = ancla
        self.libre = not ancla
        self.t0 = self.t
        self.al_cerrar = None

    def ahora(self) -> float:
        return self.t

    def dormir(self, segundos: float) -> None:
        # Cede de verdad para que el hijo escriba. 0,03 s por vuelta de 0,2 s
        # deja el hueco entre escrituras del hijo por debajo del cierre de 1 s.
        self._dormir(0.01 if not self.libre else 0.03)
        if self.ancla and not self.libre:
            if (self.intento / "result.json").exists() or self._wall() - self.t0 > 0.8:
                self.libre = True
        avance = float(segundos)
        if self.vuelta is not None and avance == 0.2:
            avance = self.vuelta
        if self.libre:
            self.t += avance
        else:
            self.t += min(avance, 0.01)


class CierreTrasResultadoTest(unittest.TestCase):
    def test_cursor_sin_sesion_conserva_el_error_del_proveedor(self):
        from motor.fallas import clasifica

        for error, esperado in (
            ("Error: Authentication required. Please run 'agent login' first.", "auth"),
            (
                "ActionRequiredError: GPT-5.6 Sol is not included in your weekly usage "
                "Buy credits or switch to a Grok model to continue.",
                "modelo_no_disponible",
            ),
        ):
            with self.subTest(causa=esperado):
                o, _, _ = self.corre_en(
                    "import sys\n"
                    f"sys.stderr.write({error!r})\n"
                    "sys.exit(1)\n"
                )
                fallo = clasifica("cursor-agent", o["tail"], o.get("stderr_tail", ""))
                self.assertEqual(fallo["kind"], esperado)

    def corre(
        self, script: str, sin_avance: float = 30, extra: tuple[str, ...] = (), **reloj
    ) -> tuple[dict, float]:
        o, dura, _ = self.corre_en(script, sin_avance, extra, **reloj)
        return o, dura

    def corre_en(
        self,
        script: str,
        sin_avance: float = 30,
        extra: tuple[str, ...] = (),
        *,
        vuelta: float | None = None,
        ancla: bool = True,
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
        self.reloj = _Reloj(d, vuelta, ancla)
        inicio = time.time()
        real_outcome = runwrap._outcome

        def guarda(destino, datos):
            self.reloj.al_cerrar = self.reloj.t
            return real_outcome(destino, datos)

        with (
            unittest.mock.patch.object(runwrap.time, "time", self.reloj.ahora),
            unittest.mock.patch.object(runwrap.time, "sleep", self.reloj.dormir),
            unittest.mock.patch.object(runwrap, "_outcome", guarda),
        ):
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
            "fin = time.time() + 0.55\n"
            "while time.time() < fin:\n"
            "    for f in (sesion, sub / 'x.jsonl'):\n"
            "        f.open('a').write('l\\n')\n"
            "    time.sleep(0.05)\n",
            extra=self.REVISOR,
        )
        self.assertEqual((o["cerrado_tras_resultado"], o["exit"]), (False, 0))

    def test_llamada_sin_fin_impide_el_cierre_hasta_que_se_llena(self):
        """El paso de abierta a cerrada cuenta como avance: el cierre llega una vuelta
        después de la primera lectura que ve `fin` lleno. Esa lectura se mide en el
        reloj del wrapper, no en la hora de `fin`. La vuelta de 0,2 s se estira a 1 s
        en ese reloj. Sin ese estirón, o si el cierre cae en la misma vuelta, la
        diferencia queda por debajo de 1 s."""
        real_huella = runwrap._huella_pstack
        lecturas = []

        def huella_anotada(d):
            huella = real_huella(d)
            lecturas.append((self.reloj.ahora(), huella[1]))
            return huella

        with unittest.mock.patch.object(runwrap, "_huella_pstack", huella_anotada):
            o, _, _d = self.corre_en(
                self.PRELUDIO + "(d / 'llamadas').mkdir()\n"
                "f = d / 'llamadas' / 'x.json'\n"
                "f.write_text(json.dumps({'fin': None, 'pid': os.getpid()}))\n"
                "veredicto('APROBADO')\n"
                "time.sleep(0.12)\n"
                "f.write_text(json.dumps({'fin': time.time(), 'pid': os.getpid()}))\n"
                "time.sleep(30)\n",
                sin_avance=80,
                extra=self.REVISOR,
                vuelta=1.0,
                ancla=False,
            )
        self.assertIs(o["cerrado_tras_resultado"], True)
        self.assertGreaterEqual(
            min(b - a for (a, _), (b, _) in zip(lecturas, lecturas[1:])), 1.0
        )  # la vuelta quedó estirada: si runwrap deja de dormir 0,2 s, esto suena
        vio_abierta = False
        primera = None
        for marca, abierta in lecturas:
            if abierta:
                vio_abierta = True
            elif vio_abierta and primera is None:
                primera = marca
        self.assertIsNotNone(primera)
        self.assertGreaterEqual(self.reloj.al_cerrar - primera, 1.0)

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
            "fin = time.time() + 0.55\n"
            "while time.time() < fin:\n"
            "    sesion.open('a').write('l\\n')\n"
            "    time.sleep(0.05)\n"
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
            "fin = time.time() + 0.55\n"
            "while time.time() < fin:\n"
            "    (sub / 'x.jsonl').open('a').write('l\\n')\n"
            "    time.sleep(0.05)\n",
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
            "fin = time.time() + 0.55\n"
            "while time.time() < fin:\n"
            "    sesion.open('a').write('l\\n')\n"
            "    time.sleep(0.05)\n"
            "veredicto('CAMBIOS', ['falla'])\n",
            extra=self.REVISOR,
        )
        final = json.loads((d / "entrega.json").read_text())
        self.assertEqual(final["veredicto"], "CAMBIOS")

    def test_sin_resultado_sigue_esperando_al_agente(self):
        o, _ = self.corre("import time; time.sleep(0.05)")
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
            self.corre("import time; time.sleep(0.45)")
        self.assertLessEqual(len(llamadas), 2)

    def test_foto_colgada_no_apaga_el_timeout(self):
        t = Path(tempfile.mkdtemp(prefix="runwrap-foto-"))
        d, cwd = t / "intento", t / "w"
        d.mkdir()
        cwd.mkdir()

        def foto_colgada(*_a, **_k):
            if threading.current_thread() is threading.main_thread():
                time.sleep(3)

        inicio = time.time()
        with unittest.mock.patch.object(runwrap, "foto_wip", foto_colgada):
            runwrap.main(
                [
                    "--dir", str(d), "--cwd", str(cwd), "--timeout", "0.4",
                    "--sin-avance", "60", "--cierre", "1", "--wip-rama", "wip",
                    "--wip-cada", "0.2",
                    "--", sys.executable, "-c", "import time; time.sleep(30)",
                ]
            )  # fmt: skip
        o = json.loads((d / "outcome.json").read_text())
        self.assertTrue(o["timed_out"])
        self.assertLess(time.time() - inicio, 2)


if __name__ == "__main__":
    unittest.main()
