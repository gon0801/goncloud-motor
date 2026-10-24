"""Lado servidor de la señal a Kuma. Se manda por stdin: `python3 - <kuma.db> <alta|baja|estado>`.

alta   respalda kuma.db una vez por día, deja los monitores push del motor activos y conectados al
       aviso, reinicia Kuma y escribe en stdout `<clave> <token>` por monitor.
baja   respalda, deja los monitores con active 0, reinicia Kuma. No escribe tokens.
estado escribe `<clave> <active> <avisos> <señales>` por monitor; nunca lee config ni push_token.

Kuma sigue corriendo todo el tiempo: se escribe con la base abierta (WAL) y busy_timeout de 30 s,
y se reinicia con `docker restart`, nunca `docker stop`.
"""

import datetime
import os
import secrets
import sqlite3
import string
import subprocess
import sys
from pathlib import Path

MONITORES = {"mini": "Motor de la Mini", "pc": "Motor de la PC"}
PLANTILLA = "backup orbit"
AVISO = "Server Alerts"
CONTENEDOR = "uptime-kuma"
BUSY_MS = 30_000
SIN_COPIAR = {
    "id",
    "name",
    "push_token",
    "active",
    "interval",
    "retry_interval",
    "maxretries",
    "created_date",
}
ALFABETO = string.ascii_letters + string.digits


def conectar(db: Path, solo_lectura: bool = False) -> sqlite3.Connection:
    uri = f"file:{db}?mode=ro" if solo_lectura else f"file:{db}"
    con = sqlite3.connect(uri, uri=True, timeout=BUSY_MS / 1000, isolation_level=None)
    con.execute(f"PRAGMA busy_timeout={BUSY_MS}")
    con.row_factory = sqlite3.Row
    return con


def respaldar(db: Path) -> None:
    destino = db.with_name(f"{db.name}.antes-motor-03-{datetime.date.today():%Y%m%d}")
    if destino.exists():
        return
    parcial = destino.with_name(destino.name + ".parcial")
    parcial.unlink(missing_ok=True)
    origen, copia = conectar(db, True), sqlite3.connect(parcial)
    try:
        origen.backup(copia)
    finally:
        copia.close()
        origen.close()
    parcial.chmod(0o600)
    parcial.replace(destino)


def reiniciar_kuma() -> None:
    subprocess.run(
        ["docker", "restart", CONTENEDOR], check=True, stdout=subprocess.DEVNULL
    )


def token_nuevo() -> str:
    return "".join(secrets.choice(ALFABETO) for _ in range(32))


def id_aviso(con: sqlite3.Connection) -> int:
    fila = con.execute(
        "SELECT id FROM notification WHERE name = ? ORDER BY id", (AVISO,)
    ).fetchone()
    if fila is None:
        sys.exit(f"no existe el aviso {AVISO!r} en Kuma")
    return fila["id"]


def copiado_de_plantilla(con: sqlite3.Connection, aviso: int) -> dict:
    fila = con.execute("SELECT * FROM monitor WHERE name = ?", (PLANTILLA,)).fetchone()
    if fila is None:
        fila = con.execute(
            "SELECT * FROM monitor WHERE type = 'push' ORDER BY id"
        ).fetchone()
    if fila is not None:
        return {
            k: fila[k]
            for k in fila.keys()
            if k not in SIN_COPIAR and fila[k] is not None
        }
    usuario = con.execute(
        "SELECT user_id FROM notification WHERE id = ?", (aviso,)
    ).fetchone()["user_id"]
    return {"type": "push", "user_id": usuario}


def asegurar_monitor(
    con: sqlite3.Connection, nombre: str, aviso: int, base: dict
) -> str:
    fila = con.execute(
        "SELECT id, push_token FROM monitor WHERE name = ? ORDER BY id", (nombre,)
    ).fetchone()
    token = (fila["push_token"] if fila else None) or token_nuevo()
    ajustes = {
        "type": "push",
        "active": 1,
        "interval": 300,
        "retry_interval": 60,
        "maxretries": 2,
        "push_token": token,
    }
    if fila is None:
        columnas = {**base, "name": nombre, **ajustes}
        con.execute(
            f"INSERT INTO monitor ({', '.join(columnas)}) VALUES ({', '.join('?' * len(columnas))})",
            list(columnas.values()),
        )
        monitor = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    else:
        monitor = fila["id"]
        con.execute(
            f"UPDATE monitor SET {', '.join(f'{k} = ?' for k in ajustes)} WHERE id = ?",
            [*ajustes.values(), monitor],
        )
    ya = con.execute(
        "SELECT 1 FROM monitor_notification WHERE monitor_id = ? AND notification_id = ?",
        (monitor, aviso),
    ).fetchone()
    if ya is None:
        con.execute(
            "INSERT INTO monitor_notification (monitor_id, notification_id) VALUES (?, ?)",
            (monitor, aviso),
        )
    return token


def alta(db: Path) -> None:
    respaldar(db)
    con = conectar(db)
    tokens = {}
    con.execute("BEGIN IMMEDIATE")
    try:
        aviso = id_aviso(con)
        base = copiado_de_plantilla(con, aviso)
        for clave, nombre in MONITORES.items():
            tokens[clave] = asegurar_monitor(con, nombre, aviso, base)
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()
    reiniciar_kuma()
    for clave, token in tokens.items():
        print(clave, token)


def baja(db: Path) -> None:
    respaldar(db)
    con = conectar(db)
    con.execute("BEGIN IMMEDIATE")
    try:
        marcas = ", ".join("?" * len(MONITORES))
        con.execute(
            f"UPDATE monitor SET active = 0 WHERE name IN ({marcas})",
            list(MONITORES.values()),
        )
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()
    reiniciar_kuma()


def estado(db: Path) -> None:
    con = conectar(db, True)
    for clave, nombre in MONITORES.items():
        fila = con.execute(
            """
            SELECT m.active AS active,
              (SELECT count(*) FROM monitor_notification mn JOIN notification n ON n.id = mn.notification_id
                WHERE mn.monitor_id = m.id AND n.name = ? AND n.active = 1) AS avisos,
              (SELECT count(*) FROM heartbeat h
                WHERE h.monitor_id = m.id AND h.status = 1 AND h.time >= datetime('now', '-3 minutes')) AS senales
            FROM monitor m WHERE m.name = ? ORDER BY m.id LIMIT 1
            """,
            (AVISO, nombre),
        ).fetchone()
        print(clave, *(tuple(fila) if fila else (0, 0, 0)))
    con.close()


if __name__ == "__main__":
    os.umask(0o077)
    db, modo = Path(sys.argv[1]), sys.argv[2]
    {"alta": alta, "baja": baja, "estado": estado}[modo](db)
