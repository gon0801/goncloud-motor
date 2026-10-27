"""Candado compartido por el supervisor y la reversa independiente."""

from __future__ import annotations

import errno
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

_cerrojo_hilo = threading.RLock()
_cerrojo_nivel = threading.local()


@contextmanager
def _cerrojo_flota(home: Path):
    path = Path(home) / "flota.lock"
    clave = str(path.resolve())
    with _cerrojo_hilo:
        niveles = getattr(_cerrojo_nivel, "bases", {})
        _cerrojo_nivel.bases = niveles
        if niveles.get(clave, 0):
            niveles[clave] += 1
            try:
                yield
            finally:
                niveles[clave] -= 1
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as fh:
            if os.name == "nt":
                import msvcrt

                while True:
                    try:
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as exc:
                        if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                            raise
                        time.sleep(0.05)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            niveles[clave] = 1
            try:
                yield
            finally:
                niveles.pop(clave)
                if os.name == "nt":
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
