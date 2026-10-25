"""Marcador de formato fuera del event log.

El launcher lo lee antes de escribir. No se borra al revertir el binario.
Una intencion sin efecto resuelto impide volver a un escritor que no la entiende.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

VERSION = 2
NOMBRE = "motor08"


class FormatoIncompatible(RuntimeError):
    """Este codigo no puede escribir un home marcado con un formato mas nuevo."""


def ruta(home: Path) -> Path:
    return Path(home) / "formato"


def leer(home: Path) -> dict | None:
    """None si no hay marcador. Un archivo corrupto se trata como mas nuevo."""
    p = ruta(home)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": VERSION + 1, "corrupto": True}
    if not isinstance(data, dict) or not isinstance(data.get("version"), int):
        return {"version": VERSION + 1, "corrupto": True}
    return data


def admite_escritor(home: Path) -> None:
    """Niegua escribir si el home ya pide un formato que este codigo no habla."""
    data = leer(home)
    if data is not None and int(data.get("version", 0)) > VERSION:
        raise FormatoIncompatible(
            f"el home {home} esta en formato {data.get('version')}; "
            f"este motor habla hasta {VERSION} y no va a escribir"
        )


def activa(home: Path) -> None:
    """Escribe el marcador una sola vez, justo antes de la primera intencion."""
    admite_escritor(home)
    p = ruta(home)
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name("formato.tmp")
    tmp.write_text(
        json.dumps({"version": VERSION, "nombre": NOMBRE}, ensure_ascii=False), encoding="utf-8"
    )
    os.replace(tmp, p)


def puede_volver(home: Path, eventos) -> tuple[bool, str]:
    """True solo si un escritor viejo no repetiria un efecto a ciegas."""
    data = leer(home)
    if data is None:
        return True, "sin formato nuevo"
    if int(data.get("version", 0)) > VERSION:
        return False, "el formato del home es mas nuevo que este codigo"
    ultimo, pedidos = {}, []
    for e in eventos:
        _junta(e, ultimo, pedidos)
    for ident in pedidos:
        res = ultimo.get(ident)
        if res is None:
            return False, f"la intencion {ident} no tiene efecto observado"
        if res not in {"aplicado", "no_aplicado"}:
            return False, f"la intencion {ident} quedo {res or 'sin resultado'}"
    return True, "efectos resueltos"


def _junta(e, ultimo: dict, pedidos: list) -> None:
    kind = getattr(e, "kind", "")
    payload = getattr(e, "data", {}) or {}
    if kind == "Intencion":
        ident = (getattr(e, "plan", ""), str(payload.get("id") or ""))
        if ident[1] and ident not in pedidos:
            pedidos.append(ident)
    elif kind == "EfectoObservado":
        ident = (getattr(e, "plan", ""), str(payload.get("intencion") or ""))
        if ident[1]:
            ultimo[ident] = str(payload.get("resultado") or "")
