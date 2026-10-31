"""Registro append-only del encargo. No abre el home del motor instalado."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .dominio import Encargo, TransicionIlegal, reducir

_VIVO = Path.home() / ".motor"


class BaseProhibida(RuntimeError):
    pass


def _estable(data: dict) -> dict:
    return {k: v for k, v in data.items() if k != "hora"}


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
            "CREATE TABLE IF NOT EXISTS cortes_avisos ("
            " version TEXT PRIMARY KEY, evento INTEGER NOT NULL)"
        )
        self.db.execute(
            "INSERT OR IGNORE INTO cortes_avisos (version, evento) "
            "SELECT 'avances-v1', COALESCE(MAX(id), 0) FROM events"
        )

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
            estado = None
            for kind_prev, data_prev in self.db.execute(
                "SELECT kind, data FROM events WHERE encargo = ? ORDER BY id",
                (encargo,),
            ):
                estado = reducir(estado, {"kind": kind_prev, "data": json.loads(data_prev)})
            try:
                reducir(estado, {"kind": kind, "data": data}, nuevo=True)
            except TransicionIlegal:
                self.db.execute("ROLLBACK")
                return "ilegal"
            self.db.execute(
                "INSERT INTO events (ts, encargo, kind, data, ukey) VALUES (?,?,?,?,?)",
                (ts, encargo, kind, payload, ukey),
            )
            self.db.execute("COMMIT")
            return "nuevo"
        except Exception:
            self.db.execute("ROLLBACK")
            raise

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
        rows = self.db.execute(
            "SELECT id, ts, kind, data FROM events WHERE encargo = ? ORDER BY id",
            (encargo,),
        ).fetchall()
        return [
            {"id": r[0], "ts": r[1], "kind": r[2], "data": json.loads(r[3])} for r in rows
        ]

    def ultimo_evento(self, encargo: str) -> int:
        row = self.db.execute(
            "SELECT MAX(id) FROM events WHERE encargo = ?", (encargo,)
        ).fetchone()
        return int(row[0] or 0)

    def ids(self) -> list[str]:
        rows = self.db.execute(
            "SELECT DISTINCT encargo FROM events ORDER BY encargo"
        ).fetchall()
        return [r[0] for r in rows]

    def corte_avisos(self) -> int:
        return self.db.execute(
            "SELECT evento FROM cortes_avisos WHERE version = 'avances-v1'"
        ).fetchone()[0]

    def fold(self, encargo: str, hasta: int | None = None) -> Encargo | None:
        estado = None
        for ev in self.eventos(encargo):
            if hasta is not None and ev["id"] > hasta:
                break
            estado = reducir(estado, ev)
        return estado

    def anotar(self, encargo: str, evento: dict, ukey: str) -> str:
        data = dict(evento["data"])
        ts = str(data.get("hora") or "")
        return self.append(encargo, evento["kind"], data, ukey, ts)
