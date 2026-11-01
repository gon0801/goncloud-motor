"""Temporal propio del comprobador y de las sesiones dentro de la jaula.

En macOS, BSD `mktemp` sin plantilla ignora TMPDIR y usa /var/folders, que la
jaula niega. El temporal de la corrida vive dentro del runtime y se crea vacío;
la jaula no concede otra escritura de temporales.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import aislamiento, comprobacion
from motor.encargos.aislamiento import (
    aislar,
    lecturas_del_interprete,
    limpiar_temporal,
    temporal_de,
)
from tests.entorno import HAY_AISLAMIENTO
from tests.test_flujo_revisiones import Recorrido

RAIZ = Path(__file__).resolve().parents[1]

if HAY_AISLAMIENTO:
    from tests.test_comprobador_interprete import EXIGIDA, JAULA_REAL
else:
    EXIGIDA, JAULA_REAL = False, False


class ElTemporalSeCreaVacioYSeBorra(unittest.TestCase):
    def test_cada_corrida_empieza_sin_restos_de_la_anterior(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            runtime = Path(tmp) / "runtime"
            temporal = temporal_de(runtime)
            (temporal / "viejo.txt").write_text("resto", encoding="utf-8")
            temporal_de(runtime)
            self.assertEqual(list(temporal.iterdir()), [])
            limpiar_temporal(runtime)
            self.assertFalse(temporal.exists())


@unittest.skipUnless(
    HAY_AISLAMIENTO and sys.platform == "darwin", "el caso de mktemp es de macOS"
)
class ElEntornoApuntaAlTemporalDelRuntime(unittest.TestCase):
    def test_tmpdir_y_el_atajo_de_mktemp(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            espacio = Path(tmp) / "espacio" / "enc_x"
            espacio.mkdir(parents=True)
            runtime = Path(tmp) / "runtime"
            comando = aislar([sys.executable], espacio, runtime, lecturas_extra=())
            temporal = (runtime / "tmp").resolve()
            self.assertEqual(Path(comando.entorno["TMPDIR"]).resolve(), temporal)
            self.assertEqual(Path(comando.entorno["TEMP"]).resolve(), temporal)
            self.assertEqual(Path(comando.entorno["TMP"]).resolve(), temporal)
            atajo = shutil.which("mktemp", path=comando.entorno["PATH"])
            self.assertEqual(Path(atajo).resolve(), (runtime / "bin" / "mktemp").resolve())
            hecho = subprocess.run(
                [atajo, "-d"],
                env={**os.environ, "PATH": comando.entorno["PATH"], "TMPDIR": str(temporal)},
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(hecho.returncode, 0, hecho.stderr)
            self.assertTrue(Path(hecho.stdout.strip()).is_relative_to(temporal))

    def test_la_siguiente_corrida_vacia_el_temporal(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            espacio = Path(tmp) / "espacio" / "enc_x"
            espacio.mkdir(parents=True)
            runtime = Path(tmp) / "runtime"
            aislar([sys.executable], espacio, runtime, lecturas_extra=())
            (runtime / "tmp" / "resto.txt").write_text("x", encoding="utf-8")
            aislar([sys.executable], espacio, runtime, lecturas_extra=())
            self.assertFalse((runtime / "tmp" / "resto.txt").exists())

    def test_el_atajo_no_depende_de_la_opcion_p(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            real = base / "mktemp-real"
            real.write_text(
                "#!/bin/sh\n"
                'for a in "$@"; do [ "$a" = -p ] && exit 64; done\n'
                'exec /usr/bin/mktemp "$@"\n',
                encoding="utf-8",
            )
            real.chmod(0o755)
            runtime = base / "runtime"
            with mock.patch.object(aislamiento.shutil, "which", return_value=str(real)):
                bin_dir = aislamiento._atajo_mktemp(runtime)
            atajo = bin_dir / "mktemp"
            hecho = subprocess.run(
                [str(atajo), "-d"],
                env={**os.environ, "TMPDIR": str(temporal_de(runtime))},
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(hecho.returncode, 0, hecho.stderr)
            self.assertTrue(Path(hecho.stdout.strip()).is_relative_to(runtime.resolve()))


class ConLaJaulaReal(unittest.TestCase):
    def setUp(self):
        if JAULA_REAL:
            return
        causa = "hace falta sandbox-exec aplicable (macOS fuera de otra jaula)"
        if EXIGIDA:
            self.fail("MOTOR_EXIGE_SEATBELT=1 y la jaula real no se puede aplicar: " + causa)
        self.skipTest(causa)


@unittest.skipUnless(sys.platform == "darwin", "el caso de mktemp es de macOS")
class UnaComprobacionHaceMktempDentroDeLaJaula(ConLaJaulaReal):
    def test_mktemp_dentro_de_la_jaula_y_el_temporal_se_borra(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "temporal-1", "Crea dato.txt con 42")
            self.addCleanup(r.reg.close)
            argv = [
                "/bin/bash",
                "-c",
                (
                    "d=$(mktemp -d) && test -d \"$d\" && "
                    "if : >/tmp/motor-temp-$$ 2>/dev/null; then exit 3; fi && "
                    "printf 'tmp=%s\\n' \"$d\""
                ),
            ]
            datos = comprobacion._ejecutar_declaradas(
                [{"objetivo": "usa mktemp -d dentro de la jaula", "argv": argv}],
                r.espacio,
                r.d,
                r.d / "comprobador-t",
            )
            ejecucion = datos["ejecuciones"][0]
            self.assertEqual(ejecucion.get("rc"), 0, ejecucion)
            self.assertNotIn("error_instrumento", ejecucion)
            self.assertIn("tmp=", ejecucion.get("stdout", ""))
            runtime = r.d / "comprobador-t" / "runtime-0"
            self.assertFalse((runtime / "tmp").exists(), "el temporal debe borrarse al terminar")


class UnaComprobacionRealDeOpsCorreDentroDeLaJaula(ConLaJaulaReal):
    def test_bash_ops_tests_test_lib_sh_pasa(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            r = Recorrido(tmp, "temporal-ops-1", "Crea dato.txt con 42")
            self.addCleanup(r.reg.close)
            shutil.copytree(RAIZ / "ops", r.espacio / "ops")
            datos = comprobacion._ejecutar_declaradas(
                [{"objetivo": "una prueba real de ops", "argv": ["bash", "ops/tests/test-lib.sh"]}],
                r.espacio,
                r.d,
                r.d / "comprobador-t",
            )
            ejecucion = datos["ejecuciones"][0]
            self.assertEqual(ejecucion.get("rc"), 0, ejecucion)
            self.assertNotIn("error_instrumento", ejecucion)
            self.assertIn("todo bien", ejecucion.get("stdout", ""))


if __name__ == "__main__":
    unittest.main()
