"""Retención durable del home. No usa SQLite, red ni el candado vitalicio del daemon.

El archivo es `mantenimiento.json`. Una liberación solo borra la generación que nombra.
Un archivo ilegible cuenta como retenido y no se pisa.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import plataforma

ARCHIVO = "mantenimiento.json"
_CANDADO = "mantenimiento.lock"
_VERSION = 1


class AunNo(RuntimeError):
    """La generación coincide, pero el árbol propio sigue vivo."""


@dataclass(frozen=True)
class Hold:
    version: int
    generation: str
    request_id: str
    created_at: str
    reason: str
    cause: str = ""


def leer(home: Path) -> Hold | None:
    """None si no hay archivo. Corrupto o ilegible sigue retenido, con causa."""
    p = Path(home) / ARCHIVO
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Hold(_VERSION, "", "", "", "", cause="la retención no se puede leer")
    if not isinstance(data, dict):
        return Hold(_VERSION, "", "", "", "", cause="la retención no es un objeto")
    generation = data.get("generation")
    request_id = data.get("request_id")
    created_at = data.get("created_at")
    reason = data.get("reason")
    version = data.get("version")
    if (
        not isinstance(version, int)
        or not isinstance(generation, str)
        or not generation
        or not isinstance(request_id, str)
        or not request_id
        or not isinstance(created_at, str)
        or not created_at
        or not isinstance(reason, str)
    ):
        return Hold(_VERSION, "", "", "", "", cause="la retención está corrupta")
    return Hold(version, generation, request_id, created_at, reason)


def activar(home: Path, request_id: str, reason: str) -> Hold:
    """Crea la retención si no existe. Si ya hay una, incluida una corrupta, la devuelve igual."""
    if not request_id:
        raise ValueError("la retención necesita un id de solicitud")
    with _candado(home):
        actual = leer(home)
        if actual is not None:
            return actual
        hold = Hold(
            _VERSION,
            uuid.uuid4().hex,
            request_id,
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            reason or "recuperacion",
        )
        _escribe(home, hold)
        return hold


def liberar(home: Path, expected_generation: str, observed_state: dict) -> str:
    """Borra solo si la generación vigente es `expected_generation` y no quedan árboles.

    Devuelve `liberado`, `distinta` o `sin`. Una generación vieja no toca el archivo.
    """
    if not isinstance(observed_state, dict) or observed_state.get("arboles") != "ausentes":
        raise AunNo("todavía hay árboles propios")
    with _candado(home):
        actual = leer(home)
        if actual is None:
            return "sin"
        if actual.cause or actual.generation != expected_generation:
            return "distinta"
        path = Path(home) / ARCHIVO
        path.unlink()
        plataforma.sincroniza_directorio(Path(home))
        return "liberado"


def _escribe(home: Path, hold: Hold) -> None:
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    tmp = home / f".{ARCHIVO}.tmp"
    payload = {
        "version": hold.version,
        "generation": hold.generation,
        "request_id": hold.request_id,
        "created_at": hold.created_at,
        "reason": hold.reason,
    }
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, home / ARCHIVO)
    plataforma.sincroniza_directorio(home)


@contextmanager
def _candado(home: Path):
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    tomado = None
    for _ in range(100):
        tomado = plataforma.candado(home / _CANDADO)
        if tomado is not None:
            break
        time.sleep(0.02)
    if tomado is None:
        raise TimeoutError(f"no pude tomar el candado de retención en {home}")
    try:
        yield
    finally:
        tomado.close()
