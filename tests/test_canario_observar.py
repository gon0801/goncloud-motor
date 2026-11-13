"""Lo que el canario observa de un encargo trabajado por el motor.

El encargo es real: el banco de `tests/test_compuerta_e2e.py` lo recibe, lo
trabaja con el agente de ensayo, lo mergea en el git local y lo despliega. La
prueba deja en la base lo que un encargo real puede dejar (un hijo vivo de un
proceso muerto, los archivos de un despliegue, un `git` colgado) y mira qué
dice la tabla.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import ops.canario_encargos as canario
from motor import plataforma
from ops.canario_encargos import TABLA, Esperado, Topes, juzgar, main, observar
from tests.test_compuerta_e2e import (
    SEPARA_TRABAJADOR,
    Banco,
    _DeepSeekAprueba,
    _escenario_tres_fases,
    _trabajador_contenido,
)

TOPES = Topes(minutos_termina=90, minutos=45, turnos_por_rol=2, rondas=1)


def _ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _esperado() -> Esperado:
    return Esperado(filas={nombre: "pasa" for nombre, *_ in TABLA}, topes=TOPES)  # type: ignore[arg-type]


def _limpio(obs) -> str:
    return next(f.resultado for f in juzgar(obs, _esperado()) if f.nombre == "limpio")


def _restos(obs, tipo: str) -> dict:
    return {r.nombre: r.queda for r in obs.restos if r.tipo == tipo}


@unittest.skipIf(os.name == "nt", "el grupo de procesos sin lanzador es de POSIX")
class ObservarUnEncargoDelBanco(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="canario-obs-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        if not SEPARA_TRABAJADOR:
            _trabajador_contenido(self)
        escenario = _escenario_tres_fases(True, self.tmp / "destino")
        escenario["fases"] = escenario["fases"][:1]
        escenario["producto"] = {"uno": escenario["producto"]["uno"]}
        self.banco = Banco(self.tmp, escenario=escenario, gh_cls=_DeepSeekAprueba)
        self.addCleanup(self.banco.reg.close)
        self.banco.correr(lambda: getattr(self.banco.fold(), "estado", "") == "terminado")

    def mirar(self):
        return observar(self.banco.base, self.banco.ident, self.banco.gh, hora=_ahora())

    def clave(self) -> Path:
        (pedido,) = (self.banco.base / "despliegues").glob("*/pedido.json")
        return pedido.parent

    def huerfano(self) -> dict:
        """Un lanzador que ya murió y dejó un hijo vivo en su grupo."""
        lanzador = subprocess.Popen(
            ["sh", "-c", "sleep 60 & read x"], stdin=subprocess.PIPE, start_new_session=True,
        )
        self.addCleanup(lambda: os.killpg(lanzador.pid, signal.SIGKILL))
        identidad = {
            "pid": lanzador.pid,
            "inicio": plataforma.hora_inicio(lanzador.pid),
            "boot_id": plataforma.boot_id(),
            "job": "",
        }
        assert lanzador.stdin is not None
        lanzador.stdin.close()
        lanzador.wait(timeout=10)
        self.assertEqual(plataforma.estado_proc(identidad), "muerto")
        return identidad

    def test_un_hijo_vivo_de_un_trabajador_muerto_no_esta_limpio(self):
        (self.clave() / "proceso.json").write_text(json.dumps(self.huerfano()), encoding="utf-8")
        obs = self.mirar()
        self.assertIs(_restos(obs, "proceso")["algún trabajador de despliegue"], True)
        self.assertEqual(_limpio(obs), "falla")

    def test_un_hijo_vivo_de_un_turno_muerto_no_esta_limpio(self):
        raiz = self.banco.base / "ejecucion" / self.banco.ident
        raiz.mkdir(parents=True, exist_ok=True)
        (raiz / "proc.json").write_text(json.dumps(self.huerfano()), encoding="utf-8")
        obs = self.mirar()
        (turnos,) = [q for n, q in _restos(obs, "proceso").items() if "turnos" in n]
        self.assertIs(turnos, True)

    def test_un_trabajador_que_no_se_puede_mirar_no_cuenta_como_ausente(self):
        (self.clave() / "proceso.json").write_text(
            json.dumps({"pid": "x", "inicio": "y", "boot_id": plataforma.boot_id()}),
            encoding="utf-8",
        )
        obs = self.mirar()
        self.assertIsNone(_restos(obs, "proceso")["algún trabajador de despliegue"])
        self.assertNotEqual(_limpio(obs), "pasa")

    def test_lo_que_queda_junto_al_recibo_es_un_resto(self):
        clave = self.clave()
        obs = self.mirar()
        restos = _restos(obs, "despliegue")
        nombre = next(n for n in restos if n.startswith(f"despliegues/{clave.name[:12]}"))
        self.assertIs(restos[nombre], True)
        self.assertIn("pedido.json", nombre)
        self.assertEqual(_limpio(obs), "falla")

        for hijo in clave.iterdir():
            if hijo.name != "recibo.json":
                hijo.unlink()
        for puntero in (self.banco.base / "despliegues").glob("*.actual"):
            puntero.unlink()
        limpio = self.mirar()
        self.assertFalse(any(_restos(limpio, "despliegue").values()))
        self.assertTrue(limpio.recibos, "el recibo se conserva y se sigue leyendo")

    def test_cada_regla_de_limpieza_del_modelo_se_revisa(self):
        tipos = {r.tipo for r in self.mirar().restos}
        self.assertEqual(
            tipos,
            {"rama", "pr", "espacio", "ejecucion", "runtime", "despliegue", "referencia", "proceso"},
        )

    def test_una_revision_sin_origen_cuenta_como_ronda(self):
        db = sqlite3.connect(self.banco.base / "registro.sqlite")
        for (disparador,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall():
            db.execute(f'DROP TRIGGER "{disparador}"')
        filas = db.execute(
            "SELECT id, data FROM events WHERE encargo = ? AND kind = 'revision'",
            (self.banco.ident,),
        ).fetchall()
        independientes = 0
        for ident, crudo in filas:
            datos = json.loads(crudo)
            if datos.get("origen") == "independiente":
                independientes += 1
                del datos["origen"]
                db.execute("UPDATE events SET data = ? WHERE id = ?", (json.dumps(datos), ident))
        db.commit()
        db.close()
        self.assertGreater(independientes, 0)
        obs = self.mirar()
        self.assertEqual(obs.pliegue.rondas, independientes)  # type: ignore[union-attr]

    def test_main_sale_con_0_si_coincide_y_con_1_si_no(self):
        obs = self.mirar()
        filas = {f.nombre: f.resultado for f in juzgar(obs, _esperado())}
        ruta = self.tmp / "esperado.json"
        argv = [self.banco.ident, "--base", str(self.banco.base), "--esperado", str(ruta)]
        topes = {"minutos_termina": 90, "minutos": 45, "turnos_por_rol": 2, "rondas": 1}

        ruta.write_text(json.dumps({"filas": filas, "topes": topes}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(argv, gh=self.banco.gh), 0)

        otro = "pasa" if filas["limpio"] != "pasa" else "falla"
        ruta.write_text(
            json.dumps({"filas": {**filas, "limpio": otro}, "topes": topes}), encoding="utf-8",
        )
        salida = io.StringIO()
        with contextlib.redirect_stdout(salida):
            self.assertEqual(main(argv, gh=self.banco.gh), 1)
        self.assertIn("- limpio:", salida.getvalue())


@unittest.skipIf(os.name == "nt", "el git falso es un script de sh")
class GitColgado(unittest.TestCase):
    def test_un_git_colgado_deja_la_version_sin_medir(self):
        tmp = Path(tempfile.mkdtemp(prefix="canario-git-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        bin_ = tmp / "bin"
        bin_.mkdir()
        git = bin_ / "git"
        git.write_text("#!/bin/sh\nexec sleep 60\n", encoding="utf-8")
        git.chmod(0o755)
        base = tmp / "base"
        base.mkdir()
        db = sqlite3.connect(base / "registro.sqlite")
        db.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY, ts TEXT, encargo TEXT,"
            " kind TEXT, data TEXT, ukey TEXT UNIQUE)"
        )
        db.commit()
        db.close()
        (base / "supervisor-pasada.json").write_text(
            json.dumps({"sha": "a" * 40, "ruta": str(tmp)}), encoding="utf-8",
        )
        entorno = {"PATH": f"{bin_}{os.pathsep}{os.environ.get('PATH', '')}"}
        with mock.patch.dict(os.environ, entorno), mock.patch.object(canario, "ESPERA_GIT_S", 0.5):
            obs = observar(base, "enc_x", _DeepSeekAprueba(tmp), hora=_ahora())
        self.assertEqual(obs.version.arbol, "")
        fila = next(f for f in juzgar(obs, _esperado()) if f.nombre == "versión")
        self.assertEqual(fila.resultado, "no medido")


if __name__ == "__main__":
    unittest.main()
