"""Propuesta de fases que el implementador deja en su espacio.

El usuario entrega texto normal. El implementador escribe `.interno/fases.json`
y el supervisor lo adopta como un hecho del registro; el archivo no acredita
por sí solo autorización ni aprobación.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .dominio import Encargo
from .registro import Registro

NOMBRE = "fases.json"
ERROR = "fases.error.txt"


def ruta(interno: Path) -> Path:
    return interno / NOMBRE


def _dependencias(item: dict, ids: set[str], fase_id: str) -> list[dict]:
    crudo = item.get("requiere") or []
    if not isinstance(crudo, (list, tuple)):
        raise ValueError("requiere debe ser una lista de fases")
    salida = []
    for dep in crudo:
        if isinstance(dep, dict):
            objetivo = str(dep.get("fase") or "").strip()
            version = str(dep.get("version") or "").strip()
        else:
            objetivo, version = str(dep or "").strip(), ""
        if not objetivo:
            raise ValueError("requiere contiene una fase vacía")
        if objetivo == fase_id:
            raise ValueError(f"la fase {fase_id} no puede requerirse a sí misma")
        if objetivo not in ids:
            raise ValueError(f"la fase {fase_id} requiere una fase inexistente: {objetivo}")
        salida.append({"fase": objetivo, "version": version})
    return salida


def leer_propuesta(interno: Path) -> tuple[dict, ...] | None:
    """Devuelve la propuesta, None si no existe, o lanza ValueError con el defecto.

    El defecto describe qué corregir en el archivo; no se convierte en una
    pregunta al usuario del encargo.
    """
    path = ruta(interno)
    if not path.is_file():
        return None
    try:
        datos = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"{NOMBRE} no es JSON válido: {exc}") from exc
    crudo = datos.get("fases") if isinstance(datos, dict) else datos
    if not isinstance(crudo, (list, tuple)) or not crudo:
        raise ValueError(f"{NOMBRE} necesita una lista no vacía de fases")
    ids: set[str] = set()
    for indice, item in enumerate(crudo):
        if not isinstance(item, dict):
            raise ValueError(f"{NOMBRE}[{indice}] debe ser un objeto")
        fase_id = str(item.get("id") or "").strip()
        if not fase_id or not all(c.isalnum() or c in "-_" for c in fase_id):
            raise ValueError(
                f"{NOMBRE}[{indice}] necesita un id de letras, números, guion o guion bajo"
            )
        if fase_id in ids:
            raise ValueError(f"{NOMBRE} repite el id {fase_id}")
        ids.add(fase_id)
        if not str(item.get("resultado") or "").strip():
            raise ValueError(f"{NOMBRE}[{indice}] necesita un resultado observable")
    propuesta = []
    for item in crudo:
        fase_id = str(item["id"]).strip()
        propuesta.append(
            {
                "id": fase_id,
                "resultado": " ".join(str(item["resultado"]).split()),
                "requiere": _dependencias(item, ids, fase_id),
            }
        )
    return tuple(propuesta)


def adoptar(
    registro: Registro, enc: Encargo, interno: Path, hora: str
) -> tuple[Encargo, str]:
    """Registra la propuesta. Devuelve el encargo y el defecto, si lo hubo."""
    error_path = interno / ERROR
    try:
        propuesta = leer_propuesta(interno)
    except ValueError as exc:
        error_path.write_text(str(exc) + "\n", encoding="utf-8")
        return enc, str(exc)
    if propuesta is None:
        return enc, ""
    huella = hashlib.sha256(
        json.dumps(list(propuesta), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]
    resultado = registro.anotar(
        enc.id,
        {
            "kind": "fases",
            "data": {"fases": list(propuesta), "hora": hora},
        },
        f"fases:{enc.id}:{huella}",
    )
    if resultado == "nuevo" or resultado == "igual":
        error_path.unlink(missing_ok=True)
        return registro.fold(enc.id) or enc, ""
    defecto = (
        "la propuesta de fases no se pudo adoptar; debe conservar las fases ya "
        "trabajadas y no dejar dependencias circulares ni fases inexistentes"
    )
    error_path.write_text(defecto + "\n", encoding="utf-8")
    return registro.fold(enc.id) or enc, defecto
