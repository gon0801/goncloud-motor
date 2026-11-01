"""Copia de un árbol sobre un destino que ya existe, sin depender de su permiso de escritura.

`shutil.copytree(dirs_exist_ok=True)` abre cada archivo del destino para escribir.
El git de snapshot de opencode guarda sus objetos en modo 0444: la primera copia los
deja así y la segunda falla con EACCES (en Windows, con el atributo de solo lectura).
Aquí un archivo igual se deja como está y uno distinto se reemplaza por otro nuevo.
"""

from __future__ import annotations

import filecmp
import os
import shutil
import stat
import uuid
from pathlib import Path


def _escribible(ruta: Path) -> None:
    try:
        modo = ruta.lstat().st_mode
        if not stat.S_ISLNK(modo):
            os.chmod(ruta, modo | stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def _quitar(ruta: Path) -> None:
    if ruta.is_symlink() or ruta.is_file():
        _escribible(ruta.parent)
        _escribible(ruta)
        ruta.unlink()
    elif ruta.is_dir():
        for raiz, _carpetas, _archivos in os.walk(ruta):
            _escribible(Path(raiz))
        shutil.rmtree(ruta)


def _igual(origen: Path, destino: Path) -> bool:
    a, b = origen.stat(), destino.stat()
    if a.st_size != b.st_size:
        return False
    return a.st_mtime_ns == b.st_mtime_ns or filecmp.cmp(origen, destino, shallow=False)


def _archivo(origen: Path, destino: Path) -> None:
    if destino.is_symlink() or destino.is_dir():
        _quitar(destino)
    if not destino.exists():
        shutil.copy2(origen, destino)
        return
    if _igual(origen, destino):
        return
    temporal = destino.with_name(f".{destino.name}.{uuid.uuid4().hex}.nuevo")
    try:
        shutil.copy2(origen, temporal)
        _escribible(destino)
        os.replace(temporal, destino)
    finally:
        if temporal.exists():
            temporal.unlink()


def copiar_arbol(origen: Path, destino: Path, omitir: tuple[str, ...] = ()) -> None:
    """Deja en `destino` lo que hay en `origen`. Los enlaces se copian como enlaces.

    Sigue con los demás archivos si uno falla y lo informa al final en un `shutil.Error`,
    igual que `copytree`.
    """
    errores: list[tuple[str, str, str]] = []
    destino.mkdir(parents=True, exist_ok=True)
    for raiz, carpetas, archivos in os.walk(origen):
        carpetas[:] = [c for c in carpetas if c not in omitir]
        relativa = Path(raiz).relative_to(origen)
        aqui = destino / relativa
        enlaces = [c for c in carpetas if (Path(raiz) / c).is_symlink()]
        carpetas[:] = [c for c in carpetas if c not in enlaces]
        for carpeta in carpetas:
            try:
                nueva = aqui / carpeta
                if nueva.is_symlink() or (nueva.exists() and not nueva.is_dir()):
                    _quitar(nueva)
                nueva.mkdir(exist_ok=True)
                _escribible(nueva)
            except OSError as exc:
                errores.append((str(Path(raiz) / carpeta), str(aqui / carpeta), str(exc)))
        for nombre in [*archivos, *enlaces]:
            if nombre in omitir:
                continue
            fuente, meta = Path(raiz) / nombre, aqui / nombre
            try:
                if fuente.is_symlink():
                    if meta.is_symlink() or meta.exists():
                        _quitar(meta)
                    os.symlink(os.readlink(fuente), meta, target_is_directory=fuente.is_dir())
                else:
                    _archivo(fuente, meta)
            except OSError as exc:
                errores.append((str(fuente), str(meta), str(exc)))
    if errores:
        raise shutil.Error(errores)
