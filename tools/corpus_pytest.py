from __future__ import annotations

import itertools
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
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
_INICIO = registro.Registro.__init__
_ESCRIBIR = registro.Registro._escribir
_POPEN = subprocess.Popen
_TABLA = (
    "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
    "encargo TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, ukey TEXT UNIQUE)"
)


def _ruta_aceptados(origen: tuple) -> Path:
    carpeta = SALIDA / "aceptados"
    carpeta.mkdir(mode=0o700, exist_ok=True)
    return carpeta / (hashlib.sha256(repr(origen).encode()).hexdigest() + ".jsonl")


def _sincronizar(instancia: registro.Registro) -> tuple[Path, tuple]:
    archivo, origen = _INSTANCIAS[instancia]
    ids: set[int] = set()
    ruta = _ruta_aceptados(origen)
    if ruta.exists():
        with ruta.open(encoding="utf-8") as entrada:
            ids.update(id for linea in entrada if linea.endswith("\n") for id in json.loads(linea))
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
        origen_error = rechazo.__cause__ or rechazo
        registro_rechazado = {
            "archivo": archivo.name, "encargo": encargo, "conteo": conteo,
            "indice": rechazo.indice, "tipo": type(rechazo).__name__,
            "mensaje": str(rechazo), "tipo_origen": type(origen_error).__name__,
            "mensaje_origen": str(origen_error),
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
        linea = (json.dumps(sorted(nuevos)) + "\n").encode()
        with _CANDADO:
            fd = os.open(_ruta_aceptados(origen), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, linea)
            finally:
                os.close(fd)
    _sincronizar(instancia)
    return resultados


def instalar() -> None:
    if registro.Registro._escribir is _escribir:
        return
    registro.Registro.__init__ = _iniciar
    registro.Registro._escribir = _escribir


def pytest_configure() -> None:
    instalar()

    class PopenCorpus(_POPEN):
        def __init__(self, args, *posicional, **opciones):
            if (isinstance(args, (list, tuple)) and len(args) > 1
                    and str(args[1]) == "-c"
                    and Path(str(args[0])).resolve() == Path(sys.executable).resolve()):
                original = opciones.get("env")
                entorno = dict(os.environ if original is None else original)
                if "MOTOR_CORPUS_DIR" in entorno:
                    entorno["PYTHONPATH"] = (
                        str(Path(__file__).resolve().parents[1])
                        + os.pathsep + entorno.get("PYTHONPATH", "")
                    )
                    opciones["env"] = entorno
            super().__init__(args, *posicional, **opciones)

    subprocess.Popen = PopenCorpus
