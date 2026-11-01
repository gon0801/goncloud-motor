from __future__ import annotations

import json
import os
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


class CopiaConArchivosDeSoloLectura(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(self._limpiar)
        self.origen = self.tmp / "intento"
        self.almacen = self.tmp / "sesion-responsable"
        self.snapshot = self.origen / ".local/share/opencode/snapshot/abc/objects/ab"

    def _limpiar(self):
        for ruta in [self.tmp, *self.tmp.rglob("*")]:
            if ruta.is_dir() and not ruta.is_symlink():
                ruta.chmod(0o755)
        for ruta in self.tmp.rglob("*"):
            if ruta.is_file():
                ruta.chmod(0o644)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _objeto(self, nombre, contenido, modo=0o444):
        self.snapshot.mkdir(parents=True, exist_ok=True)
        ruta = self.snapshot / nombre
        if ruta.exists():
            ruta.chmod(0o644)
        ruta.write_text(contenido)
        ruta.chmod(modo)
        return ruta

    def test_copiar_dos_veces_con_objetos_0444_en_el_destino(self):
        objeto = self._objeto("cd34", "contenido-git")
        db = self.origen / ".local/share/opencode/opencode.db"
        db.write_text("v1")
        _copiar_sesion(self.origen, self.almacen)
        copiado = self.almacen / ".local/share/opencode/snapshot/abc/objects/ab/cd34"
        self.assertEqual(copiado.stat().st_mode & 0o222, 0)
        inodo = copiado.stat().st_ino

        _copiar_sesion(self.origen, self.almacen)

        self.assertEqual(copiado.read_text(), "contenido-git")
        self.assertEqual(copiado.stat().st_ino, inodo)
        self.assertEqual(objeto.read_text(), "contenido-git")

    def test_un_archivo_que_cambio_se_reemplaza_aunque_el_destino_sea_0444(self):
        db = self.origen / ".local/share/opencode/opencode.db"
        db.parent.mkdir(parents=True)
        db.write_text("v1")
        db.chmod(0o444)
        _copiar_sesion(self.origen, self.almacen)
        copiado = self.almacen / ".local/share/opencode/opencode.db"
        self.assertEqual(copiado.stat().st_mode & 0o222, 0)

        db.chmod(0o644)
        db.write_text("v2-mas-largo")
        db.chmod(0o444)
        nuevo = self._objeto("ef56", "otro-objeto")
        _copiar_sesion(self.origen, self.almacen)

        self.assertEqual(copiado.read_text(), "v2-mas-largo")
        self.assertEqual(
            (copiado.parent / "snapshot/abc/objects/ab/ef56").read_text(), nuevo.read_text()
        )

    def test_mismo_tamano_y_otro_contenido_tambien_se_reemplaza(self):
        db = self.origen / ".local/share/opencode/opencode.db"
        db.parent.mkdir(parents=True)
        db.write_text("aaaa")
        db.chmod(0o444)
        _copiar_sesion(self.origen, self.almacen)
        marca = db.stat().st_mtime_ns
        db.chmod(0o644)
        db.write_text("bbbb")
        os.utime(db, ns=(marca, marca))
        db.chmod(0o444)
        copiado = self.almacen / ".local/share/opencode/opencode.db"
        self.assertEqual(copiado.stat().st_mtime_ns, db.stat().st_mtime_ns)
        _copiar_sesion(self.origen, self.almacen)
        self.assertEqual(
            (self.almacen / ".local/share/opencode/opencode.db").read_text(), "bbbb"
        )

    def test_las_credenciales_no_se_copian(self):
        auth = self.origen / ".local/share/opencode/auth.json"
        auth.parent.mkdir(parents=True)
        auth.write_text("secreto")
        (auth.parent / "otro.json").write_text("{}")
        _copiar_sesion(self.origen, self.almacen)
        self.assertFalse((self.almacen / ".local/share/opencode/auth.json").exists())
        self.assertTrue((self.almacen / ".local/share/opencode/otro.json").exists())


if __name__ == "__main__":
    unittest.main()
