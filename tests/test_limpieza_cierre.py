"""La limpieza de un encargo cerrado borra también lo de solo lectura.

En la PC, el snapshot de opencode dejó archivos con el atributo de solo lectura
y `shutil.rmtree` falló con WinError 5 en cada barrido. Estas pruebas reproducen
ese permiso en un temporal y comprueban que un fallo real se anota una sola vez.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from motor.encargos import ciclo, politica
from motor.encargos.ciclo import ahora_iso, barrer, ordenar, recibir
from motor.encargos.entrega.limpieza import fallos_legados
from motor.encargos.registro import Registro


@contextlib.contextmanager
def unlink_de_windows():
    """En POSIX un archivo 0444 se puede borrar; en Windows no. Este parche
    aplica la regla de Windows al `os.unlink` que usa `shutil.rmtree`."""
    real = os.unlink

    def unlink(ruta, *args, **kwargs):
        dir_fd = kwargs.get("dir_fd")
        try:
            modo = os.stat(ruta, dir_fd=dir_fd, follow_symlinks=False).st_mode
        except OSError:
            modo = None
        if modo is not None and not modo & stat.S_IWRITE:
            raise PermissionError(13, "Permission denied", os.fspath(ruta))
        return real(ruta, *args, **kwargs)

    with mock.patch.object(os, "unlink", side_effect=unlink):
        yield


@mock.patch.object(politica, "ENTREGA_V4", False)
class LimpiezaAlCerrar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.reg = Registro(Path(self.tmp) / "base")
        self.addCleanup(self.reg.close)

    def _terminar(self, encargo: str) -> None:
        self.reg.anotar(
            encargo,
            {
                "kind": "pendiente_entrega",
                "data": {"texto": "bloqueo abierto", "hora": ahora_iso()},
            },
            f"pendiente-{encargo}",
        )
        enc = ciclo._entregar_con_pendientes(
            self.reg, self.reg.fold(encargo), None, ahora_iso()
        )
        self.assertEqual(enc.estado, "terminado")

    def test_un_runtime_con_archivos_de_solo_lectura_se_borra_completo(self):
        encargo = recibir(self.reg, "x", "solo-lectura", ahora_iso())["encargo"]
        runtime = self.reg.base / "runtime" / encargo / "intento-1"
        ejecucion = self.reg.base / "ejecucion" / encargo / "intento-1"
        snapshot = runtime / ".local" / "share" / "opencode" / "snapshot" / "objeto"
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text("snapshot", encoding="utf-8")
        os.chmod(snapshot, 0o444)
        ejecucion.mkdir(parents=True)
        herramientas = ejecucion / "herramientas.json"
        herramientas.write_text("{}", encoding="utf-8")
        os.chmod(herramientas, 0o444)

        with unlink_de_windows():
            self._terminar(encargo)

        self.assertFalse(
            (self.reg.base / "runtime" / encargo).exists(),
            "quedó el runtime de solo lectura",
        )
        self.assertFalse(ejecucion.exists(), "quedó el intento de ejecución")
        self.assertFalse(
            any(
                evento["kind"] == "instrumento_fallo"
                for evento in self.reg.eventos(encargo)
            )
        )

    def _cancelar_con_archivo_abierto(self) -> str:
        encargo = recibir(self.reg, "x", "sin-borrar", ahora_iso())["encargo"]
        runtime = self.reg.base / "runtime" / encargo / "intento-1"
        ejecucion = self.reg.base / "ejecucion" / encargo / "intento-1"
        runtime.mkdir(parents=True)
        ejecucion.mkdir(parents=True)
        (runtime / "abierto.db").write_text("abierto", encoding="utf-8")
        (ejecucion / "herramientas.json").write_text("{}", encoding="utf-8")
        ordenar(self.reg, encargo, "cancelar", "cancela-1", ahora_iso())
        self.reg.anotar(
            encargo,
            {"kind": "arbol", "data": {"observacion": "ausente"}},
            f"cancela-{encargo}",
        )
        self.assertEqual(self.reg.fold(encargo).estado, "cancelado")
        return encargo

    def _notas_de_limpieza(self, encargo: str) -> list[dict]:
        return [
            evento
            for evento in self.reg.eventos(encargo)
            if evento["kind"] == "procedimiento"
            and "limpieza(" in evento["data"].get("nota", "")
        ]

    def test_un_archivo_imposible_de_borrar_se_anota_una_sola_vez(self):
        encargo = self._cancelar_con_archivo_abierto()
        error = PermissionError(13, "Permission denied", "abierto.db")

        with mock.patch.object(ciclo, "borrar_arbol", side_effect=error) as borrar:
            barrer(self.reg, ahora_iso(), time.time(), None)
            self.assertEqual(borrar.call_count, 2)
            self.assertEqual(len(self._notas_de_limpieza(encargo)), 1)
            self.assertFalse(
                any(
                    evento["kind"] == "instrumento_fallo"
                    for evento in self.reg.eventos(encargo)
                )
            )

            # Un fallo transitorio se reintenta una sola vez; la segunda nota
            # queda registrada con su propio intento (no se descarta en
            # silencio por repetir la ukey), y después el barrido deja de
            # intentarlo sin agregar más notas.
            barrer(self.reg, ahora_iso(), time.time(), None)
            self.assertEqual(borrar.call_count, 4, "no se reintentó una vez")
            self.assertEqual(len(self._notas_de_limpieza(encargo)), 2)
            antes = self.reg.eventos(encargo)
            for _ in range(3):
                barrer(self.reg, ahora_iso(), time.time(), None)
            self.assertEqual(borrar.call_count, 4, "la limpieza se reintentó en bucle")
            self.assertEqual(self.reg.eventos(encargo), antes)

    def test_el_fallo_al_enumerar_los_intentos_tambien_se_anota(self):
        encargo = self._cancelar_con_archivo_abierto()
        # `ejecucion/<id>` como archivo: iterdir() falla con NotADirectoryError.
        ejecucion = self.reg.base / "ejecucion" / encargo
        shutil.rmtree(ejecucion)
        ejecucion.write_text("no es carpeta", encoding="utf-8")

        barrer(self.reg, ahora_iso(), time.time(), None)
        notas = self._notas_de_limpieza(encargo)
        self.assertEqual(len(notas), 1)
        self.assertIn("NotADirectoryError", notas[0]["data"]["nota"])

    def test_el_reintento_que_si_borra_quita_la_marca_del_encargo(self):
        encargo = self._cancelar_con_archivo_abierto()
        error = PermissionError(13, "Permission denied", "abierto.db")
        efectos = [error, error, None, None]

        def borrar_falso(*a):
            if efectos:
                efecto = efectos.pop(0)
                if isinstance(efecto, BaseException):
                    raise efecto
            return None

        with mock.patch.object(
            ciclo, "borrar_arbol", side_effect=borrar_falso
        ) as borrar:
            barrer(self.reg, ahora_iso(), time.time(), None)
            self.assertEqual(len(self._notas_de_limpieza(encargo)), 1)
            barrer(self.reg, ahora_iso(), time.time(), None)
            self.assertEqual(borrar.call_count, 4, "el segundo barrido no borró")
            # El reintento logró limpiar: sin nota nueva y sin marca que
            # siga intentando en los barridos siguientes.
            self.assertEqual(len(self._notas_de_limpieza(encargo)), 1)
            self.assertEqual(fallos_legados(self.reg, encargo)[0], 0)
            # El barrido siguiente no agrega nada: sin fallo, sin nota.
            antes = self.reg.eventos(encargo)
            barrer(self.reg, ahora_iso(), time.time(), None)
            self.assertEqual(self.reg.eventos(encargo), antes)

    def test_un_arranque_nuevo_recupera_del_registro_el_intento_pendiente(self):
        encargo = self._cancelar_con_archivo_abierto()
        self.assertFalse(hasattr(ciclo, "_LIMPIEZA_FALLIDA"))
        error = PermissionError(13, "Permission denied", "abierto.db")

        with mock.patch.object(ciclo, "borrar_arbol", side_effect=error) as borrar:
            barrer(self.reg, ahora_iso(), time.time(), None)
            self.assertEqual(borrar.call_count, 2)
            self.reg.close()
            nueva = Registro(self.reg.base)
            self.addCleanup(nueva.close)
            barrer(nueva, ahora_iso(), time.time(), None)
            self.assertEqual(borrar.call_count, 4, "no se reintentó tras el arranque")
            barrer(nueva, ahora_iso(), time.time(), None)
            self.assertEqual(borrar.call_count, 4, "se perdió el tope tras reiniciar")
            self.assertEqual(len([
                e for e in nueva.eventos(encargo)
                if e["kind"] == "procedimiento"
                and "limpieza(" in e["data"].get("nota", "")
            ]), 2)

    def test_fallo_restauracion_y_otro_fallo_conservan_el_ciclo(self):  # no-roja-en-base: fija la rama legada de la ukey con ciclo, existente antes de 4.3
        encargo = self._cancelar_con_archivo_abierto()
        error = PermissionError(13, "Permission denied", "abierto.db")
        with mock.patch.object(ciclo, "borrar_arbol", side_effect=error):
            barrer(self.reg, ahora_iso(), time.time(), None)
        self.assertEqual(fallos_legados(self.reg, encargo)[0], 1)

        barrer(self.reg, ahora_iso(), time.time(), None)
        self.assertEqual(fallos_legados(self.reg, encargo)[0], 0)
        restauracion = self.reg.db.execute(
            "SELECT id FROM events WHERE encargo = ? "
            "AND ukey LIKE 'limpieza-restaurada:%' ORDER BY id DESC LIMIT 1",
            (encargo,),
        ).fetchone()
        self.assertIsNotNone(restauracion)
        runtime = self.reg.base / "runtime" / encargo
        runtime.mkdir(parents=True)
        (runtime / "abierto.db").write_text("abierto", encoding="utf-8")
        with mock.patch.object(ciclo, "borrar_arbol", side_effect=error):
            barrer(self.reg, ahora_iso(), time.time(), None)
            barrer(self.reg, ahora_iso(), time.time(), None)
        fallos = [
            ukey for (ukey,) in self.reg.db.execute(
                "SELECT ukey FROM events WHERE encargo = ? "
                "AND ukey LIKE 'limpieza-fallida:%' ORDER BY id",
                (encargo,),
            )
        ]
        self.assertEqual(fallos[-2:], [
            f"limpieza-fallida:{encargo}:{restauracion[0]}:1",
            f"limpieza-fallida:{encargo}:{restauracion[0]}:2",
        ])
        self.assertEqual(fallos_legados(self.reg, encargo),
                         (2, str(restauracion[0])))


if __name__ == "__main__":
    unittest.main()
