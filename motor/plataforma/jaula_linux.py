"""Jaula de Linux con bubblewrap, la misma que concede la de Windows.

Solo stdlib y sin importar nada del motor: en la PC este archivo viaja como fuente (`-c`) a
la distro de WSL, igual que `GUARDIAN_WSL`, y allí no hay ni el motor ni su entorno.

La raíz del agente es un tmpfs de solo lectura. Entra lo que hace falta para correr
(`/usr`, `/etc`, el ejecutable del agente) y lo que el encargo concede:

- escritura: el espacio del encargo y su runtime. El revisor monta el espacio de solo lectura;
- lectura: las fuentes autorizadas;
- nada del home real ni de `/mnt/c`: no se monta, no existe dentro.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

BWRAP = "/usr/bin/bwrap"
RED_ABIERTA = "abierta"
RED_NINGUNA = "ninguna"
PATH_JAULA = "/usr/local/bin:/usr/bin:/bin"
DIRECTORIOS_DEL_SISTEMA = ("bin", "sbin", "lib", "lib32", "lib64", "libx32")


@dataclass(frozen=True)
class Jaula:
    espacio: str
    runtime: str
    lectura: bool = False
    lecturas: tuple = ()
    ejecutables: tuple = ()
    red: str = RED_ABIERTA


def _absoluta(ruta: str) -> str:
    if not ruta.startswith("/"):
        raise ValueError(f"la jaula necesita rutas absolutas: {ruta!r}")
    return os.path.normpath(ruta)


def _sistema() -> list:
    """Lo que el agente necesita para correr, de solo lectura. Sigue al anfitrión: en un
    Ubuntu con `/usr` unificado `/bin` y `/lib` son enlaces; en otro, directorios."""
    montajes = ["--ro-bind", "/usr", "/usr"]
    for nombre in DIRECTORIOS_DEL_SISTEMA:
        ruta = "/" + nombre
        if os.path.islink(ruta):
            montajes += ["--symlink", os.readlink(ruta), ruta]
        elif os.path.isdir(ruta):
            montajes += ["--ro-bind", ruta, ruta]
    return montajes + _etc()


def _etc(origen: str = "/etc") -> list:
    """`/etc` de solo lectura, entrada por entrada. En WSL `resolv.conf` es un enlace a
    `/mnt/wsl/resolv.conf`; montar `/etc` entero lo deja colgando, y montar su destino donde
    está haría existir `/mnt` en la jaula. Se monta el archivo real sobre `/etc/resolv.conf`."""
    montajes = ["--dir", "/etc"]
    for nombre in sorted(os.listdir(origen)):
        ruta, destino = os.path.join(origen, nombre), "/etc/" + nombre
        if nombre == "resolv.conf" and os.path.islink(ruta):
            real = os.path.realpath(ruta)
            if os.path.isfile(real):
                montajes += ["--ro-bind", real, destino]
        elif os.path.islink(ruta):
            montajes += ["--symlink", os.readlink(ruta), destino]
        elif os.path.isdir(ruta) or os.path.isfile(ruta):
            montajes += ["--ro-bind", ruta, destino]
    return montajes


def _ejecutable(ruta: str) -> list:
    """Un enlace se monta como enlace y su destino de solo lectura: el agente ve la ruta
    que le dieron sin que entre el directorio que la contiene."""
    real = os.path.realpath(ruta)
    if real.startswith("/usr/") and ruta.startswith("/usr/"):
        return []
    montajes = ["--ro-bind", real, real]
    if real != ruta:
        montajes += ["--symlink", real, ruta]
    return montajes


def argv_jaula(
    jaula: Jaula, cmd: list, *, cwd: str | None = None, bwrap: str = BWRAP
) -> list:
    """El argv que corre `cmd` dentro de la jaula. Lo arma y no lo ejecuta.

    `--remount-ro /` no es recursivo: un tmpfs montado debajo (`/tmp`) seguiría con
    escritura y bwrap crea ahí los padres de cada bind. Por eso no hay `--tmpfs`: el
    temporal del agente es `TMPDIR`, dentro del runtime."""
    espacio, runtime = _absoluta(jaula.espacio), _absoluta(jaula.runtime)
    if jaula.red not in (RED_ABIERTA, RED_NINGUNA):
        raise ValueError(f"red desconocida: {jaula.red!r}")
    if runtime == espacio or runtime.startswith(espacio + "/"):
        raise ValueError("el runtime no puede estar dentro del espacio")
    argv = [bwrap, "--die-with-parent", "--new-session", "--unshare-all"]
    if jaula.red == RED_ABIERTA:
        argv.append("--share-net")
    argv += _sistema()
    argv += ["--proc", "/proc", "--dev", "/dev"]
    for ruta in jaula.ejecutables:
        argv += _ejecutable(_absoluta(ruta))
    for ruta in jaula.lecturas:
        ruta = _absoluta(ruta)
        argv += ["--ro-bind", ruta, ruta]
    argv += ["--bind", runtime, runtime]
    argv += ["--ro-bind" if jaula.lectura else "--bind", espacio, espacio]
    argv += ["--remount-ro", "/", "--chdir", _absoluta(cwd or espacio), "--"]
    return argv + list(cmd)


def entorno_jaula(jaula: Jaula, extra: dict | None = None) -> dict:
    """Entorno mínimo: nada heredado del anfitrión. El home del agente es su runtime."""
    runtime = _absoluta(jaula.runtime)
    temporal = runtime + "/tmp"
    entorno = {
        "PATH": PATH_JAULA,
        "HOME": runtime,
        "TMPDIR": temporal,
        "TEMP": temporal,
        "TMP": temporal,
        "XDG_CONFIG_HOME": runtime + "/.config",
        "XDG_DATA_HOME": runtime + "/.local/share",
        "LANG": "C.UTF-8",
        "TERM": "dumb",
    }
    entorno.update(extra or {})
    return entorno


def disponible(bwrap: str = BWRAP) -> str:
    """Vacío si bubblewrap puede crear una jaula aquí; si no, la causa. Un kernel sin
    espacios de nombres de usuario, o una política que los veda, falla en esta prueba y
    no en el primer encargo."""
    if not os.access(bwrap, os.X_OK):
        return f"no está {bwrap}: instalar bubblewrap"
    try:
        hecho = subprocess.run(
            [bwrap, "--unshare-all", *_sistema(), "--proc", "/proc", "--dev", "/dev",
             "--", "/usr/bin/true"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"{bwrap} no corre: {type(exc).__name__}: {exc}"
    if hecho.returncode:
        detalle = " ".join((hecho.stderr or "").split())[:300]
        return f"{bwrap} no puede crear una jaula (codigo {hecho.returncode}): {detalle}"
    return ""
