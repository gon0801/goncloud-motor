"""Temporal propio del comprobador y de las sesiones de agente.

Cada corrida recibe un `TMPDIR` vacío dentro de su runtime y se borra al
terminar. El aislamiento ya no recorta el entorno, pero cada intento sigue
sin mezclar sus temporales con los de otra corrida.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from motor.encargos.aislamiento import aislar, limpiar_temporal, temporal_de

RAIZ = Path(__file__).resolve().parents[1]


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


@unittest.skipIf(os.name == "nt", "el entorno de la rama POSIX")
class ElEntornoApuntaAlTemporalDelRuntime(unittest.TestCase):
    def test_tmpdir_temp_y_tmp_apuntan_al_temporal_de_la_corrida(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            espacio = Path(tmp) / "espacio" / "enc_x"
            espacio.mkdir(parents=True)
            runtime = Path(tmp) / "runtime"
            comando = aislar([sys.executable], espacio, runtime, lecturas_extra=())
            temporal = (runtime / "tmp").resolve()
            self.assertEqual(Path(comando.entorno["TMPDIR"]).resolve(), temporal)
            self.assertEqual(Path(comando.entorno["TEMP"]).resolve(), temporal)
            self.assertEqual(Path(comando.entorno["TMP"]).resolve(), temporal)

    def test_la_siguiente_corrida_vacia_el_temporal(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            espacio = Path(tmp) / "espacio" / "enc_x"
            espacio.mkdir(parents=True)
            runtime = Path(tmp) / "runtime"
            aislar([sys.executable], espacio, runtime, lecturas_extra=())
            (runtime / "tmp" / "resto.txt").write_text("x", encoding="utf-8")
            aislar([sys.executable], espacio, runtime, lecturas_extra=())
            self.assertFalse((runtime / "tmp" / "resto.txt").exists())


if __name__ == "__main__":
    unittest.main()
