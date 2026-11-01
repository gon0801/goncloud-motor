"""La dirección push de Kuma (MOTOR_KUMA_URL) es un secreto del supervisor: ni la sesión de un agente de encargos
ni un comando del comprobador la heredan en su entorno, aunque el supervisor la tenga en el entorno de su proceso o
en el `secretos.env` de su base. Si alguien la exporta al entorno de los agentes, estas pruebas fallan."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import aislamiento, comprobacion
from motor.encargos.aislamiento import lecturas_del_interprete
from tests.test_comprobador_interprete import EXIGIDA, FLUJO, JAULA_REAL
from tests.test_flujo_revisiones import Recorrido

# Armada al correr: ningún literal con forma de llave entra al repo.
DIRECCION = "http://127.0.0.1:1/api/push/" + "sentinela" * 3
VER_ENTORNO = "import json, os; print(json.dumps(dict(os.environ)))"


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        raiz = Path(self._tmp.name)
        self.base = raiz / "base"
        self.base.mkdir()
        secretos = self.base / "secretos.env"
        secretos.write_text(f"MOTOR_KUMA_URL={DIRECCION}\n")
        secretos.chmod(0o600)
        self.espacio = raiz / "espacio" / "enc_x"
        self.espacio.mkdir(parents=True)
        self.runtime = raiz / "runtime"

    def exige_limpio(self, entorno: dict) -> None:
        self.assertNotIn("MOTOR_KUMA_URL", {k.upper() for k in entorno}, "la dirección de Kuma está en el entorno")
        con_la_direccion = [k for k, v in entorno.items() if "api/push/" in str(v) or DIRECCION in str(v)]
        self.assertEqual(con_la_direccion, [], "algún valor del entorno lleva la dirección de Kuma")

    def supervisor_con_la_direccion(self):
        """El proceso del supervisor tiene la dirección en su entorno (y en el secretos.env de su base)."""
        return mock.patch.dict(os.environ, {"MOTOR_KUMA_URL": DIRECCION, "motor_kuma_url": DIRECCION})


class EntornoDeLosAgentes(Base):
    def test_la_sesion_de_un_agente_no_hereda_la_direccion_de_kuma(self):
        with self.supervisor_con_la_direccion():
            entorno = aislamiento._entorno(self.runtime, True, ["agente-x"], agente="agente-x")
        self.exige_limpio(entorno)
        self.assertIn("HOME", entorno, "es el entorno de la jaula, no el del supervisor")

    def test_un_comando_del_comprobador_no_hereda_la_direccion_de_kuma(self):
        with self.supervisor_con_la_direccion():
            entorno = aislamiento._entorno(self.runtime, False, ["python3", "-c", "pass"])
        self.exige_limpio(entorno)
        self.assertEqual(entorno["HOME"], str(self.runtime))

    def test_la_prueba_detecta_que_alguien_exporte_la_direccion_a_los_agentes(self):
        with self.supervisor_con_la_direccion():
            entorno = aislamiento._entorno(self.runtime, False, ["python3", "-c", "pass"])
        for roto in (
            {**entorno, "MOTOR_KUMA_URL": DIRECCION},
            {**entorno, "motor_kuma_url": DIRECCION},
            {**entorno, "OTRA": DIRECCION},
        ):
            with self.subTest(roto=[k for k in roto if k not in entorno][0]):
                with self.assertRaises(AssertionError):
                    self.exige_limpio(roto)


class LaPruebaCorreEnCi(unittest.TestCase):
    """Una prueba que en CI siempre se salta no es evidencia: el job de macOS tiene que correr este archivo."""

    def test_el_job_de_macos_corre_este_archivo_junto_al_del_comprobador(self):
        texto = FLUJO.read_text(encoding="utf-8")
        lineas = [l for l in texto.splitlines() if "tests.test_kuma_fuera_del_entorno_de_agentes" in l]
        self.assertEqual(len(lineas), 1, lineas)
        self.assertIn("unittest", lineas[0])
        self.assertIn("tests.test_comprobador_interprete", lineas[0])
        job = texto.split("  jaula-macos:")[1].split("\n  bateria:")[0]
        self.assertIn(lineas[0], job.splitlines())
        self.assertIn('MOTOR_EXIGE_SEATBELT: "1"', job)

    def test_la_parte_sin_jaula_corre_en_la_bateria_normal(self):
        # EntornoDeLosAgentes no se salta en ningún host y la batería de ubuntu descubre todo tests/test_*.py.
        texto = FLUJO.read_text(encoding="utf-8")
        bateria = texto.split("\n  bateria:")[1]
        self.assertIn("python -m unittest discover -s tests -t . -v", bateria)
        self.assertTrue(Path(__file__).name.startswith("test_"))
        for nombre in (
            "test_la_sesion_de_un_agente_no_hereda_la_direccion_de_kuma",
            "test_un_comando_del_comprobador_no_hereda_la_direccion_de_kuma",
        ):
            self.assertTrue(callable(getattr(EntornoDeLosAgentes, nombre, None)), nombre)
        self.assertFalse(getattr(EntornoDeLosAgentes, "__unittest_skip__", False))


class ConLaJaulaRealDeMacOS(Base):
    def setUp(self):
        super().setUp()
        if JAULA_REAL:
            return
        causa = "hace falta sandbox-exec aplicable (macOS fuera de otra jaula)"
        if EXIGIDA:
            self.fail("MOTOR_EXIGE_SEATBELT=1 y la jaula real no se puede aplicar: " + causa)
        self.skipTest(causa)

    def test_la_sesion_de_un_agente_lanzada_en_la_jaula_real_no_ve_la_direccion_de_kuma(self):
        with self.supervisor_con_la_direccion():
            for lectura in (False, True):
                with self.subTest(lectura=lectura):
                    comando = aislamiento.aislar(
                        [sys.executable, "-c", VER_ENTORNO],
                        self.espacio,
                        self.runtime / f"r{int(lectura)}",
                        lectura=lectura,
                        lecturas_extra=lecturas_del_interprete(),
                    )
                    hecho = subprocess.run(
                        comando.argv,
                        cwd=self.espacio,
                        env=comando.entorno,
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    self.assertEqual(hecho.returncode, 0, hecho.stderr)
                    self.exige_limpio(json.loads(hecho.stdout))

    def test_un_comando_del_comprobador_en_la_jaula_real_no_ve_la_direccion_de_kuma(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "kuma-1", "Crea dato.txt con 42")
            self.addCleanup(r.reg.close)
            comandos = [{"objetivo": "entorno", "argv": [sys.executable, "-c", VER_ENTORNO]}]
            with self.supervisor_con_la_direccion():
                datos = comprobacion._ejecutar_declaradas(comandos, r.espacio, r.d, r.d / "comprobador-t")
        ejecucion = datos["ejecuciones"][0]
        self.assertEqual(ejecucion.get("rc"), 0, ejecucion)
        self.exige_limpio(json.loads(ejecucion["stdout"]))


if __name__ == "__main__":
    unittest.main()
