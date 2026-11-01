"""El comprobador respeta el tope de cada comprobacion declarada.

Antes del cambio, `_ejecutar_declaradas` usaba `communicate(timeout=30)` fijo y
un comando mas largo quedaba como fallo del instrumento sin salida.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import comprobacion
from motor.encargos.aislamiento import ComandoAislado
from tests.test_flujo_revisiones import Recorrido


def _sin_jaula(cmd, espacio, runtime, **kw):
    return ComandoAislado(list(cmd), dict(os.environ), "prueba")


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.r = Recorrido(self.tmp.name, "tope-" + self.id().rsplit(".", 1)[-1], "Crea dato.txt con 42")
        self.addCleanup(self.r.reg.close)

    def _correr(self, comandos: list[dict]) -> dict:
        with mock.patch.object(comprobacion, "aislar", _sin_jaula):
            return comprobacion._ejecutar_declaradas(
                comandos, self.r.espacio, self.r.d, self.r.d / "comprobador-t"
            )


class ElTopeDeclaradoSeRespeta(_Base):
    def test_un_comando_de_40_s_con_tope_de_60_pasa(self):
        argv = [sys.executable, "-c", "import time; time.sleep(40)"]
        datos = self._correr([{"objetivo": "tarda mas que el tope viejo", "argv": argv, "tope": 60}])
        ejecucion = datos["ejecuciones"][0]
        self.assertEqual(ejecucion.get("rc"), 0, ejecucion)
        self.assertNotIn("error_instrumento", ejecucion)
        self.assertEqual(ejecucion.get("tope"), 60)


class ElTopePorOmisionSeRespeta(_Base):
    def test_un_comando_de_31_s_sin_tope_declarado_pasa(self):
        argv = [sys.executable, "-c", "import time; time.sleep(31)"]
        datos = self._correr([{"objetivo": "no declara tope", "argv": argv}])
        ejecucion = datos["ejecuciones"][0]
        self.assertEqual(ejecucion.get("rc"), 0, ejecucion)
        self.assertNotIn("error_instrumento", ejecucion)
        self.assertEqual(ejecucion.get("tope"), comprobacion.TOPE_POR_OMISION_S)


class ElExcesoInformaLaSalida(_Base):
    def test_el_fallo_nombra_comando_tope_salida_parcial_y_alternativa(self):
        argv = [
            sys.executable,
            "-u",
            "-c",
            (
                "import sys, time; "
                "print('parcial-stdout-antes-del-tope'); "
                "sys.stderr.write('parcial-stderr-antes-del-tope\\n'); "
                "sys.stderr.flush(); "
                "time.sleep(5)"
            ),
        ]
        datos = self._correr([{"objetivo": "se pasa del tope", "argv": argv, "tope": 1}])
        ejecucion = datos["ejecuciones"][0]
        mensaje = ejecucion.get("error_instrumento", "")
        self.assertIn(" ".join(argv), mensaje)
        self.assertIn("tope de 1 segundo", mensaje)
        self.assertIn("parcial-stdout-antes-del-tope", mensaje)
        self.assertIn("parcial-stderr-antes-del-tope", mensaje)
        self.assertIn(str(comprobacion.TOPE_MAXIMO_S), mensaje)
        self.assertIn("partir", mensaje)
        self.assertIn("parcial-stdout-antes-del-tope", ejecucion.get("stdout", ""))


class ElMaximoNoSePuedePasar(unittest.TestCase):
    def test_un_tope_mayor_al_maximo_se_rechaza_con_motivo(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            interno = Path(tmp) / ".interno"
            interno.mkdir()
            (interno / "comprobaciones.json").write_text(
                json.dumps(
                    {
                        "comprobaciones": [
                            {
                                "objetivo": "demasiado larga",
                                "argv": ["python3", "-c", "pass"],
                                "tope": comprobacion.TOPE_MAXIMO_S + 1,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as ctx:
                comprobacion.leer_declaradas(interno)
        motivo = str(ctx.exception)
        self.assertIn(str(comprobacion.TOPE_MAXIMO_S + 1), motivo)
        self.assertIn(str(comprobacion.TOPE_MAXIMO_S), motivo)
        self.assertIn("maximo", motivo)


if __name__ == "__main__":
    unittest.main()
