"""Registro append-only del encargo. No abre el home del motor instalado."""

from __future__ import annotations

import atexit
import hashlib
import json
import re
import sqlite3
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from .dominio import Encargo, TransicionIlegal, reducir

_VIVO = Path.home() / ".motor"

Resultado = Literal["nuevo", "igual"]


class BaseProhibida(RuntimeError):
    pass


@dataclass(frozen=True)
class Evento:
    kind: str
    data: Mapping[str, object]
    ukey: str


class Rechazo(Exception):
    """El registro no aceptó el lote y no escribió ninguno de sus eventos."""


class EventoIlegal(Rechazo):
    def __init__(self, indice: int, kind: str, motivo: str) -> None:
        super().__init__(f"el evento {indice} del lote ({kind}) es ilegal: {motivo}")
        self.indice = indice
        self.kind = kind
        self.motivo = motivo


class ClaveOcupada(Rechazo):
    """`campos` nombra en qué difiere el hecho guardado: las claves de `data`, y
    `@encargo` o `@kind` si la ukey es de otro encargo o de otro tipo."""

    def __init__(self, indice: int, ukey: str, campos: tuple[str, ...] = ()) -> None:
        super().__init__(f"la ukey del evento {indice} del lote ya guarda otro hecho")
        self.indice = indice
        self.ukey = ukey
        self.campos = campos


# Solo palabras con guiones y con tope: un id de encargo (`enc_` y hexadecimal)
# o un hash nunca casan, así que la línea del conteo no puede llevar datos del
# encargo. El nombre real más largo mide 34.
_TOPE_DEL_NOMBRE = 64
_CLASE_DE_UKEY = re.compile(r"[a-z]+(?:-[a-z]+)*")
_TIPO = re.compile(r"[a-z]+(?:_[a-z]+)*")
_TOPE_DE_CAMPOS = 5

# Un rechazo de cada vuelta del supervisor (20 s) deja 144 líneas al día en vez de 4320.
_INTERVALO_DEL_CONTEO_S = 600.0


@dataclass(frozen=True)
class _Clave:
    """Lo que distingue una línea del conteo. `campos` es `None` en un rechazo
    `ilegal`, que no compara dos hechos."""

    rechazo: Literal["ajena", "ilegal"]
    kind: str
    ukey: str
    campos: str | None


def _visible(nombre: str, forma: re.Pattern[str]) -> str:
    return nombre if len(nombre) <= _TOPE_DEL_NOMBRE and forma.fullmatch(nombre) else "?"


def _escribir_linea(clave: _Clave, veces: int) -> None:
    campos = "" if clave.campos is None else f" campos={clave.campos}"
    try:
        sys.stderr.write(
            f"registro: rechazo={clave.rechazo} kind={clave.kind} ukey={clave.ukey}"
            f"{campos} veces={veces}\n"
        )
        sys.stderr.flush()
    except Exception:
        # De mejor esfuerzo: un stderr cerrado o roto no cambia lo que responde `append`.
        pass


class _Conteo:
    """Una línea por clave e intervalo, con las veces que ocurrió desde la anterior.

    La primera vez sale enseguida. Las siguientes dentro del intervalo se suman y
    salen con la primera que llega pasado el intervalo, o al terminar el proceso:
    la suma de `veces` es el total, salvo lo pendiente de un proceso que muere
    sin terminar."""

    def __init__(self, reloj: Callable[[], float] = time.monotonic) -> None:
        self._reloj = reloj
        self._candado = threading.Lock()
        self._ultima: dict[_Clave, float] = {}
        self._pendientes: dict[_Clave, int] = {}

    def contar(self, clave: _Clave) -> None:
        with self._candado:
            ahora = self._reloj()
            veces = self._pendientes.pop(clave, 0) + 1
            ultima = self._ultima.get(clave)
            if ultima is not None and ahora - ultima < _INTERVALO_DEL_CONTEO_S:
                self._pendientes[clave] = veces
                return
            self._ultima[clave] = ahora
        _escribir_linea(clave, veces)

    def vaciar(self) -> None:
        with self._candado:
            pendientes, self._pendientes = self._pendientes, {}
        for clave, veces in pendientes.items():
            _escribir_linea(clave, veces)


