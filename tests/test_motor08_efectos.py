import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from motor import huella
from motor import machine as m
from motor.effects import Effects
from motor.formato import puede_volver
from motor.plan import parse_plan
from motor.store import Store
from tests.test_puro import PLAN

SHA = "a" * 40
RAIZ = Path(__file__).resolve().parents[1]


class RecuperaEfectosTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.store = Store(self.home / "motor.db")
        self.addCleanup(self.store.db.close)
        self.plan = parse_plan(PLAN)
        self.fx = Effects(NS(home=self.home, store=self.store, programadas_sistema=False))
        self.fx.env_base = lambda *args: {}
        self.fx._huellas = lambda campo, block, snap: (
            huella.insumo_de(campo, block, snap.repos),
            "maquina",
        )
        self.fx._avisa_incierto = lambda *args: None

    def test_caida_tras_lanzar_recupera_resultado_sin_repetir_instalacion(self):
        snap = m.fold(self.plan, [])
        append = self.store.append

        def cae(plan, block, kind, *args, **kwargs):
            if kind == "DeployStarted":
                raise SystemExit("caida despues de lanzar")
            return append(plan, block, kind, *args, **kwargs)

        def lanzar(cmd, *args):
            d = Path(cmd[cmd.index("--dir") + 1])
            spec = json.loads((d / "spec.json").read_text())
            (d / "outcome.json").write_text(
                json.dumps(
                    {
                        "data": {
                            "sha": SHA,
                            "ok": True,
                            "huella": spec["huella"],
                            "huella_maquina": spec["huella_maquina"],
                        }
                    }
                )
            )
            return {"pid": 42}

        with patch("motor.effects.plataforma.lanzar_separado", side_effect=lanzar) as lanzamientos:
            self.store.append = cae
            with self.assertRaises(SystemExit):
                self.fx.deploy(self.plan, snap, m.Deploy("A", SHA))
            self.store.append = append
            self.fx.deploy(self.plan, snap, m.Deploy("A", SHA))
            self.assertEqual(lanzamientos.call_count, 1)
        self.assertTrue(self.store.events(self.plan.id, "Deployed")[-1].data["ok"])
        self.assertTrue(puede_volver(self.home, self.store.events())[0])

    def test_receta_y_maquina_nuevas_instalan_sin_repetir_la_anterior(self):
        snap = m.fold(self.plan, [])
        ident = self.fx._confirma_intencion(self.plan, "A", "instalar", SHA, "recibo")
        self.fx._efecto(self.plan, "A", ident, "aplicado")
        d = self.home / "anterior"
        d.mkdir()
        datos = {"sha": SHA, "ok": True, "huella": "anterior", "huella_maquina": "maquina"}
        (d / "outcome.json").write_text(json.dumps({"data": datos}))
        self.store.append(self.plan.id, "A", "DeployStarted", {"sha": SHA, "dir": str(d), "n": 1})
        self.store.append(self.plan.id, "A", "Deployed", datos)

        def lanzar(cmd, *args):
            d = Path(cmd[cmd.index("--dir") + 1])
            spec = json.loads((d / "spec.json").read_text())
            (d / "outcome.json").write_text(
                json.dumps(
                    {
                        "data": {
                            "sha": SHA,
                            "ok": True,
                            "huella": spec["huella"],
                            "huella_maquina": spec["huella_maquina"],
                        }
                    }
                )
            )
            return {}

        with patch("motor.effects.plataforma.lanzar_separado", side_effect=lanzar) as lanzamientos:
            for insumo, maquina in [("nueva", "maquina"), ("nueva", "otra-maquina")]:
                self.fx._huellas = lambda *args, h=insumo, mq=maquina: (h, mq)
                self.fx.deploy(self.plan, snap, m.Deploy("A", SHA))
                self.fx.deploy(self.plan, snap, m.Deploy("A", SHA))
                self.fx.deploy(self.plan, snap, m.Deploy("A", SHA))
            self.assertEqual(lanzamientos.call_count, 2)
        self.assertEqual(len(self.store.events(self.plan.id, "Deployed")), 3)
        self.assertTrue(puede_volver(self.home, self.store.events())[0])

    def test_resultado_de_otro_plan_no_autoriza_escritor_anterior(self):
        uno, dos = NS(id="uno"), NS(id="dos")
        i1 = self.fx._confirma_intencion(uno, "A", "instalar", SHA, "recibo")
        i2 = self.fx._confirma_intencion(dos, "A", "instalar", SHA, "recibo")
        self.fx._efecto(dos, "A", i2, "aplicado")
        self.assertFalse(puede_volver(self.home, self.store.events())[0])
        self.fx._efecto(uno, "A", i1, "no_aplicado")
        self.assertTrue(puede_volver(self.home, self.store.events())[0])

    def test_reinstalar_y_fallar_checklist_vuelve_a_revision_real_sin_autorizar_escritor_viejo(
        self,
    ):
        usuario = self.home / "usuario"
        usuario.mkdir()
        (usuario / ".motor").symlink_to(self.home, target_is_directory=True)
        fuente, remoto, conf = (self.home / n for n in ("fuente", "remoto.git", "conf"))

        def git(*args):
            return subprocess.run(
                ["git", *args], check=True, text=True, capture_output=True
            ).stdout.strip()

        def inicia(d):
            d.mkdir()
            git("init", "-q", "-b", "main", str(d))
            git("-C", str(d), "config", "user.name", "Prueba")
            git("-C", str(d), "config", "user.email", "prueba@localhost")
            git("-C", str(d), "config", "core.hooksPath", "/dev/null")
            git("-C", str(d), "config", "commit.gpgsign", "false")

        def commit(d, texto):
            git("-C", str(d), "add", "-A")
            git("-C", str(d), "commit", "-q", "-m", texto)
            return git("-C", str(d), "rev-parse", "HEAD")

        inicia(fuente)
        paquete = fuente / "motor"
        paquete.mkdir()
        (paquete / "__init__.py").write_text("")
        (paquete / "__main__.py").write_text(
            "import sys\nfrom pathlib import Path\n"
            "if sys.argv[1] == 'formato':\n"
            " from motor.formato import puede_volver\n from motor.store import Store\n"
            " home = Path(sys.argv[sys.argv.index('--home') + 1])\n"
            " ok, motivo = puede_volver(home, Store.solo_lectura(home / 'motor.db').events())\n"
            " print(motivo)\n raise SystemExit(0 if ok else 1)\n"
        )
        viejo = commit(fuente, "Escritor anterior")
        git("-C", str(fuente), "tag", "anterior", viejo)
        for nombre in ("formato.py", "store.py"):
            shutil.copy2(RAIZ / "motor" / nombre, paquete / nombre)
        (fuente / "ops").mkdir()
        shutil.copy2(RAIZ / "ops/sol_remoto.py", fuente / "ops/sol_remoto.py")
        actual = commit(fuente, "Candidato")
        git("-C", str(fuente), "tag", "candidato", actual)
        git("clone", "-q", "--bare", str(fuente), str(remoto))
        git("-C", str(fuente), "remote", "add", "origin", str(remoto))
        instalado = usuario / "motor"
        git("clone", "-q", str(remoto), str(instalado))
        git("-C", str(instalado), "checkout", "-q", "--detach", "candidato")
        inicia(conf)
        (conf / "maquinas").mkdir()
        (conf / "maquinas/mac.toml").write_text("nombre = 'mac'\n")
        commit(conf, "Conf local")
        conf_remota = self.home / "conf.git"
        git("clone", "-q", "--bare", str(conf), str(conf_remota))
        ops = self.home / "ops"
        ops.mkdir()
        for nombre in ("instala-todo.sh", "vuelve-a-la-anterior.sh"):
            shutil.copy2(RAIZ / "ops" / nombre, ops / nombre)
        (ops / "lib.sh").write_text(
            "MAQUINAS=mac\nETIQUETA=candidato\n"
            'REPO_URL="$PRUEBA_REPO"\nCONF_URL="$PRUEBA_CONF"\n'
            'en_posix() { shift; bash -s -- "$@"; }\n'
            'reintenta() { shift; "$@"; }\n'
            'sha_publicado() { git -C "$1" ls-remote --tags origin "refs/tags/$ETIQUETA" | cut -f1; }\n'
        )
        (self.home / "etiqueta-anterior").write_text("anterior")
        propia = self.fx._confirma_intencion(self.plan, "A", "instalar", actual, "recibo")
        otra = self.fx._confirma_intencion(NS(id="otro"), "B", "instalar", actual, "recibo")
        self.fx._efecto(NS(id="otro"), "B", otra, "incierto")
        env = {
            **os.environ,
            "HOME": str(usuario),
            "MOTOR_SHA": actual,
            "MOTOR_INSTALA_RAIZ": str(fuente),
            "MOTOR_RAMA": "main",
            "PRUEBA_REPO": str(remoto),
            "PRUEBA_CONF": str(conf_remota),
        }
        instalacion = subprocess.run(
            ["bash", str(ops / "instala-todo.sh")], env=env, capture_output=True, text=True
        )
        self.assertEqual(instalacion.returncode, 0, instalacion.stdout + instalacion.stderr)
        self.assertEqual((self.home / "etiqueta-anterior").read_text().strip(), actual)
        self.assertEqual(
            (self.home / "herramientas/sol_remoto.py").read_bytes(),
            (fuente / "ops/sol_remoto.py").read_bytes(),
        )
        checklist = subprocess.run(["bash", "-c", "exit 17"], env=env)
        self.assertEqual(checklist.returncode, 17)
        self.fx.cx.maquina = NS(bash="bash")
        pasos = []
        self.assertTrue(
            self.fx._rollback(str(ops / "vuelve-a-la-anterior.sh"), fuente, env, 30, pasos), pasos
        )
        self.assertEqual(git("-C", str(instalado), "rev-parse", "HEAD"), actual)
        self.assertIsNone(self.fx._ultimo_efecto(self.plan, propia))
        self.assertFalse(puede_volver(self.home, self.store.events())[0])
        (self.home / "etiqueta-anterior").write_text("anterior")
        self.assertFalse(
            self.fx._rollback(str(ops / "vuelve-a-la-anterior.sh"), fuente, env, 30, pasos)
        )
        self.assertEqual(git("-C", str(instalado), "rev-parse", "HEAD"), actual)
        self.fx._efecto(self.plan, "A", propia, "no_aplicado")
        self.fx._efecto(NS(id="otro"), "B", otra, "no_aplicado")
        (self.home / "etiqueta-anterior").write_text(viejo)
        self.assertTrue(
            self.fx._rollback(str(ops / "vuelve-a-la-anterior.sh"), fuente, env, 30, pasos), pasos
        )
        self.assertEqual(git("-C", str(instalado), "rev-parse", "HEAD"), viejo)
        self.assertTrue((self.home / "herramientas/sol_remoto.py").exists())


if __name__ == "__main__":
    unittest.main()
