"""El trabajo de un agente no se pierde: retomar, remoto caído, procesos huérfanos y un solo daemon."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "proto"))
sys.path.insert(0, str(RAIZ))
import sandbox  # noqa: E402

from motor import machine as m  # noqa: E402
from motor import plataforma  # noqa: E402
from motor.daemon import Daemon, OtroMotor  # noqa: E402
from motor.effects import HOLGURA_VIVO_S  # noqa: E402
from motor.store import Store  # noqa: E402
from motor.workspace import Workspace, foto_wip, git  # noqa: E402
from tests import test_motor_e2e as e2e  # noqa: E402

GIT = ("-c", "user.name=t", "-c", "user.email=t@l")


def confirma(r: subprocess.CompletedProcess) -> str:
    assert r.returncode == 0, r.stderr
    return r.stdout


class RetomaTest(unittest.TestCase):
    """Un intento se corta a la mitad; el siguiente arranca donde lo dejó el anterior."""

    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="retoma-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.nuevo("base")

    def nuevo(self, nombre: str) -> None:
        """Un origen, un motor y un estado limpios (cada subprueba parte de cero)."""
        t = self.t / nombre
        self.bare = t / "o.git"
        semilla = t / "semilla"
        git("init", "-q", "--bare", "-b", "main", str(self.bare))
        git("init", "-q", "-b", "main", str(semilla))
        (semilla / "README.md").write_text("hola\n")
        (semilla / "viejo.py").write_text("print('viejo')\n")
        git("add", "-A", cwd=semilla)
        confirma(git(*GIT, "commit", "-q", "-m", "inicio", cwd=semilla))
        confirma(git("push", "-q", str(self.bare), "main", cwd=semilla))
        self.ws = Workspace(t / "home", "Mac (prueba)")
        self.repo = str(self.bare)
        self.estado = t / "estado"
        self.rama = self.ws.rama("demo", "A")
        self.wip = self.ws.rama_wip("demo", "A")

    def agente(self, modo: str, w: Path) -> int:
        brief = self.t / "brief.md"
        brief.write_text(
            f"Trabaja en la rama `{self.rama}`.\n- A.1: escribe: nuevo.txt=ok\n"
        )
        env = {
            **os.environ,
            "FAKE_STATE": str(self.estado),
            "MOTOR_MODEL": "glm",
            "MOTOR_BLOCK": "A",
            "MOTOR_RESULT": str(self.t / "result.json"),
        }
        return subprocess.run(
            [
                sys.executable,
                str(RAIZ / "proto" / "fake_agent.py"),
                "--modo",
                modo,
                "--prompt-file",
                str(brief),
            ],
            cwd=w,
            env=env,
            capture_output=True,
            stdin=subprocess.DEVNULL,
        ).returncode

    def intento_cortado(self, modo: str, local: bool = False) -> Path:
        w = self.ws.prepara(self.repo, "demo", "A", "main")
        self.assertEqual(self.agente(modo, w), 1, "el agente falso se corta a la mitad")
        self.assertIn(foto_wip(w, self.wip, local), ("subida", "guardada"))
        return w

    def retoma(self) -> dict:
        return json.loads((self.estado / "retoma.json").read_text())

    def test_dos_commits_locales_y_un_archivo_sin_commitear_vuelven(self):
        for local in (False, True):
            for carpeta_nueva in (False, True):
                with self.subTest(repo_publico=local, carpeta_nueva=carpeta_nueva):
                    self.nuevo(f"c{int(local)}{int(carpeta_nueva)}")
                    w = self.intento_cortado("commits_sin_push", local)
                    if carpeta_nueva:
                        shutil.rmtree(
                            w
                        )  # el motor reinicia la carpeta: solo queda la foto
                    w = self.ws.prepara(self.repo, "demo", "A", "main")
                    self.assertEqual(
                        git(
                            "rev-list", "--count", "origin/main..HEAD", cwd=w
                        ).stdout.strip(),
                        "2",
                    )
                    self.assertEqual(
                        git("ls-remote", self.repo, f"refs/heads/{self.rama}").stdout,
                        "",
                        "los commits solo estaban en la carpeta y en la foto, no en la rama remota",
                    )
                    self.assertEqual(self.agente("commits_sin_push", w), 0)
                    visto = self.retoma()
                    self.assertEqual(visto["log"][:2], ["local 2", "local 1"], visto)
                    self.assertIn("?? sin_commit.txt", visto["status"])

    def test_dos_commits_locales_con_la_carpeta_limpia_vuelven(self):
        for local in (False, True):
            with self.subTest(repo_publico=local):
                self.nuevo(f"l{int(local)}")
                w = self.intento_cortado("commits_limpios", local)
                self.assertEqual(git("status", "--porcelain", cwd=w).stdout, "")
                w = self.ws.prepara(self.repo, "demo", "A", "main")
                self.assertEqual(
                    git(
                        "rev-list", "--count", "origin/main..HEAD", cwd=w
                    ).stdout.strip(),
                    "2",
                )
                self.assertEqual(git("status", "--porcelain", cwd=w).stdout, "")

    def test_lo_que_el_agente_borro_sigue_borrado(self):
        for local in (False, True):
            with self.subTest(repo_publico=local):
                self.nuevo(f"b{int(local)}")
                w = self.intento_cortado("borra_viejo", local)
                self.assertFalse((w / "viejo.py").exists())
                w = self.ws.prepara(self.repo, "demo", "A", "main")
                self.assertFalse((w / "viejo.py").exists(), "el borrado se restaura")
                self.assertIn(" D viejo.py", git("status", "--porcelain", cwd=w).stdout)
                self.assertEqual(self.agente("borra_viejo", w), 0)
                self.assertFalse(self.retoma()["viejo"])

    def test_foto_final_vacia_no_impide_traer_la_base(self):
        """La foto que runwrap toma al terminar un intento normal no trae nada: el intento 2
        debe seguir mezclando la base (MOTOR 01, bloque F)."""
        for local in (False, True):
            with self.subTest(repo_publico=local):
                self.nuevo(f"v{int(local)}")
                w = self.ws.prepara(self.repo, "demo", "A", "main")
                (w / "arreglo.txt").write_text("1\n")
                git("add", "-A", cwd=w)
                confirma(git(*GIT, "commit", "-q", "-m", "A.1", cwd=w))
                confirma(
                    git("push", "-q", "origin", f"HEAD:refs/heads/{self.rama}", cwd=w)
                )
                foto_wip(w, self.wip, local)
                otro = self.t / f"otro{int(local)}"
                git("clone", "-q", self.repo, str(otro))
                (otro / "base.txt").write_text("otro bloque mergeado\n")
                git("add", "-A", cwd=otro)
                confirma(git(*GIT, "commit", "-q", "-m", "otro bloque", cwd=otro))
                confirma(git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=otro))
                w = self.ws.prepara(self.repo, "demo", "A", "main")
                self.assertEqual(
                    git(
                        "merge-base", "--is-ancestor", "origin/main", "HEAD", cwd=w
                    ).returncode,
                    0,
                    "el intento 2 trae main",
                )

    def test_foto_que_no_desciende_de_la_punta_no_se_restaura(self):
        w = self.intento_cortado("commits_sin_push")
        # mientras tanto la rama avanzó por otro lado: la foto es de otra historia
        otro = self.t / "otro"
        git("clone", "-q", self.repo, str(otro))
        (otro / "otra.txt").write_text("x\n")
        git("add", "-A", cwd=otro)
        confirma(git(*GIT, "commit", "-q", "-m", "otra", cwd=otro))
        confirma(git("push", "-q", "origin", f"HEAD:refs/heads/{self.rama}", cwd=otro))
        w = self.ws.prepara(self.repo, "demo", "A", "main")
        self.assertEqual(git("log", "-1", "--format=%s", cwd=w).stdout.strip(), "otra")
        self.assertFalse((w / "sin_commit.txt").exists())
        self.assertFalse((w / "local1.txt").exists())

    def test_prepara_deja_la_carpeta_intacta_hasta_decidir_el_punto_de_partida(self):
        """Sin foto, una carpeta con trabajo sin commitear se reinicia a la punta (como siempre);
        con foto, el trabajo vuelve. La decisión se toma antes de tocar la carpeta."""
        w = self.ws.prepara(self.repo, "demo", "A", "main")
        (w / "x.txt").write_text("sin foto\n")
        w = self.ws.prepara(self.repo, "demo", "A", "main")
        self.assertFalse((w / "x.txt").exists())

    def test_entrega_hecha_y_luego_pasado_del_tope_cuenta_como_entregada(self):
        """C.6: el agente deja su entrega y sigue imprimiendo hasta pasarse del tope."""
        b = e2e.Banco()
        b.setUp()
        self.addCleanup(shutil.rmtree, b.tmp, True)
        home = b.arranca(
            codigo=("glm", "grok"),
            modos={"glm": "entrega_y_sigue", "grok": "ok"},
            timeout=6,
        )
        p = e2e.daemon(home)
        try:

            def terminado() -> bool:
                return any(
                    e.kind == "AttemptEnded" and e.block == "A" and e.data["n"] == 1
                    for e in e2e.eventos(home)
                )

            self.assertTrue(e2e.esperar(terminado), "el intento nunca terminó")
        finally:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
        fin = [
            e
            for e in e2e.eventos(home)
            if e.kind == "AttemptEnded" and e.block == "A" and e.data["n"] == 1
        ][0]
        self.assertEqual(fin.data["outcome"]["kind"], "entregado", fin.data["outcome"])
        salida = json.loads(
            (
                Path(
                    [
                        x
                        for x in e2e.eventos(home)
                        if x.kind == "AttemptStarted" and x.block == "A"
                    ][0].data["dir"]
                )
                / "outcome.json"
            ).read_text()
        )
        self.assertTrue(salida["timed_out"], "el agente se pasó del tope")


class PuntaTest(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="punta-"))
        self.addCleanup(shutil.rmtree, self.t, True)

    def test_remoto_que_no_contesta_no_es_una_rama_que_no_existe(self):
        home, _ = sandbox.create(self.t / "b")
        app = self.t / "b" / "app.git"
        ws = Workspace(home, "Mac (prueba)")
        self.assertEqual(ws.punta(str(app), "no/existe"), "", "rama inexistente")
        self.assertRegex(ws.punta(str(app), "main"), r"^[0-9a-f]{40}$")
        escondido = self.t / "b" / "app-escondido.git"
        app.rename(escondido)
        with self.assertRaises(RuntimeError):
            ws.punta(str(app), "main")
        with self.assertRaises(RuntimeError):
            ws.punta(str(app), "no/existe")

    def test_collect_deja_subir_la_falla_y_recoge_cuando_el_remoto_vuelve(self):
        home, plan_md = sandbox.create(self.t / "b")
        confirma(
            e2e.motor(
                "entrega", str(plan_md), "--home", str(home), "--json", "--espera", "0"
            )
        )
        d = Daemon(home)
        d.ingest_inbox()
        plan = d.plans()[0]
        app = self.t / "b" / "app.git"
        rama = d.cx.ws.rama(plan.id, "A")
        w = self.t / "w"
        git("clone", "-q", str(app), str(w))
        (w / "saludo.txt").write_text("hola\n")
        git("add", "-A", cwd=w)
        confirma(git(*GIT, "commit", "-q", "-m", "A.1", cwd=w))
        confirma(git("push", "-q", "origin", f"HEAD:refs/heads/{rama}", cwd=w))
        sha = git("rev-parse", "HEAD", cwd=w).stdout.strip()
        carpeta = d.home / "intentos" / plan.id / "A-implementar-1"
        carpeta.mkdir(parents=True)
        (carpeta / "outcome.json").write_text(
            json.dumps(
                {
                    "exit": 0,
                    "timed_out": False,
                    "no_progress": False,
                    "tail": "",
                    "fotos": [],
                    "segundos": 1,
                }
            )
        )
        (carpeta / "result.json").write_text(
            json.dumps({"sha": sha, "resumen": "hecho"})
        )
        d.store.append(
            plan.id,
            "A",
            "AttemptStarted",
            {
                "n": 1,
                "role": "implementar",
                "modelo": "glm",
                "cuenta": "zai",
                "proc": {},
                "dir": str(carpeta),
                "sha": "",
                "conf_sha": "c",
                "feedback": [],
            },
        )
        escondido = self.t / "b" / "app-escondido.git"
        app.rename(escondido)
        with self.assertRaises(RuntimeError):
            d.ingest_attempts(plan)
        d.tick()  # el tick no se cae por el remoto
        kinds = [e.kind for e in d.store.events(plan.id)]
        self.assertNotIn(
            "AttemptEnded", kinds, "no se cierra el intento con el remoto caído"
        )
        escondido.rename(app)
        d.ingest_attempts(plan)
        fin = [e for e in d.store.events(plan.id) if e.kind == "AttemptEnded"]
        self.assertEqual([e.data["outcome"]["kind"] for e in fin], ["entregado"])
        self.assertEqual(fin[0].data["outcome"]["sha"], sha)


class ProcesosTest(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="procesos-"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.home, plan_md = sandbox.create(self.t / "b")
        confirma(
            e2e.motor(
                "entrega",
                str(plan_md),
                "--home",
                str(self.home),
                "--json",
                "--espera",
                "0",
            )
        )
        self.d = Daemon(self.home)
        self.addCleanup(self.d._candado.close)
        self.d.ingest_inbox()
        self.plan = self.d.plans()[0]
        self.hijos: list[subprocess.Popen] = []
        self.addCleanup(self.limpia)

    def limpia(self):
        for p in self.hijos:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            p.wait()

    def hijo(self) -> subprocess.Popen:
        p = subprocess.Popen(
            ["sleep", "60"], start_new_session=True, stdin=subprocess.DEVNULL
        )
        self.hijos.append(p)
        return p

    def intento(self, nombre: str, proc: dict, hace_s: float = 0) -> m.Attempt:
        carpeta = self.t / f"intento-{nombre}"
        carpeta.mkdir()
        return m.Attempt(
            n=1,
            role="implementar",
            modelo="glm",
            started=time.time() - hace_s,
            proc=proc,
            dir=str(carpeta),
        )

    def collect(self, att: m.Attempt):
        snap = m.fold(self.plan, self.d.store.events(self.plan.id))
        return self.d.fx.collect(self.plan, snap, "A", att)

    def proc_de(self, p: subprocess.Popen) -> dict:
        return {
            "pid": p.pid,
            "boot_id": plataforma.boot_id(),
            "inicio": plataforma.hora_inicio(p.pid),
        }

    def test_un_segundo_daemon_sobre_el_mismo_home_sale_con_un_mensaje_claro(self):
        r = e2e.motor(
            "daemon", "--home", str(self.home), "--hasta-terminar", "--tope", "5"
        )
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("ya hay un motor corriendo", r.stderr)
        with self.assertRaises(OtroMotor):
            Daemon(self.home)
        self.d._candado.close()  # el candado se suelta al cerrar o morir el daemon
        Daemon(self.home)._candado.close()

    def test_estado_abre_la_base_en_solo_lectura_y_sin_daemon(self):
        antes = (self.home / "motor.db").read_bytes()
        self.assertFalse(
            self.d.store.solo_lectura(self.home / "motor.db")
            .db.execute("select count(*) from events")
            .fetchone()[0]
            == 0
        )
        ro = Store.solo_lectura(self.home / "motor.db")
        with self.assertRaises(Exception) as cm:
            ro.append("demo", "A", "Nota", {})
        self.assertIn("readonly", str(cm.exception).lower())
        # con un daemon vivo (tiene el candado) `estado` igual contesta, y sin base no crea nada
        r = e2e.motor("estado", "--home", str(self.home), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)[0]["plan"], "demo")
        vacio = self.t / "vacio"
        vacio.mkdir()
        r = e2e.motor("estado", "--home", str(vacio), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            list(vacio.iterdir()), [], "estado no crea la base ni el candado"
        )
        self.assertEqual((self.home / "motor.db").read_bytes(), antes)

    def test_started_se_escribe_antes_de_lanzar_el_proceso(self):
        visto = {}

        def lanza(cmd, cwd, env, log):
            carpeta = self.home / "intentos" / "demo" / "A-implementar-1"
            visto["marca"] = (carpeta / "started").exists()
            return {"pid": os.getpid(), "boot_id": plataforma.boot_id(), "inicio": ""}

        snap = m.fold(self.plan, self.d.store.events("demo"))
        with mock.patch("motor.effects.plataforma.lanzar_separado", lanza):
            self.d.fx.start_attempt(
                self.plan,
                snap,
                m.StartAttempt("A", "implementar", "glm", 1, "", (), False),
            )
        self.assertTrue(visto["marca"], "la marca existe antes de lanzar")
        marca = json.loads(
            (
                self.home / "intentos" / "demo" / "A-implementar-1" / "started"
            ).read_text()
        )
        self.assertEqual(
            marca["proc"]["pid"], os.getpid(), "tras lanzar, la marca trae el pid"
        )

    def test_si_el_daemon_cae_entre_la_marca_y_el_lanzamiento_no_se_duplica_y_se_da_por_perdido(
        self,
    ):
        snap = m.fold(self.plan, self.d.store.events("demo"))
        a = m.StartAttempt("A", "implementar", "glm", 1, "", (), False)
        with mock.patch(
            "motor.effects.plataforma.lanzar_separado", side_effect=OSError("cae")
        ):
            with self.assertRaises(OSError):
                self.d.fx.start_attempt(self.plan, snap, a)
        self.d.fx.start_attempt(
            self.plan, snap, a
        )  # el daemon vuelve: no lanza otra vez
        iniciados = [
            e for e in self.d.store.events("demo") if e.kind == "AttemptStarted"
        ]
        self.assertEqual(len(iniciados), 1)
        att = m.fold(self.plan, self.d.store.events("demo")).blocks["A"].running()
        self.assertEqual(self.collect(att)["kind"], "perdido")

    def test_perdido_mata_el_grupo_del_agente_antes_de_otro_intento(self):
        agente = self.hijo()
        att = self.intento("a", {})
        carpeta = Path(att.dir)
        (carpeta / "agent.inicio").write_text(plataforma.hora_inicio(agente.pid))
        (carpeta / "agent.pid").write_text(str(agente.pid))
        self.assertEqual(self.collect(att)["kind"], "perdido")
        self.assertIsNotNone(agente.poll(), "el agente huérfano murió")

    def test_pid_reusado_no_cuenta_como_vivo_ni_se_mata(self):
        ajeno = self.hijo()
        proc = self.proc_de(ajeno)
        self.assertEqual(plataforma.estado_proc(proc), "vivo")
        reusado = {**proc, "inicio": "Mon Jan  1 00:00:00 2001"}
        self.assertEqual(plataforma.estado_proc(reusado), "muerto")
        att = self.intento("b", reusado)
        carpeta = Path(att.dir)
        (carpeta / "agent.pid").write_text(str(ajeno.pid))
        (carpeta / "agent.inicio").write_text("Mon Jan  1 00:00:00 2001")
        self.assertEqual(self.collect(att)["kind"], "perdido")
        self.assertIsNone(ajeno.poll(), "otro proceso con el mismo pid no se toca")

    def test_vivo_pasado_de_timeout_mas_15_minutos_se_da_por_perdido(self):
        runwrap = self.hijo()
        policy = self.d.cx.policy
        att = self.intento(
            "c", self.proc_de(runwrap), hace_s=policy.timeout_s + HOLGURA_VIVO_S - 60
        )
        self.assertIsNone(self.collect(att), "dentro del margen sigue vivo")
        att = self.intento(
            "d", self.proc_de(runwrap), hace_s=policy.timeout_s + HOLGURA_VIVO_S + 60
        )
        self.assertEqual(HOLGURA_VIVO_S, 15 * 60)
        res = self.collect(att)
        self.assertEqual(res["kind"], "perdido", res)
        self.assertIsNotNone(runwrap.poll(), "el supervisor pasado de tiempo se detuvo")

    def test_outcome_que_aparece_entre_las_dos_lecturas_se_lee(self):
        att = self.intento("e", {"pid": 2**22, "boot_id": plataforma.boot_id()})
        carpeta = Path(att.dir)

        def estado(proc):
            (carpeta / "outcome.json").write_text(
                json.dumps(
                    {
                        "exit": 0,
                        "timed_out": False,
                        "no_progress": False,
                        "tail": "",
                    }
                )
            )
            return "muerto"

        with mock.patch("motor.effects.plataforma.estado_proc", estado):
            res = self.collect(att)
        self.assertEqual(
            res["kind"], "sin_resultado", "se leyó el resultado, no se dio por perdido"
        )


if __name__ == "__main__":
    unittest.main()
