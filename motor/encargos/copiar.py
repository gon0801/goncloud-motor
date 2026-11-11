"""Copia de un árbol sobre un destino que ya existe, sin depender de su permiso de escritura.

`shutil.copytree(dirs_exist_ok=True)` abre cada archivo del destino para escribir.
El git de snapshot de opencode guarda sus objetos en modo 0444: la primera copia los
deja así y la segunda falla con EACCES (en Windows, con el atributo de solo lectura).
Aquí un archivo de igual contenido se deja como está y uno distinto se reemplaza por otro nuevo.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import uuid
from contextlib import closing
from pathlib import Path


def _escribible(ruta: Path) -> None:
    try:
        modo = ruta.lstat().st_mode
        if not stat.S_ISLNK(modo):
            os.chmod(ruta, modo | stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def _reintentar_sin_solo_lectura(funcion, ruta, excinfo) -> None:
    """Un archivo de solo lectura no se puede borrar en Windows. Se le quita el
    atributo y se reintenta ese mismo archivo; si aun así falla, el error sube.
    Se usa `onerror` porque `onexc` no existe en el Python 3.11 de la PC."""
    exc = excinfo[1] if isinstance(excinfo, tuple) else excinfo
    if isinstance(exc, FileNotFoundError):
        return
    if funcion not in (os.unlink, os.remove, os.rmdir):
        raise exc
    ruta = Path(ruta)
    _escribible(ruta)
    _escribible(ruta.parent)
    try:
        funcion(ruta)
    except FileNotFoundError:
        pass


def _quitar(ruta: Path) -> None:
    """Borra un archivo, enlace o árbol aunque tenga el permiso de solo lectura."""
    if ruta.is_symlink() or ruta.is_file():
        _escribible(ruta.parent)
        _escribible(ruta)
        ruta.unlink()
    elif ruta.is_dir():
        shutil.rmtree(ruta, onerror=_reintentar_sin_solo_lectura)


def _igual(origen: Path, destino: Path) -> bool:
    """Igual solo si el contenido es igual: tamaño y mtime no bastan, porque una base
    SQLite reescrita conserva el tamaño y el reloj del archivo avanza a saltos."""
    if origen.stat().st_size != destino.stat().st_size:
        return False
    with open(origen, "rb") as a, open(destino, "rb") as b:
        while True:
            trozo = a.read(1 << 20)
            if trozo != b.read(len(trozo)):
                return False
            if not trozo:
                return True


_CABECERA_SQLITE = b"SQLite format 3\x00"
_LATERALES_SQLITE = ("-wal", "-shm", "-journal")


def es_sqlite(ruta: Path) -> bool:
    """True si el archivo empieza con la cabecera de una base SQLite. Por cabecera
    y no por extensión: la misma regla cubre `opencode.db`, `session-index.db` y
    `state_*.sqlite`, y un `.db` que no es base se copia como un archivo cualquiera."""
    try:
        with open(ruta, "rb") as f:
            return f.read(len(_CABECERA_SQLITE)) == _CABECERA_SQLITE
    except OSError:
        return False


def _copiar_sqlite(origen: Path, destino: Path) -> None:
    """Deja en `destino` una sola base consistente con `origen`, con el API de
    respaldo de SQLite: la foto incluye lo que el WAL ya comprometió sin
    checkpoint, y los laterales `-wal`/`-shm` no viajan ni quedan viejos en el
    destino (un `-wal` de otra generación junto a una base nueva fue lo que
    dañó la sesión guardada en la PC).

    El respaldo se escribe a un temporal con permiso privado y reemplaza al
    destino solo si terminó: una copia que falla deja la base anterior y sus
    laterales como estaban."""
    temporal = destino.with_name(f".{destino.name}.{uuid.uuid4().hex}.nuevo")
    try:
        os.close(os.open(temporal, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        with closing(sqlite3.connect(origen)) as fuente, closing(
            sqlite3.connect(temporal)
        ) as copia:
            fuente.backup(copia)
        try:
            shutil.copymode(origen, temporal)
        except OSError:
            pass
    except BaseException:
        try:
            temporal.unlink()
        except OSError:
            pass
        raise
    for sufijo in _LATERALES_SQLITE:
        try:
            destino.with_name(destino.name + sufijo).unlink(missing_ok=True)
        except OSError:
            pass
    _escribible(destino)
    os.replace(temporal, destino)


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


def copiar_arbol(
    origen: Path, destino: Path, omitir: tuple[str, ...] = (), sqlite_consistente: bool = False
) -> None:
    """Deja en `destino` lo que hay en `origen`. Los enlaces se copian como enlaces.

    Sigue con los demás archivos si uno falla y lo informa al final en un `shutil.Error`,
    igual que `copytree`.

    Con `sqlite_consistente`, cada base SQLite se copia con el API de respaldo en una
    sola base y sus laterales `-wal`/`-shm`/`-journal` no se copian: una base en WAL
    llevada archivo por archivo no es una foto consistente."""
    errores: list[tuple[str, str, str]] = []
    destino.mkdir(parents=True, exist_ok=True)
    def ilegible(exc: OSError) -> None:
        errores.append((str(exc.filename), "", str(exc)))

    for raiz, carpetas, archivos in os.walk(origen, onerror=ilegible):
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
            if sqlite_consistente and nombre.endswith(_LATERALES_SQLITE):
                continue
            fuente, meta = Path(raiz) / nombre, aqui / nombre
            try:
                if fuente.is_symlink():
                    if meta.is_symlink() or meta.exists():
                        _quitar(meta)
                    os.symlink(os.readlink(fuente), meta, target_is_directory=fuente.is_dir())
                elif sqlite_consistente and es_sqlite(fuente):
                    _copiar_sqlite(fuente, meta)
                else:
                    _archivo(fuente, meta)
            except (OSError, sqlite3.Error) as exc:
                errores.append((str(fuente), str(meta), str(exc)))
    if errores:
        raise shutil.Error(errores)


def vaciar_arbol(ruta: Path) -> None:
    """Deja `ruta` como directorio vacío, aunque contenga archivos de solo lectura."""
    _quitar(ruta)
    ruta.mkdir(parents=True, exist_ok=True)


def borrar_arbol(ruta: Path) -> None:
    """Borra `ruta` y su contenido, aunque haya archivos de solo lectura."""
    if ruta.exists() or ruta.is_symlink():
        _quitar(ruta)
