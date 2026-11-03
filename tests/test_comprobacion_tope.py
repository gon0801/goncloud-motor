"""El comprobador respeta el tope de cada comprobacion declarada.

Antes del cambio, `_ejecutar_declaradas` usaba `communicate(timeout=30)` fijo y
un comando mas largo quedaba como fallo del instrumento sin salida.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import comprobacion
from motor.encargos.aislamiento import ComandoAislado
from tests.test_flujo_revisiones import Recorrido


def _sin_jaula(cmd, espacio, runtime, **kw):
    return ComandoAislado(list(cmd), dict(os.environ), "prueba")


class _Corte(Exception):
    pass


def _ejecutar_script(script: str, timeout):
    """Corre el script con un sleep que no espera. Si la suma pasa el tope, corta."""
    dormido = 0.0
    salida, error = io.StringIO(), io.StringIO()

    def sleep(segundos):
        nonlocal dormido
        dormido += float(segundos)
        if timeout is not None and dormido > float(timeout):
            raise _Corte

    falso = types.ModuleType("time")
    falso.sleep = sleep
    falso.time = time.time
    guardados = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = salida, error
    try:
        with mock.patch.dict(sys.modules, {"time": falso}):
            try:
                exec(compile(script, "<comprobacion>", "exec"), {"__name__": "__main__"})
            except _Corte:
                return True, salida.getvalue(), error.getvalue()
    finally:
        sys.stdout, sys.stderr = guardados
    return False, salida.getvalue(), error.getvalue()


class _Proc:
    """El comprobador sigue pasando el tope a communicate. El sleep no es real.

    Un tope de 30 s frente a un comando de 40 s sigue siendo timeout: el falso
    corta cuando la suma de los sleep del script pasa el timeout que recibió.
    """

    def __init__(self, argv):
        self.argv = list(argv)
        self.pid = 2_000_000_001
        self.returncode = None
        self._corte = None
        self._listo = None

    def _script(self) -> str:
        if "-c" in self.argv:
            return self.argv[self.argv.index("-c") + 1]
        return ""

    def communicate(self, timeout=None):
        if self._listo is not None:
            return self._listo
        if self._corte is not None:
            self.returncode = -9
            self._listo = self._corte
            return self._corte
        corto, salida, error = _ejecutar_script(self._script(), timeout)
        if corto:
            self._corte = (salida, error)
            raise subprocess.TimeoutExpired(self.argv, timeout)
        self.returncode = 0
        self._listo = (salida, error)
        return self._listo

    def kill(self):
        self.returncode = -9


_POPEN_REAL = subprocess.Popen


def _es_el_comando_declarado(argv) -> bool:
    """Solo el `python -c` de la comprobacion. `git -c` sigue siendo git."""
    if isinstance(argv, (str, bytes)) or not isinstance(argv, (list, tuple)):
        return False
    args = list(argv)
    if "-c" not in args:
        return False
    return Path(str(args[0])).name.lower().startswith("python")


def _popen_falso(argv, **kwargs):
    if _es_el_comando_declarado(argv):
        return _Proc(list(argv))
    return _POPEN_REAL(argv, **kwargs)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.r = Recorrido(self.tmp.name, "tope-" + self.id().rsplit(".", 1)[-1], "Crea dato.txt con 42")
        self.addCleanup(self.r.reg.close)

    def _correr(self, comandos: list[dict]) -> dict:
        with (
            mock.patch.object(comprobacion, "aislar", _sin_jaula),
            mock.patch.object(comprobacion.subprocess, "Popen", _popen_falso),
        ):
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
