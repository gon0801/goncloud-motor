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
        path.parent.mkdir(parents=True, exist_ok=True)
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
