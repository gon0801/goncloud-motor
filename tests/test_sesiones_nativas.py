from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
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
    _incorporar,
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


class SesionConsistente(unittest.TestCase):
    """La sesión guardada no se daña al copiarla y una sesión dañada no traba el encargo."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origen = self.tmp / "intento"
        self.almacen = self.tmp / "almacen"
        self.db = self.origen / ".local/share/opencode/opencode.db"

    def _base_con_wal(self, textos):
        self.db.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE notas (texto TEXT)")
            db.executemany("INSERT INTO notas VALUES (?)", [(t,) for t in textos])

    def _conjunto_solo_en_wal(self, ruta: Path) -> None:
        """Deja en `ruta` la base con la fila solo en el WAL: la tabla queda con
        checkpoint y el INSERT sin checkpoint. Los bytes se capturan con la
        conexión viva y se reconstruyen, porque cerrar la última conexión hace
        checkpoint y disolvería ese estado."""
        plantilla = self.tmp / "plantilla" / ruta.name
        plantilla.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(plantilla)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE notas (t TEXT)")
        con.commit()
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("INSERT INTO notas VALUES ('guardado solo en WAL')")
        con.commit()
        cruda = plantilla.read_bytes()
        wal = plantilla.with_name(plantilla.name + "-wal").read_bytes()
        con.close()
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(cruda)
        ruta.with_name(ruta.name + "-wal").write_bytes(wal)

    def _encargo(self, hora):
        registro = Registro(self.tmp / "base")
        ident = recibir(registro, "copia", "copia-1", hora)["encargo"]
        return registro, registro.fold(ident)

    def test_una_base_en_wal_con_escrituras_sin_checkpoint_queda_consistente(self):
        self._base_con_wal(("v1",))
        _copiar_sesion(self.origen, self.almacen)
        viva = sqlite3.connect(self.db)
        viva.execute("PRAGMA journal_mode=WAL")
        viva.execute("INSERT INTO notas VALUES ('v2')")
        viva.commit()
        _copiar_sesion(self.origen, self.almacen)
        fresco = self.tmp / "fresco"
        _copiar_sesion(self.almacen, fresco)
        copia = fresco / ".local/share/opencode/opencode.db"
        with closing(sqlite3.connect(copia)) as db:
            self.assertEqual(
                db.execute("SELECT texto FROM notas").fetchall(), [("v1",), ("v2",)]
            )
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
        viva.close()
        os.chmod(self.db, 0o600)
        with closing(sqlite3.connect(self.db)) as db, db:
            db.executescript(
                "DROP TABLE notas; CREATE TABLE notas2 (t TEXT);"
                " INSERT INTO notas2 VALUES ('v3');"
            )
        _copiar_sesion(self.origen, self.almacen)
        runtime = self.tmp / "runtime"
        _copiar_sesion(self.almacen, runtime)
        restaurada = runtime / ".local/share/opencode"
        self.assertEqual(
            [p.name for p in restaurada.iterdir() if p.name.startswith("opencode.db")],
            ["opencode.db"],
            "la sesión restaurada arrastra -wal/-shm de otra generación",
        )
        self.assertEqual(
            (restaurada / "opencode.db").stat().st_mode & 0o777,
            self.db.stat().st_mode & 0o777,
            "la copia cambia el permiso de la base",
        )
        with closing(sqlite3.connect(restaurada / "opencode.db")) as db:
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT t FROM notas2").fetchall(), [("v3",)])

    def test_una_sesion_guardada_danada_se_aparta_y_el_turno_arranca_sin_ella(self):
        hora = "2026-11-11T07:25:00Z"
        registro, enc = self._encargo(hora)
        self._base_con_wal(("v1",))
        almacen = registro.base / "runtime" / enc.id / "sesion-responsable"
        _copiar_sesion(self.origen, almacen)
        destino_db = almacen / ".local/share/opencode/opencode.db"
        cruda = destino_db.read_bytes()
        destino_db.write_bytes(cruda[: len(cruda) // 2])
        runtime = self.tmp / "runtime"
        _copiar_sesion_sin_caer(registro, enc, almacen, runtime, "restaurar", hora)
        apartado = almacen.parent / "sesion-responsable.danada-20261111T0725"
        self.assertTrue(apartado.is_dir(), "la sesión dañada no se apartó con su marca")
        self.assertFalse(almacen.exists())
        self.assertFalse(runtime.exists() and any(runtime.iterdir()))
        notas = [
            e["data"]["nota"]
            for e in registro.eventos(enc.id)
            if e["kind"] == "procedimiento"
        ]
        self.assertTrue(
            any("dañada" in n and "se apartó" in n for n in notas), notas
        )

    def _fallo_local(self, registro, enc, hora, numero=1):
        token = f"tok{numero}"
        runtime_intento = registro.base / "runtime" / enc.id / token
        (runtime_intento / ".local/share/opencode").mkdir(parents=True)
        cruda = self.db.read_bytes()
        (runtime_intento / ".local/share/opencode/opencode.db").write_bytes(
            cruda[: len(cruda) // 2]
        )
        directorio = registro.base / "ejecucion" / enc.id / f"intento-{numero}"
        directorio.mkdir(parents=True)
        (directorio / "arranque.json").write_text(
            json.dumps(
                {
                    "agente": "opencode",
                    "modelo": "deepseek/deepseek-flash",
                    "token": token,
                    "sesion_modo": "reanudada",
                }
            )
        )
        (directorio / "outcome.json").write_text(
            json.dumps(
                {
                    "fallo_runtime": (
                        "Error: Unexpected error\n\ndatabase disk image is malformed"
                    )
                }
            )
        )
        return directorio

    def test_un_fallo_local_de_sesion_no_marca_al_proveedor_y_reintenta_sin_ella(self):
        hora = "2026-11-11T07:25:00Z"
        registro, enc = self._encargo(hora)
        self._base_con_wal(("v1",))
        almacen = registro.base / "runtime" / enc.id / "sesion-responsable"
        _copiar_sesion(self.origen, almacen)
        destino_db = almacen / ".local/share/opencode/opencode.db"
        cruda = destino_db.read_bytes()
        destino_db.write_bytes(cruda[: len(cruda) // 2])
        directorio = self._fallo_local(registro, enc, hora)
        enc2 = _incorporar(registro, enc, self.tmp / "espacio", hora, directorio)
        self.assertEqual(
            [e for e in registro.eventos(enc.id) if e["kind"] == "proveedor_fallo"],
            [],
            "un fallo local de la sesión se anotó como falla del proveedor",
        )
        self.assertNotEqual(enc2.estado, "esperando_reintento")
        self.assertIn(enc2.estado, ("recibido", "ejecutando"))
        apartado = almacen.parent / "sesion-responsable.danada-20261111T0725"
        self.assertTrue(apartado.is_dir())
        self.assertFalse(almacen.exists())
        notas = [
            e["data"]["nota"]
            for e in registro.eventos(enc.id)
            if e["kind"] == "procedimiento"
        ]
        self.assertTrue(
            any("se apartó" in n and "sin sesión" in n for n in notas), notas
        )

    def test_sin_sesion_el_mismo_fallo_dice_la_causa_real_y_espera(self):  # no-roja-en-base: el segundo fallo llega en un turno nuevo, como en producción; la base ya pasaba repitiendo el intento incorporado
        hora = "2026-11-11T07:25:00Z"
        registro, enc = self._encargo(hora)
        self._base_con_wal(("v1",))
        almacen = registro.base / "runtime" / enc.id / "sesion-responsable"
        _copiar_sesion(self.origen, almacen)
        destino_db = almacen / ".local/share/opencode/opencode.db"
        cruda = destino_db.read_bytes()
        destino_db.write_bytes(cruda[: len(cruda) // 2])
        apartado = almacen.parent / "sesion-responsable.danada-20261111T0725"

        enc1 = _incorporar(
            registro, enc, self.tmp / "espacio", hora, self._fallo_local(registro, enc, hora, 1)
        )
        self.assertTrue(apartado.is_dir())
        self.assertIn(enc1.estado, ("recibido", "ejecutando"))
        registro.anotar(enc.id, {"kind": "reserva", "data": {"rol": "responsable"}}, "t-reserva-2")
        registro.anotar(enc.id, {"kind": "reserva_cerrada", "data": {}}, "t-cerrada-2")

        enc2 = _incorporar(
            registro, registro.fold(enc.id), self.tmp / "espacio", hora,
            self._fallo_local(registro, registro.fold(enc.id), hora, 2),
        )
        self.assertEqual(
            [e for e in registro.eventos(enc.id) if e["kind"] == "proveedor_fallo"],
            [],
            "un fallo local de la sesión se anotó como falla del proveedor",
        )
        self.assertEqual(len(list(almacen.parent.glob("sesion-responsable.danada-*"))), 1)
        locales = [
            e for e in registro.eventos(enc.id) if e["kind"] == "falla_sesion_local"
        ]
        self.assertTrue(locales, "la falla local no quedó dicha como tal")
        self.assertIn("database disk image is malformed", locales[0]["data"]["causa"])
        self.assertEqual(enc2.estado, "esperando_reintento")
        esperas = [
            e for e in registro.eventos(enc.id) if e["kind"] == "espera"
        ]
        self.assertTrue(esperas)
        self.assertIn(
            "falla local de la sesión, no del proveedor", esperas[0]["data"]["motivo"]
        )
        self.assertIn("malformed", esperas[0]["data"]["motivo"])

    def test_si_un_lateral_del_destino_no_se_puede_apartar_no_se_publica_la_base(self):
        import motor.encargos.copiar

        self._base_con_wal(("nuevo",))
        almacen = self.tmp / "almacen"
        destino_db = almacen / ".local/share/opencode/opencode.db"
        self._conjunto_solo_en_wal(destino_db)
        real = motor.encargos.copiar.os.replace

        def no_puede_apartar(src, dst, *args, **kwargs):
            if Path(src).name == "opencode.db-wal" and str(dst).endswith(".viejo"):
                raise PermissionError("el lateral no se deja apartar")
            return real(src, dst, *args, **kwargs)

        with mock.patch.object(
            motor.encargos.copiar.os, "replace", side_effect=no_puede_apartar
        ):
            with self.assertRaises(
                shutil.Error,
                msg="un lateral que no se deja apartar publicó la base nueva igual",
            ):
                _copiar_sesion(self.origen, almacen)
        self.assertEqual(
            sorted(
                p.name
                for p in destino_db.parent.iterdir()
                if p.name.startswith("opencode.db")
            ),
            ["opencode.db", "opencode.db-wal"],
            "el intento fallido dejó los laterales desparejados",
        )
        with closing(sqlite3.connect(destino_db)) as db:
            self.assertEqual(
                db.execute("SELECT t FROM notas").fetchall(),
                [("guardado solo en WAL",)],
                "la base nueva se publicó encima del conjunto anterior",
            )

    def test_si_publicar_la_base_falla_los_laterales_apartados_vuelven(self):  # no-roja-en-base: el defecto lo introduce este PR con la publicación por reemplazo; en main la copia archivo por archivo nunca toca el conjunto anterior
        import motor.encargos.copiar

        self._base_con_wal(("nuevo",))
        almacen = self.tmp / "almacen"
        destino_db = almacen / ".local/share/opencode/opencode.db"
        self._conjunto_solo_en_wal(destino_db)
        real = motor.encargos.copiar.os.replace

        def no_publica(src, dst, *args, **kwargs):
            if Path(dst).name == "opencode.db" and not str(dst).endswith(".viejo"):
                raise PermissionError("la publicación no se deja hacer")
            return real(src, dst, *args, **kwargs)

        with mock.patch.object(
            motor.encargos.copiar.os, "replace", side_effect=no_publica
        ):
            with self.assertRaises(shutil.Error):
                _copiar_sesion(self.origen, almacen)
        self.assertEqual(
            sorted(
                p.name
                for p in destino_db.parent.iterdir()
                if p.name.startswith("opencode.db")
            ),
            ["opencode.db", "opencode.db-wal"],
            "la publicación fallida destruyó los laterales del respaldo anterior",
        )
        self.assertFalse(list(destino_db.parent.glob(".*.nuevo")), "quedó un temporal huérfano")
        self.assertFalse(
            list(destino_db.parent.glob(".*.copia")), "quedó el directorio privado huérfano"
        )
        with closing(sqlite3.connect(destino_db)) as db:
            self.assertEqual(
                db.execute("SELECT t FROM notas").fetchall(),
                [("guardado solo en WAL",)],
                "la publicación fallida perdió los datos comprometidos del respaldo anterior",
            )

    @unittest.skipIf(os.name == "nt", "los permisos POSIX no aplican en Windows")
    def test_el_temporal_de_la_copia_nace_en_un_directorio_privado(self):
        self._base_con_wal(("v1",))
        almacen = self.tmp / "almacen"
        anterior = os.umask(0o022)
        eventos = []

        def vigilante(evento, args):
            if evento not in ("os.chmod", "os.mkdir"):
                return
            try:
                ruta, modo = Path(args[0]), int(args[1])
            except (TypeError, ValueError):
                return
            if ".opencode.db." not in str(ruta):
                return
            try:
                padre = ruta.parent.stat().st_mode & 0o077
            except OSError:
                padre = None
            eventos.append((evento, ruta, modo, padre))

        sys.addaudithook(vigilante)
        try:
            _copiar_sesion(self.origen, almacen)
        finally:
            os.umask(anterior)
        for _evento, _ruta, modo, _padre in (e for e in eventos if e[0] == "os.mkdir"):
            self.assertEqual(
                modo & 0o077,
                0,
                "el directorio privado de la copia nació transitable",
            )
        chmods = [
            e for e in eventos if e[0] == "os.chmod" and not e[1].name.endswith(".copia")
        ]
        self.assertTrue(chmods, "no se vio el temporal de la copia")
        for _evento, ruta, _modo, padre in chmods:
            self.assertEqual(
                padre,
                0,
                "el temporal de la sesión nació en un directorio transitable "
                f"({ruta}): otros usuarios locales pudieron leerlo",
            )

    def test_el_escaneo_del_almacen_no_se_cae_por_un_permiso(self):
        hora = "2026-11-11T07:25:00Z"
        registro, enc = self._encargo(hora)
        self._base_con_wal(("v1",))
        almacen = registro.base / "runtime" / enc.id / "sesion-responsable"
        _copiar_sesion(self.origen, almacen)
        destino_db = almacen / ".local/share/opencode/opencode.db"
        cruda = destino_db.read_bytes()
        destino_db.write_bytes(b"cabecera rota!" + cruda[14:])
        runtime = self.tmp / "runtime"
        real_stat = Path.stat

        conteo: dict[str, int] = {}

        def stat_trava(ruta, *args, **kwargs):
            clave = str(ruta)
            if Path(ruta).name == "opencode.db":
                conteo[clave] = conteo.get(clave, 0) + 1
                if conteo[clave] >= 2:
                    raise PermissionError("repro del escaneo")
            return real_stat(ruta, *args, **kwargs)

        with mock.patch.object(Path, "stat", autospec=True, side_effect=stat_trava):
            _copiar_sesion_sin_caer(registro, enc, almacen, runtime, "restaurar", hora)
        apartado = almacen.parent / "sesion-responsable.danada-20261111T0725"
        self.assertTrue(
            apartado.is_dir(),
            "el escaneo lanzó PermissionError revisando una base y no apartó ni anotó",
        )

        # la primera mitad apartó el almacén: la ruta quedó libre, así que se
        # vuelve a dejar una sesión dañada en ella (con otra hora para su
        # marca) y el recorrido corre de verdad, ejerciendo el guard del rglob.
        _copiar_sesion(self.origen, almacen)
        destino_db2 = almacen / ".local/share/opencode/opencode.db"
        cruda2 = destino_db2.read_bytes()
        destino_db2.write_bytes(b"cabecera rota!" + cruda2[14:])
        runtime2 = self.tmp / "runtime2"
        with mock.patch.object(Path, "rglob", side_effect=PermissionError("repro")):
            _copiar_sesion_sin_caer(
                registro, enc, almacen, runtime2, "restaurar", "2026-11-11T08:25:00Z"
            )
        apartado2 = almacen.parent / "sesion-responsable.danada-20261111T0825"
        self.assertTrue(
            apartado2.is_dir(),
            "el escaneo lanzó PermissionError recorriendo el almacén y no apartó ni anotó",
        )
        self.assertFalse(runtime2.exists() and any(runtime2.iterdir()))
        notas = [
            e["data"]["nota"]
            for e in registro.eventos(enc.id)
            if e["kind"] == "procedimiento"
        ]
        self.assertTrue(
            any("PermissionError: repro" in n for n in notas),
            "la segunda mitad no ejercitó el recorrido: no quedó la causa del rglob",
        )

    def test_un_candado_pasajero_al_revisar_no_aparta_la_sesion_sana(self):  # no-roja-en-base: en main no hay revisión al restaurar ni apartado, y la copia archivo por archivo no se entera del candado
        hora = "2026-11-11T07:25:00Z"
        registro, enc = self._encargo(hora)
        self._base_con_wal(("v1",))
        almacen = registro.base / "runtime" / enc.id / "sesion-responsable"
        _copiar_sesion(self.origen, almacen)
        destino_db = almacen / ".local/share/opencode/opencode.db"
        with closing(sqlite3.connect(destino_db)) as db, db:
            db.execute("PRAGMA journal_mode=DELETE")
        candado = sqlite3.connect(destino_db, timeout=60)
        candado.execute("BEGIN EXCLUSIVE")
        try:
            runtime = self.tmp / "runtime"
            _copiar_sesion_sin_caer(registro, enc, almacen, runtime, "restaurar", hora)
        finally:
            candado.close()
        self.assertFalse(
            any(almacen.parent.glob("sesion-responsable.danada-*")),
            "un candado pasajero al revisar apartó el almacén sano",
        )
        self.assertTrue(almacen.is_dir(), "la sesión sana se perdió por el candado")

    def test_el_turno_refleja_la_sesion_perdida_antes_de_lanzar(self):
        from motor.encargos.ciclo import _lanzar_uno_en_turno

        hora = "2026-11-11T07:25:00Z"
        registro, enc = self._encargo(hora)
        registro.anotar(
            enc.id,
            {
                "kind": "intento_cerrado",
                "data": {"sesion_runtime": "ses_guardada", "hora": hora},
            },
            f"intento-cerrado:{enc.id}:0",
        )
        enc = registro.fold(enc.id)
        self.assertEqual(enc.sesion_responsable, "ses_guardada")
        self._base_con_wal(("v1",))
        almacen = registro.base / "runtime" / enc.id / "sesion-responsable"
        _copiar_sesion(self.origen, almacen)
        destino_db = almacen / ".local/share/opencode/opencode.db"
        cruda = destino_db.read_bytes()
        destino_db.write_bytes(cruda[: len(cruda) // 2])
        espacio = self.tmp / "espacio"
        espacio.mkdir()
        lanzadas = []

        def lanzador(enc, prompt, espacio, sesion):
            lanzadas.append(enc)
            return [], {"agente": "opencode", "modelo": "deepseek/deepseek-flash"}

        with mock.patch(
            "motor.encargos.ciclo._asegurar_repo", return_value=None
        ), mock.patch(
            "motor.flota.runtime.exigir_poteto", return_value=(Path("poteto"), "x")
        ):
            _lanzar_uno_en_turno(
                registro, registro.fold(enc.id), None, espacio, hora, lanzador
            )
        self.assertTrue(lanzadas, "el turno no llegó a lanzar")
        self.assertEqual(
            lanzadas[0].sesion_responsable,
            "",
            "el turno lanzó con el id de la sesión recién apartada",
        )
        self.assertFalse(lanzadas[0].viva_responsable)

    def test_un_candado_en_la_copia_no_borra_la_sesion_sana_ni_reanuda(self):
        from motor.encargos.agentes import _lanzador, _sesion_modo
        from motor.encargos.ciclo import _incorporar, _lanzar_uno_en_turno

        hora = "2026-11-11T07:25:00Z"
        registro, enc = self._encargo(hora)
        registro.anotar(
            enc.id,
            {
                "kind": "intento_abierto",
                "data": {
                    "rol": "responsable",
                    "agente": "opencode",
                    "modelo": "deepseek/deepseek-flash",
                    "poteto_digest": "x",
                    "id": "i1",
                    "pid": 1,
                    "inicio": hora,
                    "sesion_modo": "reanudada",
                },
            },
            f"intento-abierto:{enc.id}:0",
        )
        registro.anotar(
            enc.id,
            {
                "kind": "intento_cerrado",
                "data": {"sesion_runtime": "ses_guardada", "hora": hora},
            },
            f"intento-cerrado:{enc.id}:0",
        )
        enc = registro.fold(enc.id)
        self.assertEqual(enc.sesion_responsable, "ses_guardada")
        self.assertTrue(enc.viva_responsable)

        almacen = registro.base / "runtime" / enc.id / "sesion-responsable"
        destino_db = almacen / ".local/share/opencode/opencode.db"
        destino_db.parent.mkdir(parents=True)
        with closing(sqlite3.connect(destino_db)) as db, db:
            db.execute("CREATE TABLE notas (t TEXT)")
            db.execute("INSERT INTO notas VALUES ('sana')")
        candado = sqlite3.connect(destino_db, timeout=60)
        candado.execute("BEGIN EXCLUSIVE")
        lanzadas = []
        espacio = self.tmp / "espacio"
        espacio.mkdir()

        def lanzador(enc, prompt, espacio, sesion):
            modo = _sesion_modo(enc, "opencode", "responsable")
            argv = _lanzador("opencode", "deepseek/deepseek-flash", modo)(
                enc, prompt, espacio, sesion
            )
            lanzadas.append((enc, modo, argv))
            return [], {
                "agente": "opencode",
                "modelo": "deepseek/deepseek-flash",
                "sesion_modo": modo,
            }

        try:
            with mock.patch(
                "motor.encargos.ciclo._asegurar_repo", return_value=None
            ), mock.patch(
                "motor.flota.runtime.exigir_poteto", return_value=(Path("poteto"), "x")
            ):
                _lanzar_uno_en_turno(
                    registro, registro.fold(enc.id), None, espacio, hora, lanzador
                )
        finally:
            candado.close()
        self.assertTrue(lanzadas, "el turno no llegó a lanzar")
        enc_lanzado, modo, argv = lanzadas[0]
        with self.subTest("el turno no reanuda una sesión que no entró al runtime"):
            self.assertNotEqual(
                modo,
                "reanudada",
                "la copia falló por el candado y el turno construye --session igual",
            )
            self.assertNotIn("--session", argv)
        self.assertTrue(
            almacen.is_dir(), "el episodio del candado perdió el almacén sano"
        )

        # el intento que sigue se incorpora con lo que el modo diga: con
        # reanudada el agente no encuentra la sesión que no está en el runtime;
        # con reconstruida arranca una sesión nueva y termina bien.
        directorio = registro.base / "ejecucion" / enc.id / "intento-episodio"
        directorio.mkdir(parents=True)
        (directorio / "arranque.json").write_text(
            json.dumps(
                {
                    "agente": "opencode",
                    "modelo": "deepseek/deepseek-flash",
                    "token": "tok",
                    "sesion_modo": modo,
                }
            )
        )
        if modo == "reanudada":
            (directorio / "outcome.json").write_text(
                json.dumps({"fallo_runtime": "session not found"})
            )
        else:
            runtime_fresco = registro.base / "runtime" / enc.id / "tok"
            (runtime_fresco / ".local/share/opencode").mkdir(parents=True)
            with closing(
                sqlite3.connect(runtime_fresco / ".local/share/opencode/opencode.db")
            ) as db, db:
                db.execute("CREATE TABLE notas (t TEXT)")
                db.execute("INSERT INTO notas VALUES ('nueva')")
            (directorio / "outcome.json").write_text(
                json.dumps({"sesion_runtime": "ses_nueva"})
            )
        _incorporar(registro, registro.fold(enc.id), espacio, hora, directorio)
        with self.subTest("la incorporación no borra el almacén sano"):
            self.assertEqual(
                list(almacen.parent.glob("sesion-responsable.danada-*")),
                [],
                "el episodio apartó el almacén",
            )
            self.assertTrue(
                almacen.is_dir(), "la incorporación borró el almacén sano"
            )
        self.assertEqual(
            [e for e in registro.eventos(enc.id) if e["kind"] == "proveedor_fallo"],
            [],
        )
        if modo != "reanudada":
            with closing(sqlite3.connect(destino_db)) as db:
                self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(
                    db.execute("SELECT t FROM notas").fetchall(), [("nueva",)]
                )

    def test_el_revisor_arranca_sin_sesion_cuando_su_almacen_se_aparta(self):
        try:
            from motor.encargos.agentes import _lanzador, _sesion_modo
            from motor.encargos.ciclo import _reflejar_sesion
        except ImportError as exc:
            raise AssertionError(
                "el apartado del almacén del revisor no refleja la pérdida de sesión"
            ) from exc

        hora = "2026-11-11T07:25:00Z"
        registro, enc = self._encargo(hora)
        registro.anotar(
            enc.id,
            {
                "kind": "intento_abierto",
                "data": {
                    "rol": "revisor",
                    "agente": "codex",
                    "modelo": "openai/gpt-6-astra",
                    "poteto_digest": "x",
                    "id": "i1",
                    "pid": 1,
                    "inicio": hora,
                    "sesion_modo": "reanudada",
                },
            },
            f"intento-abierto:{enc.id}:0",
        )
        registro.anotar(
            enc.id,
            {
                "kind": "intento_cerrado",
                "data": {"sesion_runtime": "ses_revisor", "hora": hora},
            },
            f"intento-cerrado:{enc.id}:0",
        )
        enc = replace(
            enc,
            rol="revisor",
            modelos=(("revisor", "codex", "openai/gpt-6-astra"),),
        )
        enc = registro.fold(enc.id)
        self.assertEqual(enc.sesion_revisor, "ses_revisor")
        self.assertTrue(enc.viva_revisor)
        self._base_con_wal(("v1",))
        almacen = registro.base / "runtime" / enc.id / "sesion-revisor"
        _copiar_sesion(self.origen, almacen)
        destino_db = almacen / ".local/share/opencode/opencode.db"
        cruda = destino_db.read_bytes()
        destino_db.write_bytes(cruda[: len(cruda) // 2])
        runtime = self.tmp / "runtime"
        _copiar_sesion_sin_caer(registro, enc, almacen, runtime, "restaurar", hora)
        apartado = almacen.parent / "sesion-revisor.danada-20261111T0725"
        self.assertTrue(apartado.is_dir(), "el almacén del revisor no se apartó")
        enc2 = _reflejar_sesion(registro, enc)
        self.assertEqual(enc2.sesion_revisor, "")
        self.assertFalse(enc2.viva_revisor)
        modo = _sesion_modo(enc2, "codex", "revisor")
        self.assertNotEqual(
            modo, "reanudada", "el revisor reanuda la sesión recién apartada"
        )
        argv = _lanzador("codex", "openai/gpt-6-astra", modo)(
            enc2, Path("p"), Path("e"), "ses"
        )
        self.assertNotIn("ses_revisor", argv)
        self.assertNotIn("resume", argv)

    def test_una_base_fuente_bloqueada_acaba_acotada_y_no_traba_la_copia(self):
        home = self.tmp / "home"
        home.mkdir()
        raiz = Path(__file__).resolve().parents[1]
        script = f"""
