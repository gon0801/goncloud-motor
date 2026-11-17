"""Rutas del encargo: las lecturas de cerrados no recrean carpetas borradas."""

from pathlib import Path

from motor.io import leer_texto


def _directorio(base: Path, encargo_id: str, *, crear: bool = True) -> Path:
    raiz = base / "ejecucion" / encargo_id
    if crear:
        raiz.mkdir(parents=True, exist_ok=True)
    actual = raiz / "actual"
    if actual.is_file():
        nombre = leer_texto(actual).strip()
        if nombre.startswith("intento-") and "/" not in nombre and "\\" not in nombre:
            return raiz / nombre
    return raiz


def _directorio_lectura(base: Path, encargo_id: str) -> Path:
    return _directorio(base, encargo_id, crear=False)


def _espacio(base: Path, encargo_id: str, *, crear: bool = True) -> Path:
    path = _espacio_lectura(base, encargo_id)
    if crear:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _espacio_lectura(base: Path, encargo_id: str) -> Path:
    return base / "espacio" / encargo_id
