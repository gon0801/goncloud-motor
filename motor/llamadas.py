"""Las llamadas de un agente a otros CLIs de modelos (paneles de poteto-mode: interrogate, arena, swarm).

Un agente puede pedirle una segunda opinión a otro modelo. Esa llamada pasa por un shim del motor
(`motor/shims/<cli>`, ver `llamada_guard.py`) que:

- la deja pasar al CLI real si la cuenta tiene lugar y no está en pausa de cuota;
- la cuenta en los topes (agentes por máquina y por cuenta, los mismos que usa `machine`);
- la registra en la carpeta del intento (`llamadas/*.json`: modelo, cuenta, inicio y fin);
- no deja llamar a otro modelo desde dentro de una llamada (profundidad 1).

El libro de cuentas vive en `<home>/llamadas/`: un archivo por agente o llamada viva. Solo stdlib y
`plataforma`: el shim corre en el entorno del agente, sin el daemon.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from . import plataforma

CLIS = ("claude", "kimi", "opencode", "grok", "muse", "qwen")
SIN_LUGAR = "esa cuenta no tiene lugar ahora; usa otro modelo"
ANIDADA = (
    "no se puede llamar a otro modelo desde dentro de una llamada a otro modelo; "
    "usa tus propios subagentes"
)


def libro(home: Path) -> Path:
    return home / "llamadas"


def registra(home: Path, clave: str, rec: dict) -> None:
    d = libro(home)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{clave}.tmp"
    tmp.write_text(json.dumps(rec), encoding="utf-8")
    os.replace(tmp, d / f"{clave}.json")


def quita(home: Path, clave: str) -> None:
    try:
        (libro(home) / f"{clave}.json").unlink()
    except OSError:
        pass


def vivos(home: Path) -> list[dict]:
    """Agentes y llamadas que siguen corriendo. Los de un proceso muerto se borran del libro."""
    out = []
    for f in sorted(libro(home).glob("*.json")) if libro(home).is_dir() else ():
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
            vivo = plataforma.pid_vivo(int(rec["pid"]))
        except (OSError, ValueError, KeyError):
            continue
        if vivo:
            out.append(rec)
        else:
            f.unlink(missing_ok=True)
    return out


def por_cuenta(home: Path, solo_llamadas: bool = False) -> dict[str, int]:
    n: dict[str, int] = {}
    for r in vivos(home):
        if solo_llamadas and r.get("tipo") != "llamada":
            continue
        n[r["cuenta"]] = n.get(r["cuenta"], 0) + 1
    return n


def pausas(home: Path) -> dict[str, float]:
    """cuenta -> hasta cuándo está en pausa de cuota (el último `Cooldown` del log de eventos)."""
    db = home / "motor.db"
    if not db.exists():
        return {}
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            filas = con.execute(
                "SELECT data FROM events WHERE kind = 'Cooldown'"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return {}
    out: dict[str, float] = {}
    for (data,) in filas:
        d = json.loads(data)
        out[d["cuenta"]] = max(out.get(d["cuenta"], 0), float(d["hasta"]))
    return out


def elige_ruta(cli: str, args: list[str], rutas: dict) -> tuple[str, str]:
    """(modelo, cuenta) de una llamada a `cli`: la ruta cuyo `--model`/`-m` coincide, o la primera del CLI."""
    pedido = ""
    for i, a in enumerate(args):
        if a in ("--model", "-m") and i + 1 < len(args):
            pedido = args[i + 1]
        elif a.startswith("--model="):
            pedido = a.split("=", 1)[1]
    cand = [(mo, r) for mo, r in rutas.items() if r["cli"] == cli]
    for mo, r in cand:
        if pedido and r.get("modelo_arg") == pedido:
            return mo, r["cuenta"]
    if cand:
        return cand[0][0], cand[0][1]["cuenta"]
    return cli, cli


def rechazo(home: Path, cupos: dict, cuenta: str, ahora: float | None = None) -> str:
    """ "" si la cuenta tiene lugar; si no, el motivo (el mensaje que ve el agente)."""
    ahora = time.time() if ahora is None else ahora
    if pausas(home).get(cuenta, 0) > ahora:
        return SIN_LUGAR
    ocupados = por_cuenta(home)
    if ocupados.get(cuenta, 0) >= cupos.get("max_parallel", {}).get(cuenta, 1):
        return SIN_LUGAR
    tope = int(cupos.get("agentes_por_maquina", 0))
    if tope and sum(ocupados.values()) >= tope:
        return SIN_LUGAR
    return ""


@contextmanager
def cerrojo(home: Path, espera_s: float = 10.0):
    """Exclusión mutua entre shims: comprobar el lugar y anotar la llamada es un solo paso."""
    libro(home).mkdir(parents=True, exist_ok=True)
    f = libro(home) / ".cerrojo"
    fin = time.time() + espera_s
    while True:
        try:
            os.close(os.open(f, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - f.stat().st_mtime > espera_s:  # de un shim que murió
                    f.unlink(missing_ok=True)
                    continue
            except OSError:
                continue
            if time.time() > fin:
                break
            time.sleep(0.02)
    try:
        yield
    finally:
        f.unlink(missing_ok=True)
