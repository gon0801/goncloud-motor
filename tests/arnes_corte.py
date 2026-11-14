"""Arnés de apagón: corta la conexión del registro antes de cada `COMMIT` de un paso.

Un paso aguanta el apagón si, cortado antes de cualquiera de sus `COMMIT`, deja
guardados cero o todos sus eventos, y si al repetirlo el registro pliega igual
que en la corrida sin corte. Cada corrida sale de la misma foto de la escena,
restaurada en la misma ruta para que las rutas absolutas del registro sigan
valiendo.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import unittest
from collections.abc import Callable
from pathlib import Path

from motor.encargos.dominio import Encargo, reducir
from motor.encargos.registro import Registro


class Corte(BaseException):
    """El apagón. Hereda de `BaseException` para que ningún `except Exception` lo atrape."""


class _ConexionQueSeCorta:
    """La conexión real, salvo que lanza `Corte` justo antes del `COMMIT` número `corte`."""

    def __init__(self, real: sqlite3.Connection, corte: int) -> None:
        self._real = real
        self._corte = corte
        self.commits = 0

    def execute(self, sql: str, *args):
        if sql.strip().upper() == "COMMIT":
            self.commits += 1
            if self.commits == self._corte:
                raise Corte(f"apagón antes del COMMIT {self.commits}")
        return self._real.execute(sql, *args)

    def __getattr__(self, nombre: str):
        return getattr(self._real, nombre)


def filas(base: Path) -> list[tuple[str, str, dict]]:
    db = sqlite3.connect(f"file:{base / 'registro.sqlite'}?mode=ro", uri=True)
    try:
        return [
            (kind, ukey, json.loads(data))
            for kind, ukey, data in db.execute(
                "SELECT kind, ukey, data FROM events ORDER BY id"
            )
        ]
    finally:
        db.close()


def _plegado(base: Path, encargo: str) -> Encargo | None:
    """El estado que pliega un proceso nuevo: cada evento guardado, sin validar."""
    registro = Registro(base)
    try:
        estado = None
        for evento in registro.eventos(encargo):
            estado = reducir(estado, evento, nuevo=False)
        return estado
    finally:
        registro.close()


def _correr(base: Path, paso: Callable[[Registro], object], corte: int = 0) -> int:
    registro = Registro(base)
    conexion = _ConexionQueSeCorta(registro.db, corte)
    registro.db = conexion
    try:
        paso(registro)
    finally:
        registro.close()
    return conexion.commits


def cortar_en_cada_commit(
    caso: unittest.TestCase,
    escena: Path,
    base: Path,
    encargo: str,
    paso: Callable[[Registro], object],
) -> int:
    """Corre `paso` sin corte y después cortado antes de cada uno de sus `COMMIT`.

    `escena` es el directorio entero del caso (registro, copias de git,
    destino); `base` es el home del registro dentro de ella. Nada puede tener
    abierto el registro al llamar. Devuelve cuántos `COMMIT` hizo el paso sin
    corte."""
    foto = escena.with_name(escena.name + "-foto")
    shutil.copytree(escena, foto, symlinks=True)

    def restaurar() -> None:
        shutil.rmtree(escena)
        shutil.copytree(foto, escena, symlinks=True)

    previas = len(filas(base))
    commits = _correr(base, paso)
    completo = filas(base)[previas:]
    plegado = _plegado(base, encargo)
    caso.assertTrue(completo, "el paso sin corte no escribió nada")
    for corte in range(1, commits + 1):
        with caso.subTest(corte=corte):
            restaurar()
            with caso.assertRaises(Corte):
                _correr(base, paso, corte)
            guardado = [(kind, ukey) for kind, ukey, _data in filas(base)[previas:]]
            caso.assertIn(
                guardado,
                ([], [(kind, ukey) for kind, ukey, _data in completo]),
                f"el apagón antes del COMMIT {corte} dejó el paso a medias",
            )
            _correr(base, paso)
            caso.assertEqual(filas(base)[previas:], completo)
            caso.assertEqual(_plegado(base, encargo), plegado)
    return commits
