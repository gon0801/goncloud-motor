"""Registro append-only del encargo. No abre el home del motor instalado."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from .dominio import Encargo, TransicionIlegal, reducir

_VIVO = Path.home() / ".motor"


class BaseProhibida(RuntimeError):
    pass


@dataclass(frozen=True)
class _Plegado:
    estado: Encargo | None
    ultimo: int
    conteo: int


def _estable(data: dict) -> dict:
    return {k: v for k, v in data.items() if k != "hora"}


class _CacheDeLaBase:
    """Plegados ya calculados para una base, compartidos por sus `Registro`.

    La conexión sqlite sigue siendo de un solo hilo: cada `Registro` valida los
    tokens contra su propia conexión y solo toma de aquí el estado ya plegado.
    El candado cubre los diccionarios; el plegado se hace fuera para no
    serializar a los hilos que leen encargos distintos.
    """

    def __init__(self) -> None:
        self.candado = threading.Lock()
        self.plegados: dict[str, _Plegado] = {}
        self.plegados_hasta: dict[tuple[str, int], _Plegado] = {}


# La identidad del fichero y la firma del primer evento separan una base
# recreada en la misma ruta: un registro nuevo vuelve a empezar los ids y una
# colisión de token devolvería historia vieja. La cota impide que un proceso
# que abre muchas bases acumule plegados para siempre.
_CACHES: OrderedDict[tuple[str, tuple[int, int], str], _CacheDeLaBase] = OrderedDict()
_CACHE_MAX = 16
_CACHES_CANDADO = threading.Lock()


def _identidad_del_fichero(base: Path) -> tuple[int, int]:
    try:
        stat = (base / "registro.sqlite").stat()
    except OSError:
        return (0, 0)
    return (int(stat.st_dev), int(stat.st_ino))


def _firma_de_la_base(db: sqlite3.Connection) -> str:
    fila = db.execute(
        "SELECT id, ts, kind, data FROM events ORDER BY id LIMIT 1"
    ).fetchone()
    if not fila:
        return ""
    return hashlib.sha256("\0".join(str(x) for x in fila).encode()).hexdigest()


def _cache_de(base: Path, db: sqlite3.Connection) -> _CacheDeLaBase:
    firma = _firma_de_la_base(db)
    if not firma:
        # Una base vacía no comparte caché: todavía no tiene identidad estable.
        return _CacheDeLaBase()
    clave = (str(base), _identidad_del_fichero(base), firma)
    with _CACHES_CANDADO:
        cache = _CACHES.get(clave)
        if cache is not None:
            _CACHES.move_to_end(clave)
            return cache
        cache = _CacheDeLaBase()
        _CACHES[clave] = cache
        while len(_CACHES) > _CACHE_MAX:
            _CACHES.popitem(last=False)
        return cache


def exigir_base(path: Path) -> Path:
    base = Path(path).expanduser().resolve()
    vivo = _VIVO.expanduser().resolve()
    if base == vivo or vivo in base.parents:
        raise BaseProhibida(f"no uso la base instalada {vivo}")
    if (base / "motor.db").exists() or (base / "formato").exists():
        raise BaseProhibida(f"{base} es un home del motor anterior")
    base.mkdir(parents=True, exist_ok=True)
    return base


class Registro:
    def __init__(self, base: Path):
        self.base = exigir_base(base)
        self.db = sqlite3.connect(self.base / "registro.sqlite", isolation_level=None, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, encargo TEXT NOT NULL,"
            " kind TEXT NOT NULL, data TEXT NOT NULL, ukey TEXT UNIQUE)"
        )
        self.db.execute(
            "CREATE TRIGGER IF NOT EXISTS events_append_only BEFORE UPDATE ON events "
            "BEGIN SELECT RAISE(ABORT, 'append-only'); END"
        )
        self.db.execute(
            "CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events "
            "BEGIN SELECT RAISE(ABORT, 'append-only'); END"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_encargo ON events(encargo, id)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS cortes_avisos ("
            " version TEXT PRIMARY KEY, evento INTEGER NOT NULL)"
        )
        self.db.execute(
            "INSERT OR IGNORE INTO cortes_avisos (version, evento) "
            "SELECT 'avances-v1', COALESCE(MAX(id), 0) FROM events"
        )
        # El corte de los cierres de cancelados se fija la primera vez que esta
        # versión abre la base: todo cancelado anterior es historia (main nunca
        # le escribió aviso de cierre) y avisarlo al desplegar sería una ráfaga.
        self.db.execute(
            "INSERT OR IGNORE INTO cortes_avisos (version, evento) "
            "SELECT 'cierres-cancelados-v1', COALESCE(MAX(id), 0) FROM events"
        )
        self._cache_actual: _CacheDeLaBase | None = None
        self._ultimos: dict[str, tuple[int, int]] | None = None
        self._data_version = -1
        self._cambios = -1

    @property
    def _cache(self) -> _CacheDeLaBase:
        # Se resuelve en el primer fold: una instancia que nace con la base
        # vacía y escribe antes de plegar comparte la caché ya poblada.
        if self._cache_actual is None:
            self._cache_actual = _cache_de(self.base, self.db)
        return self._cache_actual

    def close(self) -> None:
        self.db.close()

    def append(self, encargo: str, kind: str, data: dict, ukey: str, ts: str) -> str:
        """La decisión y el insert van en la misma transacción. `igual` solo si es el mismo hecho."""
        payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT encargo, kind, data FROM events WHERE ukey = ?", (ukey,)
            ).fetchone()
            if row:
                self.db.execute("ROLLBACK")
                guardado = json.loads(row[2])
                if row[0] == encargo and row[1] == kind and _estable(guardado) == _estable(data):
                    return "igual"
                return "ajena"
            estado, _ultimo, conteo = self._estado_vigente(encargo)
            try:
                aplicado = reducir(estado, {"kind": kind, "data": data}, nuevo=True)
            except TransicionIlegal:
                self.db.execute("ROLLBACK")
                return "ilegal"
            cursor = self.db.execute(
                "INSERT INTO events (ts, encargo, kind, data, ukey) VALUES (?,?,?,?,?)",
                (ts, encargo, kind, payload, ukey),
            )
            nuevo_id = int(cursor.lastrowid or 0)
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        self._recordar_plegado(encargo, aplicado, nuevo_id, conteo + 1)
        return "nuevo"

    def por_ukey(self, ukey: str) -> dict | None:
        row = self.db.execute(
            "SELECT encargo, kind, data, ts FROM events WHERE ukey = ?", (ukey,)
        ).fetchone()
        if not row:
            return None
        return {
            "encargo": row[0],
            "kind": row[1],
            "data": json.loads(row[2]),
            "ts": row[3],
        }

    def eventos(self, encargo: str) -> list[dict]:
        return self._rango(encargo, 0)

    def ultimos(self) -> dict[str, tuple[int, int]]:
        """MAX(id) y cantidad de eventos por encargo, en una sola consulta."""
        self._refrescar_ultimos()
        return dict(self._ultimos or {})

    def _refrescar_ultimos(self) -> None:
        version = int(self.db.execute("PRAGMA data_version").fetchone()[0])
        cambios = self.db.total_changes
        if (
            self._ultimos is None
            or version != self._data_version
            or cambios != self._cambios
        ):
            self._ultimos = {
                fila[0]: (int(fila[1] or 0), int(fila[2] or 0))
                for fila in self.db.execute(
                    "SELECT encargo, MAX(id), COUNT(*) FROM events GROUP BY encargo"
                )
            }
            self._data_version = version
            self._cambios = cambios

    def _ultimo_y_conteo(self, encargo: str) -> tuple[int, int]:
        self._refrescar_ultimos()
        return (self._ultimos or {}).get(encargo, (0, 0))

    def ultimo_evento(self, encargo: str, excluir: tuple[str, ...] = ()) -> int:
        sql = "SELECT MAX(id) FROM events WHERE encargo = ?"
        args: list = [encargo]
        if excluir:
            marcas = ",".join("?" for _ in excluir)
            sql += f" AND kind NOT IN ({marcas})"
            args.extend(excluir)
        row = self.db.execute(sql, args).fetchone()
        return int(row[0] or 0)

    def ids(self) -> list[str]:
        rows = self.db.execute(
            "SELECT DISTINCT encargo FROM events ORDER BY encargo"
        ).fetchall()
        return [r[0] for r in rows]

    def corte_avisos(self, version: str = "avances-v1") -> int:
        fila = self.db.execute(
            "SELECT evento FROM cortes_avisos WHERE version = ?", (version,)
        ).fetchone()
        return int(fila[0])

    def _rango(self, encargo: str, desde: int, hasta: int | None = None) -> list[dict]:
        sql = "SELECT id, ts, kind, data FROM events WHERE encargo = ? AND id > ?"
        args: list = [encargo, desde]
        if hasta is not None:
            sql += " AND id <= ?"
            args.append(hasta)
        sql += " ORDER BY id"
        rows = self.db.execute(sql, args).fetchall()
        return [
            {"id": r[0], "ts": r[1], "kind": r[2], "data": json.loads(r[3])} for r in rows
        ]

    def _plegar(
        self, encargo: str, hasta: int | None = None
    ) -> tuple[Encargo | None, int, int]:
        estado = None
        ultimo = 0
        conteo = 0
        for ev in self._rango(encargo, 0, hasta):
            estado = reducir(estado, ev, nuevo=False)
            ultimo = ev["id"]
            conteo += 1
        return estado, ultimo, conteo

    def _fold_hasta(self, encargo: str, hasta: int) -> Encargo | None:
        fila = self.db.execute(
            "SELECT MAX(id), COUNT(*) FROM events WHERE encargo = ? AND id <= ?",
            (encargo, hasta),
        ).fetchone()
        ultimo = int(fila[0] or 0)
        conteo = int(fila[1] or 0)
        with self._cache.candado:
            guardado = self._cache.plegados_hasta.get((encargo, hasta))
        if guardado is not None and guardado.ultimo == ultimo and guardado.conteo == conteo:
            return guardado.estado
        estado, ultimo, conteo = self._plegar(encargo, hasta)
        with self._cache.candado:
            self._cache.plegados_hasta[(encargo, hasta)] = _Plegado(estado, ultimo, conteo)
        return estado

    def _estado_vigente(self, encargo: str) -> tuple[Encargo | None, int, int]:
        """El plegado que coincide con lo persistido, reutilizando la caché.

        `fold` y `append` lo usan: dentro de `BEGIN IMMEDIATE` ningún otro
        escritor cambia la base entre la validación y el insert, así que lo que
        se aplica en memoria es exactamente lo que queda guardado. Un token que
        no coincide se repliega desde el último punto válido."""
        ultimo, conteo = self._ultimo_y_conteo(encargo)
        if ultimo == 0:
            # Sin eventos el estado es None: no se resuelve la caché de una base
            # todavía vacía, que no tiene identidad estable (firma del primer
            # evento) hasta que el primer append confirme.
            return None, 0, 0
        with self._cache.candado:
            guardado = self._cache.plegados.get(encargo)
        if guardado is not None and guardado.ultimo == ultimo and guardado.conteo == conteo:
            return guardado.estado, ultimo, conteo
        if (
            guardado is not None
            and guardado.ultimo < ultimo
            and guardado.conteo <= conteo
        ):
            nuevos = self._rango(encargo, guardado.ultimo, ultimo)
            if (
                nuevos
                and nuevos[-1]["id"] == ultimo
                and guardado.conteo + len(nuevos) == conteo
            ):
                estado = guardado.estado
                for ev in nuevos:
                    estado = reducir(estado, ev, nuevo=False)
                with self._cache.candado:
                    self._cache.plegados[encargo] = _Plegado(estado, ultimo, conteo)
                return estado, ultimo, conteo
        # El token se toma del mismo recorrido que produjo el estado: si otro
        # proceso escribe durante el plegado, el caché no queda adelantado.
        estado, ultimo, conteo = self._plegar(encargo)
        with self._cache.candado:
            self._cache.plegados[encargo] = _Plegado(estado, ultimo, conteo)
        return estado, ultimo, conteo

    def _recordar_plegado(
        self, encargo: str, estado: Encargo, ultimo: int, conteo: int
    ) -> None:
        """Deja la caché en el estado que se acaba de confirmar y evita el
        `GROUP BY` del siguiente `append` en esta conexión."""
        with self._cache.candado:
            self._cache.plegados[encargo] = _Plegado(estado, ultimo, conteo)
        if self._ultimos is not None:
            self._ultimos[encargo] = (ultimo, conteo)
            self._cambios = self.db.total_changes

    def fold(self, encargo: str, hasta: int | None = None) -> Encargo | None:
        if hasta is not None:
            return self._fold_hasta(encargo, hasta)
        return self._estado_vigente(encargo)[0]

    def anotar(self, encargo: str, evento: dict, ukey: str) -> str:
        data = dict(evento["data"])
        ts = str(data.get("hora") or "")
        return self.append(encargo, evento["kind"], data, ukey, ts)
