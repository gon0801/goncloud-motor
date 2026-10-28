from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from motor.encargos.arranque import Observacion
from motor.encargos.ciclo import _copiar_sesion
from motor.encargos.dominio import Encargo, decision_proceso
from motor.encargos.sesion import herramienta_en_curso


class SesionesNativas(unittest.TestCase):
    def test_razonar_sin_herramienta_no_cierra_el_intento(self):
        enc = Encargo(
            id="enc_demo",
            clave="demo",
            huella="demo",
            director="claw",
            texto="crea cantidad.txt",
            adjuntos=(),
            restricciones=(),
            canal="claw",
            estado="ejecutando",
        )
        with tempfile.TemporaryDirectory() as tmp:
            for evento in (
                {"type": "thought", "data": "estoy razonando"},
                {"type": "turn.started"},
            ):
                with self.subTest(evento=evento["type"]):
                    directorio = Path(tmp)
                    observacion = Observacion(directorio)
                    observacion.evento(evento)
                    self.assertEqual(observacion.secuencia, 0)
                    ocupado = herramienta_en_curso(directorio)
                    self.assertTrue(ocupado)
                    self.assertFalse(
                        decision_proceso(enc, "vivo", ocupado, True, True, "ahora")[
                            "matar"
                        ]
                    )
                    self.assertTrue(
                        decision_proceso(
                            replace(enc, estado="deteniendo"),
                            "vivo",
                            ocupado,
                            True,
                            True,
                            "ahora",
                        )["matar"]
                    )
                    observacion.evento({"type": "turn.completed"})
                    self.assertFalse(herramienta_en_curso(directorio))
            self.assertFalse(
                decision_proceso(enc, "vivo", None, True, True, "ahora")["matar"]
            )

    def test_reanudar_conserva_el_contexto_al_cambiar_de_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp)
            origen, almacen, nuevo = (
                raiz / n for n in ("anterior", "almacen", "nuevo")
            )
            rollout = origen / ".codex/sessions/2026/10/28/rollout-demo.jsonl"
            rollout.parent.mkdir(parents=True)
            contexto = json.dumps({"type": "session_meta", "payload": {"id": "hilo"}})
            rollout.write_text(contexto)
            indice = origen / ".codex/state_5.sqlite"
            with closing(sqlite3.connect(indice)) as db, db:
                db.execute(
                    "CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT)"
                )
                db.execute("INSERT INTO threads VALUES (?, ?)", ("hilo", str(rollout)))
            _copiar_sesion(origen, almacen)
            with closing(sqlite3.connect(indice)) as db, db:
                self.assertEqual(
                    db.execute("SELECT rollout_path FROM threads").fetchone()[0],
                    str(rollout),
                )
            shutil.rmtree(origen)
            _copiar_sesion(almacen, nuevo)
            shutil.rmtree(almacen)
            with closing(sqlite3.connect(nuevo / ".codex/state_5.sqlite")) as db, db:
                ruta = db.execute(
                    "SELECT rollout_path FROM threads WHERE id=?", ("hilo",)
                ).fetchone()[0]
            self.assertEqual(Path(ruta).read_text(), contexto)


if __name__ == "__main__":
    unittest.main()
