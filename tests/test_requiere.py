"""`tiene` en maquina.toml y `[deploy] requiere` en .motor.toml: el motor rechaza al entregar un plan
cuyo repo pide algo que esta máquina no tiene, con el motivo en una frase llana."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import tomllib


from proto import sandbox
from motor.conf import (
    ConfError,
    RepoConf,
    load_maquina,
    motivo_requiere,
    requisitos_faltantes,
    valida_maquina,
)
from motor.daemon import Daemon
from motor.notify import lint_plain
from motor.store import Store


def motor(*a: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "motor", *a],
        check=False,
        cwd=RAIZ,
        env={**os.environ, "PYTHONPATH": str(RAIZ)},
        capture_output=True,
        text=True,
        timeout=60,
    )


RAIZ = Path(__file__).resolve().parent.parent
BASE = {"nombre": "Mac", "conf": "/x"}


class EsquemaTest(unittest.TestCase):
    def test_tiene_es_una_clave_valida(self):
        valida_maquina({**BASE, "tiene": ["ssh-goncloud", "docker"]})

    def test_tiene_vacio_o_ausente_tambien_vale(self):
        valida_maquina({**BASE, "tiene": []})
        valida_maquina(BASE)

    def test_tiene_debe_ser_lista_de_nombres(self):
        for malo in ("ssh-goncloud", [1], [""], ["a", None], {"a": 1}):
            with self.subTest(malo=malo), self.assertRaises(ConfError):
                valida_maquina({**BASE, "tiene": malo})

    def test_el_esquema_sigue_cerrado(self):
        with self.assertRaises(ConfError):
            valida_maquina({**BASE, "tienen": ["x"]})

    def test_load_maquina_lee_tiene(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "maquina.toml").write_text(
                'nombre = "Mini"\nconf = "/x"\ntiene = ["ssh-goncloud"]\n'
            )
            self.assertEqual(load_maquina(Path(t)).tiene, ("ssh-goncloud",))
            (Path(t) / "maquina.toml").write_text('nombre = "Mini"\nconf = "/x"\n')
            self.assertEqual(load_maquina(Path(t)).tiene, ())


class PuroTest(unittest.TestCase):
    def test_lo_que_falta(self):
        self.assertEqual(
            requisitos_faltantes(("ssh-goncloud", "docker"), ("docker",)),
            ("ssh-goncloud",),
        )
        self.assertEqual(
            requisitos_faltantes(("a", "a", "b"), ()),
            ("a", "b"),
            "sin repetidos, en orden",
        )

    def test_sin_requisitos_nunca_falta_nada(self):
        self.assertEqual(requisitos_faltantes((), ()), ())
        self.assertEqual(requisitos_faltantes((), ("ssh-goncloud",)), ())

    def test_el_repo_lee_su_requiere(self):
        rc = RepoConf.parse('[deploy]\ndesplegar = "x"\nrequiere = ["ssh-goncloud"]\n')
        self.assertEqual(rc.requiere, ("ssh-goncloud",))

    def test_el_motivo_es_una_frase_llana(self):
        frase = motivo_requiere(
            "la Mac", "git@github.com:gon0801/goncloud-motor.git", ("ssh-goncloud",)
        )
        lint_plain(frase)
        self.assertIn("ssh-goncloud", frase)
        self.assertIn("goncloud-motor", frase)
        self.assertNotIn("git@", frase)
        self.assertNotIn("/", frase)


class EntregaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="motor-requiere-"))
        self.home, self.plan = sandbox.create(self.tmp / "b")
        seed = self.tmp / "b" / "seed-app"
        toml = (
            (seed / ".motor.toml")
            .read_text()
            .replace("requiere = []", 'requiere = ["ssh-goncloud"]')
        )
        (seed / ".motor.toml").write_text(toml)
        for cmd in (
            ["add", "-A"],
            [
                "-c",
                "user.name=s",
                "-c",
                "user.email=s@l",
                "commit",
                "-q",
                "-m",
                "requiere",
            ],
            ["push", "-q", str(self.tmp / "b" / "app.git"), "main"],
        ):
            sandbox.sh("git", *cmd, cwd=seed)

    def entrega(self) -> dict:
        r = motor(
            "entrega",
            str(self.plan),
            "--home",
            str(self.home),
            "--json",
            "--espera",
            "0",
        )
        cmd = json.loads(r.stdout)["cmd_id"]
        d = Daemon(self.home)
        d.ingest_inbox()
        rec = json.loads(
            motor("recibo", cmd, "--home", str(self.home), "--json").stdout
        )
        return {**rec, "planes": d.plans()}

    def test_se_rechaza_si_la_maquina_no_lo_tiene(self):
        rec = self.entrega()
        self.assertEqual(rec["estado"], "rechazado", rec)
        self.assertIn("ssh-goncloud", rec["detalle"])
        self.assertIn("Mac (prueba)", rec["detalle"])
        self.assertEqual(rec["planes"], [], "un plan rechazado no queda cargado")
        self.assertFalse(
            [
                e
                for e in Store(self.home / "motor.db").events()
                if e.kind == "PlanLoaded"
            ]
        )

    def test_se_toma_si_la_maquina_lo_declara(self):
        mq = self.home / "maquina.toml"
        mq.write_text('tiene = ["ssh-goncloud"]\n' + mq.read_text())
        tomllib.loads(mq.read_text())
        rec = self.entrega()
        self.assertEqual(rec["estado"], "tomado", rec)
        self.assertEqual(len(rec["planes"]), 1)

    def test_entregar_el_plan_con_cambios_lo_reemplaza(self):
        """INGESTA 01: la prueba real de S corría docker en la Mini. Reentregar el plan corregido
        decía "tomado" y el motor seguía con el texto viejo."""
        mq = self.home / "maquina.toml"
        mq.write_text('tiene = ["ssh-goncloud"]\n' + mq.read_text())
        self.entrega()
        self.assertEqual(self.entrega()["estado"], "tomado")

        def cargas():
            return [
                e
                for e in Store(self.home / "motor.db").events()
                if e.kind == "PlanLoaded"
            ]

        self.assertEqual(len(cargas()), 1, "el mismo texto otra vez no hace nada")
        self.plan.write_text(
            self.plan.read_text(encoding="utf-8").replace(
                "prueba-real: true", "prueba-real: test -f saludo.txt", 1
            ),
            encoding="utf-8",
        )
        rec = self.entrega()
        self.assertEqual(rec["estado"], "tomado", rec)
        self.assertEqual(len(cargas()), 2)
        self.assertEqual(len(rec["planes"]), 1)
        self.assertEqual(rec["planes"][0].blocks[0].prueba_real, "test -f saludo.txt")

    def test_otra_capacidad_no_basta(self):
        mq = self.home / "maquina.toml"
        mq.write_text('tiene = ["docker"]\n' + mq.read_text())
        self.assertEqual(self.entrega()["estado"], "rechazado")

    def test_motor_entrega_sale_con_2_si_el_motor_lo_rechaza(self):
        def ingerir():
            time.sleep(1.0)
            Daemon(self.home).ingest_inbox()

        t = threading.Thread(target=ingerir)
        t.start()
        r = motor("entrega", str(self.plan), "--home", str(self.home), "--espera", "20")
        t.join()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("rechazado", r.stdout)
        self.assertIn("ssh-goncloud", r.stdout)


if __name__ == "__main__":
    unittest.main()