_CONTEO = _Conteo()
atexit.register(_CONTEO.vaciar)


def _campos_visibles(campos: tuple[str, ...]) -> str:
    # Solo nombres de claves, que son del código: un valor puede llevar rutas o
    # repos privados.
    visibles = sorted(
        {campo if campo in ("@encargo", "@kind") else _visible(campo, _TIPO) for campo in campos}
    )
    sobran = len(visibles) - _TOPE_DE_CAMPOS
    return ",".join(visibles[:_TOPE_DE_CAMPOS] + ([f"+{sobran}"] if sobran > 0 else []))


def _contar_rechazo(rechazo: Rechazo, kind: str, ukey: str) -> None:
    clase, separador, _resto = ukey.partition(":")
    clase_visible = _visible(clase, _CLASE_DE_UKEY)
    ukey_visible = f"{clase_visible}:*" if separador and clase_visible != "?" else "?"
    if isinstance(rechazo, ClaveOcupada):
        clave = _Clave("ajena", _visible(kind, _TIPO), ukey_visible, _campos_visibles(rechazo.campos))
    else:
        clave = _Clave("ilegal", _visible(kind, _TIPO), ukey_visible, None)
    _CONTEO.contar(clave)


def contar_omitidos(omitidos: Iterable[tuple[Evento, Rechazo]]) -> None:
    """Cuenta por stderr, igual que `append`, los eventos que un paso dejó fuera de
    su lote porque el registro los rechazaría."""
    for evento, rechazo in omitidos:
        _contar_rechazo(rechazo, evento.kind, evento.ukey)


@dataclass(frozen=True)
class _Plegado:
    estado: Encargo | None
    ultimo: int
    conteo: int


def _estable(data: Mapping[str, object]) -> dict:
    return {k: v for k, v in data.items() if k != "hora"}


_AUSENTE = object()


