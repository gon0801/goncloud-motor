from __future__ import annotations

import itertools
import json
import os
import sqlite3
import threading
import weakref
from pathlib import Path

from motor.encargos import registro

SALIDA = Path(os.environ["MOTOR_CORPUS_DIR"])
REGISTROS = SALIDA / "registros"
REGISTROS.mkdir(parents=True, exist_ok=True)
_NUMERO = itertools.count(1)
_CANDADO = threading.Lock()
_INSTANCIAS: weakref.WeakKeyDictionary[registro.Registro, tuple[Path, tuple]] = (
    weakref.WeakKeyDictionary()
)
_ACEPTADOS: dict[tuple, set[int]] = {}
_INICIO = registro.Registro.__init__
_ESCRIBIR = registro.Registro._escribir
_TABLA = (
    "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
    "encargo TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, ukey TEXT UNIQUE)"
)


def _sincronizar(instancia: registro.Registro) -> tuple[Path, tuple]:
    archivo, origen = _INSTANCIAS[instancia]
    with _CANDADO:
        ids = set(_ACEPTADOS.get(origen, ()))
    with sqlite3.connect(archivo) as copia:
        filas = instancia.db.execute(
            "SELECT id, ts, encargo, kind, data, ukey FROM events ORDER BY id"
        )
        copia.executemany(
            "INSERT OR IGNORE INTO events (id, ts, encargo, kind, data, ukey)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (fila for fila in filas if fila[0] in ids),
        )
    return archivo, origen


def _iniciar(instancia: registro.Registro, base: Path) -> None:
    _INICIO(instancia, base)
    stat = (instancia.base / "registro.sqlite").stat()
    origen = (str(instancia.base), stat.st_dev, stat.st_ino)
    nombre = f"{os.environ.get('PYTEST_XDIST_WORKER', 'local')}-{os.getpid()}-{next(_NUMERO)}.sqlite"
    archivo = REGISTROS / nombre
    with sqlite3.connect(archivo) as copia:
        copia.execute(_TABLA)
    _INSTANCIAS[instancia] = archivo, origen
    _sincronizar(instancia)


def _escribir(
    instancia: registro.Registro, encargo: str, filas: list[tuple[registro.Evento, str]]
) -> tuple[registro.Resultado, ...]:
    try:
        resultados = _ESCRIBIR(instancia, encargo, filas)
    except (registro.EventoIlegal, registro.ClaveOcupada) as rechazo:
        archivo, _ = _sincronizar(instancia)
        with sqlite3.connect(archivo) as copia:
            conteo = copia.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        registro_rechazado = {
            "archivo": archivo.name, "encargo": encargo, "conteo": conteo,
            "indice": rechazo.indice, "tipo": type(rechazo).__name__,
            "lote": [
                {"kind": evento.kind, "data": dict(evento.data), "ukey": evento.ukey, "ts": ts}
                for evento, ts in filas
            ],
        }
        linea = (json.dumps(registro_rechazado, ensure_ascii=False) + "\n").encode("utf-8")
        with _CANDADO:
            fd = os.open(SALIDA / "rechazos.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, linea)
            finally:
                os.close(fd)
        raise
    _, origen = _INSTANCIAS[instancia]
    if "nuevo" in resultados:
        ukeys = [evento.ukey for (evento, _), resultado in zip(filas, resultados, strict=True)
                 if resultado == "nuevo"]
        placeholders = ", ".join("?" for _ in ukeys)
        nuevos = {
            fila[0] for fila in instancia.db.execute(
                f"SELECT id FROM events WHERE ukey IN ({placeholders})", ukeys
            )
        }
        with _CANDADO:
            _ACEPTADOS.setdefault(origen, set()).update(nuevos)
    _sincronizar(instancia)
    return resultados


def pytest_configure() -> None:
    registro.Registro.__init__ = _iniciar
    registro.Registro._escribir = _escribir
