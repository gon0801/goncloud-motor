"""La dirección push de Kuma (MOTOR_KUMA_URL) es un secreto del supervisor: ni la sesión de un agente de encargos
ni un comando del comprobador la heredan en su entorno, aunque el supervisor la tenga en el entorno de su proceso o
en el `secretos.env` de su base. Si alguien la exporta al entorno de los agentes, estas pruebas fallan."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import aislamiento
from tests.entorno import HAY_AISLAMIENTO, SIN_AISLAMIENTO

SANDBOX = Path("/usr/bin/sandbox-exec")
# Armada al correr: ningún literal con forma de llave entra al repo.
DIRECCION = "http://127.0.0.1:1/api/push/" + "sentinela" * 3


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


@unittest.skipUnless(HAY_AISLAMIENTO and SANDBOX.is_file(), SIN_AISLAMIENTO)
class ComandoAisladoReal(Base):
    def test_el_comando_que_lanza_el_motor_no_lleva_la_direccion_de_kuma(self):
        with self.supervisor_con_la_direccion():
            for lectura in (False, True):
                with self.subTest(lectura=lectura):
                    comando = aislamiento.aislar(
                        ["python3", "-c", "pass"], self.espacio, self.runtime / f"r{int(lectura)}", lectura=lectura
                    )
                    self.exige_limpio(comando.entorno)


if __name__ == "__main__":
    unittest.main()