import sqlite3, sys, time
from pathlib import Path
sys.path.insert(0, {str(raiz)!r})
from motor.encargos.ciclo import _copiar_sesion
candado = sqlite3.connect({str(self.db)!r}, timeout=60)
candado.execute("PRAGMA journal_mode=DELETE")
candado.commit()
candado.execute("BEGIN EXCLUSIVE")
inicio = time.monotonic()
try:
    _copiar_sesion(Path({str(self.origen)!r}), Path({str(self.tmp / 'destino')!r}))
    print("TERMINO ok")
except BaseException as exc:
    print("TERMINO", type(exc).__name__, str(exc)[:2000])
print("duracion", round(time.monotonic() - inicio, 1))
candado.close()
"""
        self._base_con_wal(("v1",))
        corrida = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "HOME": str(home)},
        )
        self.assertEqual(corrida.returncode, 0, corrida.stdout + corrida.stderr)
        self.assertIn(
            "TERMINO",
            corrida.stdout,
            "la copia quedó trabada sin límite por el candado de la fuente",
        )
        self.assertIn(
            "database is locked",
            corrida.stdout,
            "el bloqueo persistente no salió como error acotado de la copia",
        )

    def test_la_sesion_apartada_en_la_restauracion_no_se_reanuda_en_el_turno(self):
        try:
            from motor.encargos.agentes import _lanzador, _sesion_modo
            from motor.encargos.ciclo import _reflejar_sesion
        except ImportError as exc:
            raise AssertionError(
                "el apartado en restaurar no refleja la pérdida de sesión en el turno"
            ) from exc

        hora = "2026-11-11T07:25:00Z"
        registro, enc = self._encargo(hora)
        enc = replace(
            enc,
            rol="responsable",
            viva_responsable=True,
            sesion_responsable="ses_guardada",
            modelos=(("responsable", "opencode", "deepseek/deepseek-flash"),),
        )
        self._base_con_wal(("v1",))
        almacen = registro.base / "runtime" / enc.id / "sesion-responsable"
        _copiar_sesion(self.origen, almacen)
        destino_db = almacen / ".local/share/opencode/opencode.db"
        cruda = destino_db.read_bytes()
        destino_db.write_bytes(cruda[: len(cruda) // 2])
        runtime = self.tmp / "runtime"
        _copiar_sesion_sin_caer(registro, enc, almacen, runtime, "restaurar", hora)
        apartado = almacen.parent / "sesion-responsable.danada-20261111T0725"
        self.assertTrue(apartado.is_dir(), "la sesión dañada no se apartó")
        perdidas = [e for e in registro.eventos(enc.id) if e["kind"] == "sesion_perdida"]
        self.assertTrue(
            perdidas,
            "el apartado no perdió la sesión: el turno construye --session con el id apartado",
        )
        enc2 = _reflejar_sesion(registro, enc)
        self.assertEqual(enc2.sesion_responsable, "")
        self.assertFalse(enc2.viva_responsable)
        modo = _sesion_modo(enc2, "opencode", "responsable")
        self.assertNotEqual(
            modo, "reanudada", "el turno siguiente reanuda la sesión apartada"
        )
        argv = _lanzador("opencode", "deepseek/deepseek-flash", modo)(
            enc2, Path("p"), Path("e"), "ses"
        )
        self.assertNotIn("ses_guardada", argv)
        self.assertNotIn("--session", argv)

    def test_una_base_con_la_cabecera_destruida_tambien_se_aparta(self):
        from motor.encargos.ciclo import _causa_sesion_danada
        from motor.encargos.copiar import es_sqlite

        hora = "2026-11-11T07:25:00Z"
        registro, enc = self._encargo(hora)
        self._base_con_wal(("v1",))
        almacen = registro.base / "runtime" / enc.id / "sesion-responsable"
        _copiar_sesion(self.origen, almacen)
        destino_db = almacen / ".local/share/opencode/opencode.db"
        cruda = destino_db.read_bytes()
        destino_db.write_bytes(b"cabecera rota!" + cruda[14:])
        self.assertFalse(es_sqlite(destino_db))
        causa = _causa_sesion_danada(almacen)
        self.assertIn("opencode.db", causa, "la cabecera destruida no se validó")
        self.assertIn("not a database", causa)
        runtime = self.tmp / "runtime"
        _copiar_sesion_sin_caer(registro, enc, almacen, runtime, "restaurar", hora)
        apartado = almacen.parent / "sesion-responsable.danada-20261111T0725"
        self.assertTrue(
            apartado.is_dir(), "la sesión con la cabecera destruida no se apartó"
        )
        self.assertFalse(almacen.exists())
        self.assertFalse((runtime / ".local/share/opencode/opencode.db").exists())

    def test_un_archivo_desconocido_sin_cabecera_no_se_cuarentena(self):
        from motor.encargos.ciclo import _causa_sesion_danada

        hora = "2026-11-11T07:25:00Z"
        registro, enc = self._encargo(hora)
        self._base_con_wal(("v1",))
        almacen = registro.base / "runtime" / enc.id / "sesion-responsable"
        _copiar_sesion(self.origen, almacen)
        (almacen / ".local/share/opencode/otro.db").write_bytes(b"datos arbitrarios")
        runtime = self.tmp / "runtime"
        _copiar_sesion_sin_caer(registro, enc, almacen, runtime, "restaurar", hora)
        self.assertEqual(_causa_sesion_danada(almacen), "")
        self.assertTrue(almacen.is_dir(), "un dato que nunca fue base se cuarentenó")
        self.assertTrue((runtime / ".local/share/opencode/otro.db").exists())


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
        self.db = self.origen / ".local/share/opencode/opencode.db"
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute("CREATE TABLE marcas (t TEXT)")
        self.ilegible.chmod(0)

    def _limpiar(self):
        self.ilegible.chmod(0o755)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _la_base_se_copio(self, ruta: Path) -> None:
        with closing(sqlite3.connect(ruta)) as db:
            self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertIsNotNone(
                db.execute("SELECT 1 FROM sqlite_master WHERE name='marcas'").fetchone(),
                "la copia no trae la base",
            )

    def test_un_directorio_ilegible_se_informa_y_no_se_salta_en_silencio(self):  # no-roja-en-base: la prueba ya pasaba en la base; solo se ajusta su artilugio (base SQLite real en vez de un texto) y la asercion de contenido al comportamiento nuevo de la copia de bases de este PR
        destino = self.tmp / "almacen"
        with self.assertRaises(shutil.Error) as visto:
            copiar_arbol(self.origen / ".local/share/opencode", destino)
        self.assertIn("privado", str(visto.exception))
        self._la_base_se_copio(destino / "opencode.db")

    def test_el_fallo_de_la_copia_queda_en_el_encargo_y_no_sube_al_barrido(self):  # no-roja-en-base: la prueba ya pasaba en la base; solo se ajusta su artilugio (base SQLite real en vez de un texto) y la asercion de contenido al comportamiento nuevo de la copia de bases de este PR
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
        self._la_base_se_copio(
            self.tmp / "almacen/.local/share/opencode/opencode.db"
        )

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

    def test_el_aviso_de_la_copia_no_corta_la_racha_de_fallos_de_repo(self):  # no-roja-en-base: la prueba migra la firma del registro al fold; el comportamiento probado ya estaba en la base
        registro, enc, hora = self._encargo()
        causa = "git fetch: no se alcanza el remoto"
        marcar_espera(registro, enc.id, causa, "2026-11-01T10:00:00Z", hora)
        _copiar_sesion_sin_caer(registro, enc, self.origen, self.tmp / "almacen", "restaurar", hora)
        marcar_espera(registro, enc.id, causa, "2026-11-01T10:05:00Z", hora)
        self.assertEqual(len(self._fallos(registro, enc)), 1)
        self.assertEqual(_fallos_de_repo_seguidos(registro.fold(enc.id), causa), 2)

        registro.anotar(
            enc.id, {"kind": "procedimiento", "data": {"nota": "otra cosa"}}, "otra:1"
        )
        self.assertEqual(_fallos_de_repo_seguidos(registro.fold(enc.id), causa), 0)

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

    def test_el_guardado_completo_no_conserva_lo_que_dejo_la_copia_fallida(self):  # no-roja-en-base: la prueba ya pasaba en la base; solo se ajusta su artilugio (base SQLite real en vez de un texto) y la asercion de contenido al comportamiento nuevo de la copia de bases de este PR
        registro, enc, hora = self._encargo()
        almacen = self.tmp / "almacen"
        _copiar_sesion_sin_caer(registro, enc, self.origen, almacen, "guardar", hora)
        viejo = almacen / ".local/share/opencode/solo-de-la-copia-fallida"
        viejo.write_text("resto")
        self.ilegible.chmod(0o755)
        _copiar_sesion_sin_caer(registro, enc, self.origen, almacen, "guardar", hora)
        self.assertFalse(viejo.exists())
        self.assertFalse((almacen / ".copia-incompleta").exists())
        self._la_base_se_copio(almacen / ".local/share/opencode/opencode.db")

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

    def test_una_copia_a_medias_no_se_restaura_hasta_que_un_guardado_completo_la_repone(self):  # no-roja-en-base: la prueba ya pasaba en la base; solo se ajusta su artilugio (base SQLite real en vez de un texto) y la asercion de contenido al comportamiento nuevo de la copia de bases de este PR
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
        self._la_base_se_copio(runtime / ".local/share/opencode/opencode.db")

    def test_si_falla_al_restaurar_el_runtime_queda_sin_sesion_parcial(self):
        registro, enc, hora = self._encargo()
        runtime = self.tmp / "runtime"
        _copiar_sesion_sin_caer(registro, enc, self.origen, runtime, "restaurar", hora)
        self.assertEqual(list(runtime.iterdir()), [])
        self.assertEqual(len(self._fallos(registro, enc)), 1)


if __name__ == "__main__":
    unittest.main()
