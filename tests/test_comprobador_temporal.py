"""Temporal propio del comprobador y de las sesiones de agente.

Cada corrida recibe un `TMPDIR` vacío dentro de su runtime y se borra al
terminar. El aislamiento ya no recorta el entorno, pero cada intento sigue
sin mezclar sus temporales con los de otra corrida.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

from motor.encargos import arranque
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


@unittest.skipIf(os.name == "nt", "la limpieza del intento es de la rama POSIX")
class ElTemporalDelIntentoSeLimpiaSinDependerDelHome(unittest.TestCase):
    """El cierre del wrapper ya no puede apoyarse en el HOME falseado: con el
    entorno del usuario el HOME es el real, y el temporal de cada intento
    (<runtime>/tmp) tiene que seguir borrándose al terminar."""

    def test_el_arranque_de_lanzamiento_directo_limpia_su_temporal(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            runtime = Path(tmp) / "runtime"
            temporal = temporal_de(runtime)
            (temporal / "resto.txt").write_text("x", encoding="utf-8")
            arranque._limpiar_temporal_de(
                {"env": {"TMPDIR": str(temporal), "CLAUDE_CODE_TMPDIR": str(temporal)}}
            )
            self.assertFalse((runtime / "tmp").exists())

    def test_un_tmpdir_ajeno_al_runtime_no_se_limita(self):
        arranque._limpiar_temporal_de({"env": {"TMPDIR": "/tmp"}})
        self.assertTrue(Path("/tmp").exists())


@unittest.skipIf(os.name == "nt", "el wrapper del intento es de la rama POSIX")
class ElCierreDelWrapperBorraElTemporal(unittest.TestCase):
    """La prueba del propietario para F1: `arranque.ejecutar` corre de verdad
    (el encargo no existe, así que el wrapper sale por la puerta temprana) y el
    temporal del intento desaparece aunque el HOME sea el real del usuario."""

    def test_arranque_ejecutar_deja_el_temporal_del_intento_borrado(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            raiz = Path(tmp).resolve()
            runtime = raiz / "runtime"
            temporal = temporal_de(runtime)
            (temporal / "resto.txt").write_text("x", encoding="utf-8")
            spec = {
                "base": str(raiz / "base"),
                "encargo": "enc_inexistente",
                "token": "tok",
                "host": socket.gethostname(),
                "aislamiento": "directo",
                "cwd": str(raiz),
                "hora": "2026-11-02T00:00:00Z",
                "sesion": "",
                "rol": "responsable",
                "agente": "python3",
                "env": {"TMPDIR": str(temporal), "CLAUDE_CODE_TMPDIR": str(temporal)},
                "cmd": ["true"],
            }
            intento = raiz / "intento"
            intento.mkdir()
            (intento / "arranque.json").write_text(
                json.dumps(spec), encoding="utf-8"
            )
            rc = arranque.ejecutar(intento / "arranque.json")
            self.assertEqual(rc, 0)
            self.assertFalse((runtime / "tmp").exists())


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
