"""Log de eventos append-only en SQLite: la única verdad del sistema.

Un solo escritor (el daemon). El CLI no escribe aquí: deja archivos en inbox/.
El estado se obtiene plegando los eventos (machine.fold); nada se edita ni se borra.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Event:
    id: int
    ts: float
    plan: str
    block: str
    kind: str
    data: dict


@dataclass(frozen=True)
class Committed:
    ids: tuple[int, ...]


@dataclass(frozen=True)
class AlreadyCommitted:
    ids: tuple[int, ...]


class Store:
    @classmethod
    def solo_lectura(cls, path: Path) -> Store:
        """Abre la base sin poder escribirla ni crearla (`motor estado` mira, no es el escritor)."""
        s = cls.__new__(cls)
        s.db = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30
        )
        return s

    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        from .formato import activa, admite_escritor

        admite_escritor(path.parent)
        activa(path.parent)
        self.db = sqlite3.connect(path, isolation_level=None, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, plan TEXT NOT NULL,"
            " block TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL, ukey TEXT UNIQUE)"
        )
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_events_plan ON events(plan)")
        self.db.execute(
            "CREATE TRIGGER IF NOT EXISTS events_append_only BEFORE UPDATE ON events "
            "BEGIN SELECT RAISE(ABORT, 'append-only'); END"
        )
        self.db.execute(
            "CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events "
            "BEGIN SELECT RAISE(ABORT, 'append-only'); END"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS batches ("
            " command_id TEXT PRIMARY KEY, ids TEXT NOT NULL)"
        )

    def append(
        self,
        plan: str,
        block: str,
        kind: str,
        data: dict,
        *,
        ukey: str | None = None,
        ts: float | None = None,
    ) -> bool:
        """Agrega un evento. Con `ukey`, el mismo hecho dos veces queda una sola vez (idempotente)."""
        try:
            self.db.execute(
                "INSERT INTO events (ts, plan, block, kind, data, ukey) VALUES (?,?,?,?,?,?)",
                (
                    ts if ts is not None else time.time(),
                    plan,
                    block,
                    kind,
                    json.dumps(data, ensure_ascii=False, sort_keys=True),
                    ukey,
                ),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def append_batch(self, command_id: str, events: list[dict]) -> Committed | AlreadyCommitted:
        """Una transacción. El mismo `command_id` devuelve los ids ya escritos.

        Una colisión de `ukey` que no es ese comando aborta el lote entero.
        """
        if not command_id:
            raise ValueError("append_batch necesita command_id")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT ids FROM batches WHERE command_id = ?", (command_id,)
            ).fetchone()
            if row:
                self.db.execute("ROLLBACK")
                return AlreadyCommitted(tuple(json.loads(row[0])))
            ids: list[int] = []
            for ev in events:
                cur = self.db.execute(
                    "INSERT INTO events (ts, plan, block, kind, data, ukey) VALUES (?,?,?,?,?,?)",
                    (
                        ev.get("ts", time.time()),
                        ev["plan"],
                        ev.get("block", ""),
                        ev["kind"],
                        json.dumps(ev["data"], ensure_ascii=False, sort_keys=True),
                        ev.get("ukey"),
                    ),
                )
                ids.append(int(cur.lastrowid))
            self.db.execute(
                "INSERT INTO batches (command_id, ids) VALUES (?, ?)",
                (command_id, json.dumps(ids)),
            )
            self.db.execute("COMMIT")
            return Committed(tuple(ids))
        except Exception:
            try:
                self.db.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def decision(self, command_id: str) -> str | None:
        """El resultado guardado con ese comando, o None si el lote no existe."""
        row = self.db.execute(
            "SELECT ids FROM batches WHERE command_id = ?", (command_id,)
        ).fetchone()
        if row is None:
            return None
        ids = json.loads(row[0])
        if not ids:
            return ""
        marks = ",".join("?" for _ in ids)
        found = self.db.execute(
            f"SELECT data FROM events WHERE id IN ({marks}) AND kind = 'CommandReceived'",
            tuple(ids),
        ).fetchall()
        if not found:
            return ""
        return str(json.loads(found[-1][0]).get("resultado", ""))

    def events(
        self, plan: str | None = None, kind: str | tuple[str, ...] | None = None
    ) -> list[Event]:
        q = "SELECT id, ts, plan, block, kind, data FROM events"
        args: list = []
        conds: list[str] = []
        if plan is not None:
            conds.append("plan = ?")
            args.append(plan)
        if kind is not None:
            if isinstance(kind, tuple):
                conds.append(f"kind IN ({','.join('?' for _ in kind)})")
                args.extend(kind)
            else:
                conds.append("kind = ?")
                args.append(kind)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        rows = self.db.execute(q + " ORDER BY id", tuple(args)).fetchall()
        return [Event(r[0], r[1], r[2], r[3], r[4], json.loads(r[5])) for r in rows]
