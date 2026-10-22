"""`motor version` y la salida 75 del daemon cuando la conf fija otra versión."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import motor
from motor.conf import ConfError, load_conf
from motor.daemon import Daemon
from motor.version import (
    RELANZO_RAPIDO_S,
    SALIDA_RELANZAR,
    espera_antes_de_relanzar,
    info,
    pide_otra,
    sha_actual,
)
from proto import sandbox

RAIZ = Path(__file__).resolve().parent.parent
ACTUAL = {"version": "0.1.0", "sha": "3872500abcdef0123456789abcdef0123456789a"}


def motor_cli(*a: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "motor", *a],
        cwd=RAIZ,
        env={**os.environ, "PYTHONPATH": str(RAIZ)},
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


class InfoTest(unittest.TestCase):
    def test_version_del_paquete_y_sha_del_repo(self):
        i = info()
        self.assertEqual(i["version"], motor.__version__)
        self.assertRegex(i["version"], r"^\d+\.\d+\.\d+$")
        git = subprocess.run(
            ["git", "-C", str(RAIZ), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(i["sha"], git.stdout.strip())

    def test_sin_git_el_sha_lo_dice(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(sha_actual(Path(t)), "sin-git")

    def test_cli_version_en_texto_y_json(self):
        r = motor_cli("version")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(
            r.stdout.strip(),
            rf"^motor {re.escape(motor.__version__)} \([0-9a-f]{{12}}\)$",
        )
        j = json.loads(motor_cli("version", "--json").stdout)
        self.assertEqual(j, info())


class PideOtraTest(unittest.TestCase):
    def test_sin_nada_fijado_no_pide_nada(self):
        self.assertEqual(pide_otra({}, ACTUAL), "")

    def test_misma_version_y_sha_por_prefijo_no_piden_nada(self):
        self.assertEqual(pide_otra({"version": "0.1.0", "sha": "3872500"}, ACTUAL), "")

    def test_otra_version_la_dice_en_una_frase(self):
        frase = pide_otra({"version": "0.2.0"}, ACTUAL)
        self.assertIn("0.2.0", frase)
        self.assertIn("0.1.0", frase)

    def test_otro_commit(self):
        self.assertIn("deadbeef", pide_otra({"sha": "deadbeef"}, ACTUAL))

    def test_sin_git_no_cumple_un_sha_fijado(self):
        self.assertTrue(
            pide_otra({"sha": "3872500"}, {"version": "0.1.0", "sha": "sin-git"})
        )

    def test_la_version_se_revisa_antes_que_el_sha(self):
        self.assertIn("versión", pide_otra({"version": "9", "sha": "x"}, ACTUAL))


class SupervisorTest(unittest.TestCase):
    def test_75_tras_vivir_un_rato_relanza_ya(self):
        self.assertEqual(
            espera_antes_de_relanzar(SALIDA_RELANZAR, RELANZO_RAPIDO_S + 1), 0
        )

    def test_75_inmediato_no_relanza_en_caliente(self):
        self.assertGreaterEqual(
            espera_antes_de_relanzar(SALIDA_RELANZAR, 0.2), RELANZO_RAPIDO_S
        )

    def test_si_murio_espera_5_s(self):
        for codigo in (1, 3, -9):
            self.assertEqual(espera_antes_de_relanzar(codigo, 1000), 5)

    def test_el_codigo_es_75(self):
        self.assertEqual(SALIDA_RELANZAR, 75)


class DaemonTest(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix="motor-version-"))
        self.home, _ = sandbox.create(self.t / "b")
        self.general = Path(
            sandbox.__file__
        ).parent  # solo para ubicar proto; la conf del banco está en b/conf
        self.conf = self.t / "b" / "conf"

    def fija(self, bloque: str) -> None:
        g = self.conf / "general.toml"
        g.write_text(g.read_text() + "\n" + bloque)

    def test_la_conf_lee_la_version_fijada(self):
        self.fija('[motor]\nversion = "7.7.7"\nsha = "abc"\n')
        self.assertEqual(load_conf(self.conf).motor, {"version": "7.7.7", "sha": "abc"})

    def test_la_conf_rechaza_claves_raras_en_motor(self):
        for malo in (
            "[motor]\nversion = 3\n",
            '[motor]\nbranch = "x"\n',
            '[motor]\nsha = ""\n',
        ):
            with self.subTest(malo=malo):
                (self.conf / "general.toml").write_text(
                    "[tiempos]\ntimeout_s = 5\n" + malo
                )
                with self.assertRaises(ConfError):
                    load_conf(self.conf)

    def test_sin_version_fijada_la_conf_trae_motor_vacio(self):
        self.assertEqual(load_conf(self.conf).motor, {})

    def test_el_daemon_no_sale_si_la_conf_fija_una_version_que_no_esta_instalada(self):
        self.fija('[motor]\nversion = "99.0.0"\n')
        self.assertEqual(
            Daemon(self.home).run(0.05, False, 0.3),
            3,
            "sale por el tope de tiempo, no por versión",
        )

    def test_el_daemon_sigue_si_la_conf_fija_la_version_que_corre(self):
        self.fija(f'[motor]\nversion = "{motor.__version__}"\n')
        self.assertEqual(
            Daemon(self.home).run(0.05, False, 0.3),
            3,
            "sale por el tope de tiempo, no por versión",
        )

    def test_el_proceso_del_daemon_sigue_y_avisa_si_la_version_no_esta_instalada(self):
        self.fija('[motor]\nsha = "ffffffffffff"\n')
        r = motor_cli(
            "daemon", "--home", str(self.home), "--intervalo", "0.05", "--tope", "2"
        )
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertEqual(
            len(list((self.home / "avisos" / "pendientes").glob("*.txt"))), 1
        )

    def test_un_cambio_de_version_en_la_conf_se_nota_sin_reiniciar(self):
        d = Daemon(self.home)
        self.assertEqual(d.pide_otra_version(), "")
        self.fija('[motor]\nversion = "42.0.0"\n')
        subprocess.run(["git", "add", "-A"], cwd=self.conf, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=c",
                "-c",
                "user.email=c@l",
                "commit",
                "-q",
                "-m",
                "fija",
            ],
            cwd=self.conf,
            check=True,
        )
        d.recargar_conf()  # la conf compartida llega por git: una versión nueva es un SHA nuevo
        self.assertIn("42.0.0", d.pide_otra_version())


if __name__ == "__main__":
    unittest.main()
