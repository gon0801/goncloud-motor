"""Lectura y escritura de texto y JSON del motor, en un solo lugar.

- Todo se lee como UTF-8 y se acepta BOM (`utf-8-sig`): en Windows los agentes
  escriben JSON con BOM y `utf-8` lo rechaza.
- Todo se escribe como UTF-8 y con saltos de línea LF, para que un archivo
  generado en Windows no meta CRLF al repo.

Nadie fuera de este módulo llama `read_text` ni `write_text`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def leer_texto(ruta: Path | str, *, errors: str = "strict") -> str:
    """El texto del archivo, sin BOM y con los saltos normalizados a LF."""
    return Path(ruta).read_text(encoding="utf-8-sig", errors=errors)


def leer_json(ruta: Path | str) -> Any:
    """El JSON del archivo, aunque venga con BOM o con CRLF."""
    return json.loads(leer_texto(ruta))


def escribir_texto(ruta: Path | str, texto: str) -> None:
    """Escribe el texto en UTF-8 y con saltos de línea LF."""
    normalizado = texto.replace("\r\n", "\n").replace("\r", "\n")
    with Path(ruta).open("w", encoding="utf-8", newline="\n") as archivo:
        archivo.write(normalizado)


__all__ = ["escribir_texto", "leer_json", "leer_texto"]
