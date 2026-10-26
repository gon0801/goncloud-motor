"""Marcador de formato fuera del event log.

El launcher lo lee antes de escribir. No se borra al revertir el binario.
Una intencion sin efecto resuelto impide volver a un escritor que no la entiende.
"""

from __future__ import annotations

import ast
import json
import os
import time
from pathlib import Path

from . import plataforma

VERSION = 3
NOMBRE = "motor09"


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
    """Eleva el marcador existente hasta VERSION antes de semántica nueva. Nunca lo rebaja."""
    admite_escritor(home)
    data = leer(home)
    if _ya_esta(data):
        return
    lock = _toma(home)
    try:
        admite_escritor(home)
        data = leer(home)
        if _ya_esta(data):
            return
        if data is not None and int(data.get("version", 0)) > VERSION:
            raise FormatoIncompatible(
                f"el home {home} esta en formato {data.get('version')}; "
                f"este motor habla hasta {VERSION} y no va a escribir"
            )
        p = ruta(home)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name("formato.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(
                json.dumps({"version": VERSION, "nombre": NOMBRE}, ensure_ascii=False)
            )
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
        plataforma.sincroniza_directorio(p.parent)
    finally:
        lock.close()


def _ya_esta(data: dict | None) -> bool:
    return (
        data is not None
        and not data.get("corrupto")
        and int(data.get("version", 0)) == VERSION
    )


def _toma(home: Path):
    for _ in range(100):
        lock = plataforma.candado(Path(home) / "formato.lock")
        if lock is not None:
            return lock
        time.sleep(0.02)
    raise FormatoIncompatible(f"no pude tomar el candado de formato en {home}")


def limite_declarado(texto: str) -> int | None:
    """VERSION entera del fuente candidato. None si ese codigo no la fija."""
    try:
        tree = ast.parse(texto)
    except SyntaxError:
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "VERSION":
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, SyntaxError):
                    return None
                if type(value) is int:
                    return value
                return None
    return None


def destino_compatible(
    version_home: int | None, limite_codigo: int | None
) -> tuple[str, str]:
    """Que hacer antes de cambiar el codigo. Nunca autoriza rebajar un home en formato 3+.

    directo: el candidato entiende el marcador. puede_volver: el marcador no se pudo
    leer (-1) o el codigo es viejo y el home es < 3; el motor instalado mira los
    efectos. niega: no se toca codigo, marcador, eventos ni retencion.
    """
    if version_home == -1:
        return (
            "puede_volver",
            "el marcador no se pudo leer; lo decide el motor que ya esta instalado",
        )
    if version_home is not None and version_home < 0:
        return (
            "niega",
            (
                f"el marcador trae una version invalida ({version_home}); "
                "no rebajo el marcador ni borro eventos"
            ),
        )
    if version_home is None:
        return "directo", "sin marcador"
    if limite_codigo is not None and limite_codigo >= version_home:
        return "directo", "el codigo entiende el formato del home"
    if version_home >= 3:
        if limite_codigo is None:
            return (
                "niega",
                (
                    f"el home esta en formato {version_home} y el codigo no declara formato; "
                    "no rebajo el marcador ni borro eventos"
                ),
            )
        return (
            "niega",
            (
                f"el codigo habla hasta {limite_codigo} y el home esta en formato {version_home}; "
                "no rebajo el marcador"
            ),
        )
    return (
        "puede_volver",
        "el codigo es anterior al formato 3; hace falta ver los efectos",
    )


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
