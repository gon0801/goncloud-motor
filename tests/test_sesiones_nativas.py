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
from unittest import mock

from motor.encargos.arranque import Observacion
from motor.encargos.ciclo import (
    _copiar_sesion,
    _copiar_sesion_sin_caer,
    _fallos_de_repo_seguidos,
    ahora_iso,
    marcar_espera,
    recibir,
)
from motor.encargos.copiar import copiar_arbol
from motor.encargos.registro import Registro
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


@unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "necesita permisos POSIX sin root")
class CopiaQueFalla(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(self._limpiar)
        self.origen = self.tmp / "intento"
        self.ilegible = self.origen / ".local/share/opencode/privado"
        self.ilegible.mkdir(parents=True)
        (self.origen / ".local/share/opencode/opencode.db").write_text("v1")
        self.ilegible.chmod(0)

    def _limpiar(self):
        self.ilegible.chmod(0o755)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_un_directorio_ilegible_se_informa_y_no_se_salta_en_silencio(self):
        destino = self.tmp / "almacen"
        with self.assertRaises(shutil.Error) as visto:
            copiar_arbol(self.origen / ".local/share/opencode", destino)
        self.assertIn("privado", str(visto.exception))
        self.assertEqual((destino / "opencode.db").read_text(), "v1")

    def test_el_fallo_de_la_copia_queda_en_el_encargo_y_no_sube_al_barrido(self):
        registro = Registro(self.tmp / "base")
        hora = ahora_iso()
        ident = recibir(registro, "copia", "copia-1", hora)["encargo"]
        enc = registro.fold(ident)
        for _ in range(2):
            _copiar_sesion_sin_caer(
                registro, enc, self.origen, self.tmp / "almacen", "guardar", hora
            )
        (nota,) = self._fallos(registro, enc)
        self.assertIn("privado", nota)
        self.assertEqual((self.tmp / "almacen/.local/share/opencode/opencode.db").read_text(), "v1")

    def test_el_fallo_de_la_copia_no_reescribe_la_causa_ni_el_motivo_del_encargo(self):
        registro, enc, hora = self._encargo()
        antes = registro.fold(enc.id)
        _copiar_sesion_sin_caer(
            registro, enc, self.origen, self.tmp / "almacen", "guardar", hora
        )
        despues = registro.fold(enc.id)
        self.assertEqual((despues.causa, despues.motivo, despues.siguiente),
                         (antes.causa, antes.motivo, antes.siguiente))
        self.assertEqual(len(despues.procedimiento), len(antes.procedimiento) + 1)

    def _encargo(self):
        registro = Registro(self.tmp / "base")
        hora = ahora_iso()
        ident = recibir(registro, "copia", "copia-1", hora)["encargo"]
        return registro, registro.fold(ident), hora

    def _fallos(self, registro, enc):
        return [
            e["data"]["nota"]
            for e in registro.eventos(enc.id)
            if e["kind"] == "procedimiento" and "copia de sesión" in e["data"]["nota"]
        ]

    def test_el_aviso_de_la_copia_no_corta_la_racha_de_fallos_de_repo(self):
        registro, enc, hora = self._encargo()
        causa = "git fetch: no se alcanza el remoto"
        marcar_espera(registro, enc.id, causa, "2026-11-01T10:00:00Z", hora)
        _copiar_sesion_sin_caer(registro, enc, self.origen, self.tmp / "almacen", "restaurar", hora)
        marcar_espera(registro, enc.id, causa, "2026-11-01T10:05:00Z", hora)
        self.assertEqual(len(self._fallos(registro, enc)), 1)
        self.assertEqual(_fallos_de_repo_seguidos(registro, enc.id, causa), 2)

        registro.anotar(
            enc.id, {"kind": "procedimiento", "data": {"nota": "otra cosa"}}, "otra:1"
        )
        self.assertEqual(_fallos_de_repo_seguidos(registro, enc.id, causa), 0)

    def test_si_vaciar_el_almacen_falla_la_marca_sigue_y_no_se_restaura(self):
        registro, enc, hora = self._encargo()
        almacen, runtime = self.tmp / "almacen", self.tmp / "runtime"
        _copiar_sesion_sin_caer(registro, enc, self.origen, almacen, "guardar", hora)
        self.ilegible.chmod(0o755)

        def borra_la_marca_y_falla(ruta):
            (ruta / ".copia-incompleta").unlink()
            raise PermissionError("no se pudo vaciar")

        with mock.patch("motor.encargos.ciclo.vaciar_arbol", side_effect=borra_la_marca_y_falla):
            _copiar_sesion_sin_caer(registro, enc, self.origen, almacen, "guardar", hora)
        self.assertTrue((almacen / ".copia-incompleta").exists())
        _copiar_sesion_sin_caer(registro, enc, almacen, runtime, "restaurar", hora)
        self.assertFalse(runtime.exists() and any(runtime.iterdir()))

    def test_si_no_se_puede_quitar_la_marca_tampoco_sube_al_barrido(self):
        registro, enc, hora = self._encargo()
        with mock.patch("motor.encargos.ciclo._copiar_sesion"), mock.patch.object(
            Path, "unlink", side_effect=PermissionError("sin permiso")
        ):
            _copiar_sesion_sin_caer(
                registro, enc, self.origen, self.tmp / "almacen", "guardar", hora
            )
        (nota,) = self._fallos(registro, enc)
        self.assertIn("PermissionError", nota)

    def test_el_guardado_completo_no_conserva_lo_que_dejo_la_copia_fallida(self):
        registro, enc, hora = self._encargo()
        almacen = self.tmp / "almacen"
        _copiar_sesion_sin_caer(registro, enc, self.origen, almacen, "guardar", hora)
        viejo = almacen / ".local/share/opencode/solo-de-la-copia-fallida"
        viejo.write_text("resto")
        self.ilegible.chmod(0o755)
        _copiar_sesion_sin_caer(registro, enc, self.origen, almacen, "guardar", hora)
        self.assertFalse(viejo.exists())
        self.assertFalse((almacen / ".copia-incompleta").exists())
        self.assertEqual((almacen / ".local/share/opencode/opencode.db").read_text(), "v1")

    def test_un_valueerror_de_la_copia_tampoco_sube_al_barrido(self):
        registro, enc, hora = self._encargo()
        with mock.patch(
            "motor.encargos.ciclo._copiar_sesion", side_effect=ValueError("ruta fuera del origen")
        ):
            _copiar_sesion_sin_caer(
                registro, enc, self.origen, self.tmp / "almacen", "guardar", hora
            )
        (nota,) = self._fallos(registro, enc)
        self.assertIn("ValueError", nota)

    def test_una_copia_a_medias_no_se_restaura_hasta_que_un_guardado_completo_la_repone(self):
        registro, enc, hora = self._encargo()
        almacen, runtime = self.tmp / "almacen", self.tmp / "runtime"
        _copiar_sesion_sin_caer(registro, enc, self.origen, almacen, "guardar", hora)
        self.assertTrue((almacen / ".copia-incompleta").exists())

        _copiar_sesion_sin_caer(registro, enc, almacen, runtime, "restaurar", hora)
        self.assertFalse((runtime / ".local").exists())
        causas = self._fallos(registro, enc)
        self.assertTrue(any("incompleta" in c for c in causas))

        self.ilegible.chmod(0o755)
        _copiar_sesion_sin_caer(registro, enc, self.origen, almacen, "guardar", hora)
        self.assertFalse((almacen / ".copia-incompleta").exists())
        _copiar_sesion_sin_caer(registro, enc, almacen, runtime, "restaurar", hora)
        self.assertEqual((runtime / ".local/share/opencode/opencode.db").read_text(), "v1")

    def test_si_falla_al_restaurar_el_runtime_queda_sin_sesion_parcial(self):
        registro, enc, hora = self._encargo()
        runtime = self.tmp / "runtime"
        _copiar_sesion_sin_caer(registro, enc, self.origen, runtime, "restaurar", hora)
        self.assertEqual(list(runtime.iterdir()), [])
        self.assertEqual(len(self._fallos(registro, enc)), 1)


if __name__ == "__main__":
    unittest.main()
