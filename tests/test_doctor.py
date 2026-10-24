"""`motor doctor --como-servicio`: una ruta falsa que responde y otra que no, en el entorno del servicio."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from motor.doctor import como_servicio, entorno_del_servicio
from motor.servicio import PATH_SERVICIO, plist_launchd

RAIZ = Path(__file__).resolve().parent.parent
PY = sys.executable

# Responde solo si ve el entorno de un servicio: lo de maquina.toml y secretos.env, el PATH de
# path_extra + servicio, stdin nulo y NADA de la terminal que lo lanzó.
RESPONDE = """
import json, os, sys
assert os.environ["DE_MAQUINA"] == "1", "falta el env de maquina.toml"
assert os.environ["DE_SECRETOS"] == "llave", "falta secretos.env"
assert os.environ["DE_RUTA"] == "llave", "falta el env de la ruta (con ${VAR} de secretos)"
partes = os.environ["PATH"].split(os.pathsep)
assert partes[0] == sys.argv[1], "path_extra va primero: %r" % partes
assert partes[1:] == sys.argv[2].split(os.pathsep), "el resto es el PATH del servicio: %r" % partes
assert "SOLO_EN_MI_TERMINAL" not in os.environ, "heredó el entorno de la terminal"
assert sys.stdin.read() == "", "stdin debía ser nulo"
open(os.environ["MOTOR_RESULT"], "w").write(json.dumps({"ok": True}))
print("OK")
"""
NO_RESPONDE = (
    "import sys; sys.stderr.write('Not logged in - Please run /login\\n'); sys.exit(1)"
)
CUELGA = "import time; time.sleep(60)"
CALLA = "import sys; sys.exit(0)"


class Banco(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="motor-doctor-"))
        self.home, conf = self.t / "home", self.t / "conf"
        self.home.mkdir()
        (conf / "reglas").mkdir(parents=True)
        self.extra = self.t / "extra-bin"
        self.extra.mkdir()

    def arma(
        self,
        rutas: dict[str, str],
        papeles: dict[str, list[str]] | None = None,
        args: dict | None = None,
    ):
        conf = self.t / "conf"
        papeles = papeles or {"implementar": list(rutas), "revisar": list(rutas)}
        (conf / "papeles.toml").write_text(
            '[papeles]\nimplementar = "uno"\nrevisar = "dos"\n'
        )
        (conf / "modelos.toml").write_text(
            f"[carriles]\nuno = {json.dumps(papeles['implementar'])}\ndos = {json.dumps(papeles['revisar'])}\n"
        )
        bloques = []
        for mo, codigo in rutas.items():
            script = self.t / f"{mo}.py"
            script.write_text(codigo)
            bloques.append(
                f'[rutas.{mo}]\ncuenta = "{mo}"\ncli = "{mo}"\n'
                f'argv = ["{PY}", "{script}", {json.dumps(str(self.extra))}, {json.dumps(PATH_SERVICIO)}, "{{brief}}"]\n'
                f'env = {{ DE_RUTA = "${{DE_SECRETOS}}" }}\n'
            )
        (conf / "rutas.toml").write_text("\n".join(bloques))
        subprocess.run(["git", "init", "-q"], cwd=conf, check=True)
        (self.home / "maquina.toml").write_text(
            f'nombre = "Mac (prueba)"\nconf = "{conf}"\npath_extra = ["{self.extra}"]\n[env]\nDE_MAQUINA = "1"\n'
        )
        sec = self.home / "secretos.env"
        sec.write_text("DE_SECRETOS=llave\n")
        sec.chmod(0o600)
        os.environ["SOLO_EN_MI_TERMINAL"] = "1"
        self.addCleanup(os.environ.pop, "SOLO_EN_MI_TERMINAL", None)


IMPRIME = """
import json, os, sys
open(sys.argv[1], "w").write(json.dumps(sys.argv[2:]))
open(os.environ["MOTOR_RESULT"], "w").write(json.dumps({"ok": True}))
print("OK")
"""


class DatosDeRutaTest(Banco):
    def ruta(self, nombre: str, codigo: str, *datos: str) -> str:
        script = self.t / f"{nombre}.py"
        script.write_text(codigo)
        argv = [PY, str(script), str(self.t / f"{nombre}.recibido"), *datos, "{brief}"]
        return (
            f'[rutas.{nombre}]\ncuenta = "{nombre}"\ncli = "{nombre}"\n'
            f"argv = {json.dumps(argv)}\n"
        )

    def arma(self, rutas: list[str], modelos: list[str]):
        conf = self.t / "conf"
        (conf / "papeles.toml").write_text(
            '[papeles]\nimplementar = "uno"\nrevisar = "uno"\n'
        )
        (conf / "modelos.toml").write_text(f"[carriles]\nuno = {json.dumps(modelos)}\n")
        (conf / "rutas.toml").write_text("\n".join(rutas))
        (conf / "pstack.toml").write_text('[pstack]\ncommit = "a"\n')
        (conf / "pstack" / "skills").mkdir(parents=True)
        (conf / "pstack" / "skills" / "x.md").write_text("x")
        (self.home / "maquina.toml").write_text(
            f'nombre = "Mac (prueba)"\nconf = "{conf}"\n'
        )

    def recibido(self, nombre: str) -> list[str]:
        return json.loads((self.t / f"{nombre}.recibido").read_text())

    def test_pstack_y_sesion_llegan_a_la_ruta(self):
        """Antes truenaba con KeyError: 'pstack' en toda ruta que lo usa (sonnet, opus, k3, deepseek)."""
        self.arma(
            [self.ruta("nueva", IMPRIME, "--p", "{pstack}", "--s", "{sesion}")],
            ["nueva"],
        )
        r = como_servicio(self.home, espera_s=20)
        self.assertTrue(r["rutas"]["nueva"]["responde"], r)
        _, pstack, _, sesion, _ = self.recibido("nueva")
        copia = Path(pstack)
        self.assertTrue((copia / "pstack" / "skills" / "x.md").is_file(), copia)
        self.assertEqual(copia.parent, self.home / "pstack")
        self.assertEqual(str(uuid.UUID(sesion)), sesion)

    def test_una_ruta_que_truena_no_tumba_a_las_otras(self):
        self.arma(
            [self.ruta("buena", IMPRIME), self.ruta("rota", IMPRIME, "{otra}")],
            ["buena", "rota"],
        )
        r = como_servicio(self.home, espera_s=20)
        self.assertTrue(r["rutas"]["buena"]["responde"], r)
        self.assertFalse(r["rutas"]["rota"]["responde"], r)
        self.assertEqual(
            r["rutas"]["rota"]["motivo"], "no pude armar la llamada: KeyError"
        )
        self.assertTrue(r["ok"], "el papel tiene al menos una ruta que responde")


class ComoServicioTest(Banco):
    def test_una_ruta_que_responde_y_otra_que_no(self):
        self.arma(
            {"bueno": RESPONDE, "malo": NO_RESPONDE},
            {"implementar": ["bueno", "malo"], "revisar": ["bueno"]},
        )
        r = como_servicio(self.home, espera_s=20)
        self.assertTrue(r["rutas"]["bueno"]["responde"], r)
        self.assertFalse(r["rutas"]["malo"]["responde"], r)
        self.assertEqual(
            r["rutas"]["malo"]["kind"], "auth", "usa la clasificación por proveedor"
        )
        self.assertIn("Not logged in", r["rutas"]["malo"]["motivo"])
        self.assertTrue(r["ok"], "cada papel tiene al menos una ruta que responde")
        self.assertTrue(any(p.startswith("malo:") for p in r["problemas"]))

    def test_carpeta_ocupada_al_terminar_no_tumba_el_doctor(self):
        """MOTOR 01, PC: Windows no dejó borrar la carpeta temporal (un hijo por WSL la ocupaba) y
        el PermissionError tumbaba toda la revisión de la PC."""
        from unittest import mock

        class Ocupada(tempfile.TemporaryDirectory):
            def cleanup(self):
                if not self._ignore_cleanup_errors:
                    raise PermissionError(
                        32, "The process cannot access the file", self.name
                    )
                super().cleanup()

        self.arma({"bueno": RESPONDE}, {"implementar": ["bueno"], "revisar": ["bueno"]})
        with mock.patch("motor.doctor.tempfile.TemporaryDirectory", Ocupada):
            r = como_servicio(self.home, espera_s=20)
        self.assertTrue(r["rutas"]["bueno"]["responde"], r)

    def test_no_ok_si_a_un_papel_no_le_responde_nadie(self):
        self.arma(
            {"bueno": RESPONDE, "malo": NO_RESPONDE},
            {"implementar": ["bueno"], "revisar": ["malo"]},
        )
        r = como_servicio(self.home, espera_s=20)
        self.assertFalse(r["ok"])
        self.assertTrue(any("revisar" in p for p in r["problemas"]), r["problemas"])

    def test_una_ruta_que_cuelga_se_corta_en_el_plazo(self):
        self.arma(
            {"bueno": RESPONDE, "cuelga": CUELGA},
            {"implementar": ["bueno"], "revisar": ["bueno"]},
        )
        r = como_servicio(self.home, espera_s=2)
        self.assertEqual(
            (r["rutas"]["cuelga"]["responde"], r["rutas"]["cuelga"]["kind"]),
            (False, "tiempo"),
        )
        self.assertLess(r["rutas"]["cuelga"]["segundos"], 10)
        self.assertTrue(r["rutas"]["bueno"]["responde"])

    def test_salir_bien_sin_decir_nada_no_es_responder(self):
        self.arma(
            {"bueno": RESPONDE, "calla": CALLA},
            {"implementar": ["bueno"], "revisar": ["bueno"]},
        )
        r = como_servicio(self.home, espera_s=20)
        self.assertFalse(r["rutas"]["calla"]["responde"], r)

    def test_un_ejecutable_que_no_existe_se_dice(self):
        self.arma({"bueno": RESPONDE}, {"implementar": ["bueno"], "revisar": ["bueno"]})
        rutas = self.t / "conf" / "rutas.toml"
        rutas.write_text(
            rutas.read_text()
            + '\n[rutas.fantasma]\ncuenta = "fantasma"\ncli = "fantasma"\nargv = ["/no/existe/nada", "{brief}"]\n'
        )
        r = como_servicio(self.home, espera_s=20)
        self.assertFalse(r["rutas"]["fantasma"]["responde"])
        self.assertIn("no pude lanzarlo", r["rutas"]["fantasma"]["motivo"])

    def test_conf_rota_no_truena(self):
        (self.home / "maquina.toml").write_text('nombre = "X"\nconf = "/no/existe"\n')
        r = como_servicio(self.home, espera_s=2)
        self.assertFalse(r["ok"])
        self.assertTrue(r["problemas"])


class EntornoTest(Banco):
    def test_el_entorno_no_hereda_la_terminal_y_pone_path_extra_primero(self):
        self.arma({"bueno": RESPONDE})
        from motor.conf import load_conf, load_maquina

        mq = load_maquina(self.home)
        env = entorno_del_servicio(
            mq, load_conf(mq.conf).rutas["bueno"], self.t / "r.json"
        )
        self.assertNotIn("SOLO_EN_MI_TERMINAL", env)
        self.assertEqual(env["PATH"].split(os.pathsep)[0], str(self.extra))
        self.assertEqual(env["DE_RUTA"], "llave")
        self.assertEqual(env["MOTOR_RESULT"], str(self.t / "r.json"))

    def test_el_plist_del_servicio_usa_el_mismo_path(self):
        self.assertIn(
            f"<key>PATH</key><string>{PATH_SERVICIO}</string>",
            plist_launchd("u", "/py", "/h", "/r"),
        )


class CliTest(Banco):
    def motor(self, *a: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [PY, "-m", "motor", *a],
            cwd=RAIZ,
            env={**os.environ, "PYTHONPATH": str(RAIZ)},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def test_cli_dice_cuales_responden_y_sale_con_1_si_falta_un_papel(self):
        self.arma(
            {"bueno": RESPONDE, "malo": NO_RESPONDE},
            {"implementar": ["bueno"], "revisar": ["bueno"]},
        )
        r = self.motor(
            "doctor",
            "--home",
            str(self.home),
            "--como-servicio",
            "--json",
            "--espera",
            "20",
        )
        d = json.loads(r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(
            {k: v["responde"] for k, v in d["rutas"].items()},
            {"bueno": True, "malo": False},
        )
        self.arma({"malo": NO_RESPONDE})
        r = self.motor(
            "doctor",
            "--home",
            str(self.home),
            "--como-servicio",
            "--json",
            "--espera",
            "20",
        )
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