def _campos_distintos(
    guardado: tuple[str, str, Mapping[str, object]], nuevo: tuple[str, str, Mapping[str, object]]
) -> tuple[str, ...]:
    """En qué difieren dos hechos de la misma ukey, con la comparación de `_cotejar`."""
    (encargo, kind, data), (otro_encargo, otro_kind, otra_data) = guardado, nuevo
    a, b = _estable(data), _estable(otra_data)
    return (
        *(("@encargo",) if encargo != otro_encargo else ()),
        *(("@kind",) if kind != otro_kind else ()),
        *(k for k in a.keys() | b.keys() if a.get(k, _AUSENTE) != b.get(k, _AUSENTE)),
    )


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
        """Un lote de un evento que devuelve el rechazo como `ajena` o `ilegal`
        en vez de lanzarlo. Cada rechazo deja una línea por stderr para medir
        cuántos se callan hoy."""
        try:
            (resultado,) = self._escribir(encargo, [(Evento(kind, data, ukey), ts)])
        except ClaveOcupada as rechazo:
            _contar_rechazo(rechazo, kind, ukey)
            return "ajena"
        except EventoIlegal as rechazo:
            _contar_rechazo(rechazo, kind, ukey)
            return "ilegal"
        return resultado

    def append_many(self, encargo: str, eventos: Sequence[Evento]) -> tuple[Resultado, ...]:
        """Escribe el lote entero o nada, en una sola transacción.

        Un evento cuya ukey ya guarda el mismo hecho (sin mirar `hora`) da
        `igual` y no se reescribe; los demás se validan sobre el estado guardado
        y se insertan en orden, y el estado que queda es el que da `proyectar`.
        Una ukey que guarda otro hecho lanza `ClaveOcupada`; un evento que el
        reductor rechaza lanza `EventoIlegal`. Una ukey repetida dentro del lote
        es `ValueError`."""
        return self._escribir(
            encargo, [(evento, str(evento.data.get("hora") or "")) for evento in eventos]
        )

    def proyectar(self, encargo: str, eventos: Sequence[Evento]) -> Encargo:
        """El estado que deja `append_many(encargo, eventos)` si se llama ahora, sin escribir.

        Es la misma cuenta que hace la escritura: un evento cuya ukey ya guarda
        el mismo hecho está en lo guardado y no se vuelve a aplicar. Lanza lo
        mismo que lanzaría `append_many`. El paso decide con este estado, así
        que lo que decide es lo que queda guardado."""
        if not eventos:
            raise ValueError("un lote vacío no proyecta ningún estado")
        # Una sola lectura: otro proceso que escribe entre el cotejo y el plegado
        # no cambia la cuenta a la mitad.
        self.db.execute("BEGIN")
        try:
            resultados = self._cotejar_lote(encargo, eventos)
            estado, _conteo = self._aplicar_ausentes(encargo, eventos, resultados)
        finally:
            self._deshacer()
        return cast(Encargo, estado)

    def _escribir(
        self, encargo: str, filas: Sequence[tuple[Evento, str]]
    ) -> tuple[Resultado, ...]:
        if not filas:
            return ()
        eventos = [evento for evento, _ts in filas]
        payloads = [
            json.dumps(dict(evento.data), ensure_ascii=False, sort_keys=True)
            for evento in eventos
        ]
        self.db.execute("BEGIN IMMEDIATE")
        try:
            resultados = self._cotejar_lote(encargo, eventos)
            if "nuevo" not in resultados:
                self.db.execute("ROLLBACK")
                return resultados
            estado, conteo = self._aplicar_ausentes(encargo, eventos, resultados)
            ultimo = 0
            for (evento, ts), payload, resultado in zip(filas, payloads, resultados, strict=True):
                if resultado == "igual":
                    continue
                cursor = self.db.execute(
                    "INSERT INTO events (ts, encargo, kind, data, ukey) VALUES (?,?,?,?,?)",
                    (ts, encargo, evento.kind, payload, evento.ukey),
                )
                ultimo = int(cursor.lastrowid or 0)
            self.db.execute("COMMIT")
        except BaseException:
            self._deshacer()
            raise
        self._recordar_plegado(
            encargo, cast(Encargo, estado), ultimo, conteo + resultados.count("nuevo")
        )
        return resultados

    def _deshacer(self) -> None:
        # Tras un disco lleno o un error de E/S SQLite ya deshizo la transacción:
        # un ROLLBACK de más lanzaría y taparía el error real.
        if self.db.in_transaction:
            self.db.execute("ROLLBACK")

    def _cotejar_lote(self, encargo: str, eventos: Sequence[Evento]) -> tuple[Resultado, ...]:
        ukeys = [evento.ukey for evento in eventos]
        if len(set(ukeys)) != len(ukeys):
            raise ValueError("el lote repite una ukey")
        return tuple(
            self._cotejar(encargo, indice, evento) for indice, evento in enumerate(eventos)
        )

    def _aplicar_ausentes(
        self, encargo: str, eventos: Sequence[Evento], resultados: Sequence[Resultado]
    ) -> tuple[Encargo | None, int]:
        """El estado guardado con los eventos `nuevo` del lote aplicados en orden.

        `append_many` y `proyectar` lo calculan aquí. Un evento `igual` ya está
        en lo guardado: aplicarlo otra vez contaría dos veces, por ejemplo, una
        nota de `procedimiento`."""
        estado, _ultimo, conteo = self._estado_vigente(encargo)
        for indice, (evento, resultado) in enumerate(zip(eventos, resultados, strict=True)):
            if resultado == "igual":
                continue
            try:
                estado = reducir(estado, {"kind": evento.kind, "data": evento.data}, nuevo=True)
            except TransicionIlegal as exc:
                raise EventoIlegal(indice, evento.kind, str(exc)) from exc
        return estado, conteo

    def _cotejar(self, encargo: str, indice: int, evento: Evento) -> Resultado:
        fila = self.db.execute(
            "SELECT encargo, kind, data FROM events WHERE ukey = ?", (evento.ukey,)
        ).fetchone()
        if fila is None:
            return "nuevo"
        campos = _campos_distintos(
            (fila[0], fila[1], json.loads(fila[2])), (encargo, evento.kind, evento.data)
        )
        if not campos:
            return "igual"
        raise ClaveOcupada(indice, evento.ukey, campos)

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
