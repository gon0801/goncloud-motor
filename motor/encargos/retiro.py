"""Separa al motor de encargos del motor viejo, en la máquina donde corre.

    python -m motor.encargos.retiro migrar  [--base B]
    python -m motor.encargos.retiro retirar [--base B] [--mirar]

`migrar` deja en la base el `maquina.toml` del motor viejo, con `conf` en su ruta
real, y le suma las llaves de `~/.motor/secretos.env` que la base no tenga. Los dos
quedan como archivos propios de la base, aunque antes fueran enlaces a las carpetas
viejas. Se puede correr las veces que haga falta.

`retirar` borra `~/.motor`, `~/motor` y `~/.motor-arranque` y los enlaces de HOME
que apuntan adentro, y en la PC da de baja la tarea `GoncloudMotor`. Se niega mientras
borrar rompa algo: la base sin su configuración, un supervisor que todavía lee
`~/.motor` o un despliegue en curso. Con `--mirar` solo dice lo que haría.

Nunca imprime el valor de un secreto, solo el nombre de la llave.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from motor import plataforma
from motor.effects import RETIRO_EN_CURSO
from motor.io import escribir_texto, leer_json, leer_texto

from .maquina import de_base

CARPETAS_VIEJAS = (".motor", "motor", ".motor-arranque")
# Solo existe en un árbol que ya lee la configuración de máquina de la base.
MODULO_DEL_CAMBIO = "motor/encargos/maquina.py"
PASADA_VIGENTE = timedelta(minutes=30)
TAREA_PC = "GoncloudMotor"
SERVICIO_VIEJO = "com.goncloud.motor"
PLIST_VIEJO = Path("/Library/LaunchDaemons") / f"{SERVICIO_VIEJO}.plist"


def _correr(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60
    )


def _lineas_de_secretos(path: Path) -> dict[str, str]:
    """Llave -> línea, con la última línea de cada llave como `load_secretos`."""
    if not path.exists():
        return {}
    lineas = {}
    for linea in leer_texto(path).splitlines():
        limpia = linea.strip()
        if limpia and not limpia.startswith("#") and "=" in limpia:
            lineas[limpia.split("=", 1)[0].strip()] = limpia
    return lineas


def _escribir_solo_dueno(path: Path, texto: str) -> None:
    temporal = path.with_name(path.name + ".tmp")
    fd = os.open(temporal, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(texto.encode("utf-8"))
    os.chmod(temporal, 0o600)
    os.replace(temporal, path)


def _con_conf(texto: str, conf: str) -> str:
    lineas = texto.splitlines()
    for i, linea in enumerate(lineas):
        clave = linea.split("=", 1)[0].strip()
        if linea.lstrip().startswith("["):
            break
        if "=" in linea and clave == "conf":
            lineas[i] = "conf = " + json.dumps(conf, ensure_ascii=False)
            return "\n".join(lineas) + "\n"
    raise ValueError("maquina.toml no tiene una línea conf = ... antes de sus tablas")


def _migrar_maquina(viejo: Path, base: Path, carpetas: list[Path]) -> str:
    destino = base / "maquina.toml"
    origen = destino if destino.is_file() else viejo / "maquina.toml"
    if not origen.is_file():
        raise ValueError(f"no hay maquina.toml ni en la base {base} ni en {viejo}")
    texto = leer_texto(origen)
    datos = tomllib.loads(texto)
    try:
        real = str(Path(datos["conf"]).expanduser().resolve(strict=True))
    except (KeyError, TypeError, OSError) as exc:
        raise ValueError(f"la conf de {origen} no existe: {exc}") from exc
    nuevo = texto if datos["conf"] == real else _con_conf(texto, real)
    if tomllib.loads(nuevo) != {**datos, "conf": real}:
        raise ValueError(f"no pude cambiar conf en {origen} sin tocar lo demás")
    enlazado = origen == destino and _dentro(destino, carpetas)
    if origen == destino and nuevo == texto and not enlazado:
        return f"maquina.toml: ya estaba en la base (conf = {real})"
    temporal = destino.with_name(destino.name + ".tmp")
    escribir_texto(temporal, nuevo)
    os.replace(temporal, destino)
    if origen != destino:
        hecho = f"copiado de {viejo}"
    elif enlazado:
        hecho = "dependía de las carpetas viejas y ahora es un archivo propio de la base"
    else:
        hecho = "corregido en la base"
    return f"maquina.toml: {hecho}, con conf = {real}"


def _migrar_secretos(viejo: Path, base: Path, carpetas: list[Path]) -> str:
    destino = base / "secretos.env"
    propias = _lineas_de_secretos(destino)
    traidas = _lineas_de_secretos(viejo / "secretos.env")
    faltan = [llave for llave in traidas if llave not in propias]
    repetidas = [llave for llave in traidas if llave in propias]
    enlazado = destino.exists() and _dentro(destino, carpetas)
    if faltan or enlazado:
        previo = leer_texto(destino) if destino.exists() else ""
        if previo and not previo.endswith("\n"):
            previo += "\n"
        _escribir_solo_dueno(destino, previo + "".join(traidas[k] + "\n" for k in faltan))
    elif destino.exists() and os.name == "posix":
        os.chmod(destino, 0o600)
    partes = [f"agregadas {', '.join(faltan)}" if faltan else "no faltaba ninguna llave"]
    if enlazado:
        partes.append("dependía de las carpetas viejas y ahora es un archivo propio de la base")
    if repetidas:
        partes.append(f"queda el valor de la base para {', '.join(repetidas)}")
    return "secretos.env: " + "; ".join(partes)


def migrar(home: Path, base: Path) -> list[str]:
    viejo = home / ".motor"
    carpetas = [home / nombre for nombre in CARPETAS_VIEJAS]
    base.mkdir(parents=True, exist_ok=True)
    return [_migrar_maquina(viejo, base, carpetas), _migrar_secretos(viejo, base, carpetas)]


def _es_enlace(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    union = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None)
    return stat.S_ISLNK(st.st_mode) or (
        union is not None and getattr(st, "st_reparse_tag", 0) == union
    )


def _destino(enlace: Path) -> Path | None:
    try:
        destino = os.readlink(enlace)
    except OSError:
        return None
    if destino.startswith("\\\\?\\"):
        destino = destino[4:]
    return Path(os.path.abspath(enlace.parent / destino))


def _recorrido(ruta: Path) -> list[Path]:
    """Cada ruta por la que pasa `ruta` hasta su destino final, enlace por enlace."""
    vistas = [Path(os.path.abspath(ruta))]
    for _ in range(40):
        partes = vistas[-1].parts
        for i in range(1, len(partes) + 1):
            prefijo = Path(*partes[:i])
            destino = _destino(prefijo) if _es_enlace(prefijo) else None
            if destino is not None:
                vistas.append(Path(os.path.abspath(Path(destino, *partes[i:]))))
                break
        else:
            break
    return vistas


def _dentro(ruta: Path, raices: list[Path]) -> bool:
    """`ruta` deja de funcionar si se borran `raices`: alguna ruta de su recorrido cae adentro."""
    raices = [
        forma for r in raices
        for forma in (Path(os.path.abspath(r)), Path(os.path.realpath(r.parent)) / r.name)
    ]
    return any(v == r or r in v.parents for v in _recorrido(ruta) for r in raices)


def _entradas(path: Path) -> list[os.DirEntry]:
    try:
        with os.scandir(path) as it:
            return list(it)
    except OSError:
        return []


def _enlaces_adentro(home: Path, carpetas: list[Path]) -> list[Path]:
    """Enlaces en todo HOME que apuntan a lo que se borra, sin entrar en enlaces ni en lo que se borra.

    En Windows una unión no es `is_symlink()`: ahí cada entrada pasa por `_es_enlace`."""
    enlaces, pendientes = [], [home]
    while pendientes:
        for entrada in _entradas(pendientes.pop()):
            ruta = Path(entrada.path)
            if ruta in carpetas:
                continue
            if _es_enlace(ruta) if os.name == "nt" else entrada.is_symlink():
                if _dentro(ruta, carpetas):
                    enlaces.append(ruta)
            elif entrada.is_dir(follow_symlinks=False):
                pendientes.append(ruta)
    return sorted(enlaces)


def _causa_maquina(base: Path, borrar: list[Path]) -> list[str]:
    try:
        maquina = de_base(base)
    except ValueError as exc:
        return [str(exc)]
    rutas = {maquina.conf: "corre migrar"}
    rutas.update((Path(r), "cámbialo en maquina.toml de la base") for r in maquina.path_extra)
    return [
        f"maquina.toml de la base usa {r}, que depende de lo que se borraría: {arreglo}"
        for r, arreglo in rutas.items() if _dentro(r.expanduser(), borrar)
    ]


def _causa_base(base: Path, borrar: list[Path]) -> list[str]:
    return [
        f"{base / nombre} depende de lo que se borraría: corre migrar"
        for nombre in ("maquina.toml", "secretos.env")
        if os.path.lexists(base / nombre) and _dentro(base / nombre, borrar)
    ]


def _causa_secretos(home: Path, base: Path) -> list[str]:
    faltan = sorted(
        set(_lineas_de_secretos(home / ".motor" / "secretos.env"))
        - set(_lineas_de_secretos(base / "secretos.env"))
    )
    if not faltan:
        return []
    return [f"a secretos.env de la base le faltan {', '.join(faltan)}: corre migrar"]


def _causa_supervisor(base: Path, borrar: list[Path], correr, ahora: datetime) -> list[str]:
    try:
        pasada = leer_json(base / "supervisor-pasada.json")
        sha, ruta, pid = str(pasada["sha"]), Path(str(pasada["ruta"])), int(pasada["pid"])
        hora = datetime.fromisoformat(str(pasada["hora"]).replace("Z", "+00:00"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"no hay una pasada legible del supervisor en {base / 'supervisor-pasada.json'}: {exc}"]
    if ahora - hora > PASADA_VIGENTE or not plataforma.pid_vivo(pid):
        return [f"el supervisor no está corriendo: su última pasada es de {pasada['hora']} (pid {pid})"]
    if _dentro(ruta, borrar):
        return [f"el supervisor corre desde {ruta}, dentro de lo que se borraría"]
    r = correr(["git", "-C", str(ruta), "cat-file", "-e", f"{sha}:{MODULO_DEL_CAMBIO}"])
    if r.returncode != 0:
        return [
            f"el supervisor corre {sha[:12]}, que todavía lee ~/.motor: "
            "despliega este cambio y espera su primera pasada"
        ]
    return []


def _causa_motor_viejo(home: Path) -> list[str]:
    """El daemon viejo sostiene ~/.motor/motor.lock mientras corre."""
    candado = home / ".motor" / "motor.lock"
    if not candado.is_file():
        return []
    with open(candado, "rb") as f:
        if plataforma.candado_exclusivo(f):
            return []
    return [f"el motor viejo sigue corriendo (tiene tomado {candado}): apágalo primero"]


@contextlib.contextmanager
def _sin_despliegues(base: Path, retiro: bool):
    """Sostiene, sin esperar, cada candado de despliegue libre de la base; da los ocupados.

    Con `retiro` toma antes el candado del retiro, y un despliegue que empiece mientras lo
    tiene espera (`effects._esperar_retiro`). Solo mirando no escribe en la base. Cada
    candado vale mientras su archivo siga abierto, como en `plataforma.candado`."""
    carpeta = base / "locks-despliegue"
    ocupados = []
    with contextlib.ExitStack() as pila:
        if retiro:
            carpeta.mkdir(parents=True, exist_ok=True)
            f = pila.enter_context(open(carpeta / RETIRO_EN_CURSO, "a+"))
            if not plataforma.candado_exclusivo(f):
                ocupados.append(RETIRO_EN_CURSO)
        for ruta in sorted(carpeta.glob("*.lock")):
            f = pila.enter_context(open(ruta, "rb"))
            if not plataforma.candado_exclusivo(f):
                ocupados.append(ruta.name)
        yield ocupados


def _quitar_enlace(path: Path) -> None:
    if os.name == "nt" and os.path.isdir(path):
        os.rmdir(path)
    else:
        os.unlink(path)


def _forzar(funcion, path, _exc) -> None:
    os.chmod(path, stat.S_IWRITE)
    funcion(path)


def _quitar_enlaces_dentro(carpeta: Path) -> None:
    """Quita cada enlace o unión de `carpeta` sin seguirlo, como `~/.motor/conf` de la PC, para
    que `rmtree` solo vea archivos y carpetas propios."""
    pendientes = [carpeta]
    while pendientes:
        for entrada in _entradas(pendientes.pop()):
            ruta = Path(entrada.path)
            if _es_enlace(ruta):
                _quitar_enlace(ruta)
            elif entrada.is_dir(follow_symlinks=False):
                pendientes.append(ruta)


def _borrar(path: Path) -> None:
    if _es_enlace(path):
        _quitar_enlace(path)
        return
    _quitar_enlaces_dentro(path)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_forzar)
    else:
        shutil.rmtree(path, onerror=_forzar)


def retirar(
    home: Path, base: Path, mirar: bool, correr=_correr, ahora: datetime | None = None
) -> tuple[int, list[str]]:
    ahora = ahora or datetime.now(timezone.utc)
    carpetas = [home / nombre for nombre in CARPETAS_VIEJAS]
    presentes = [c for c in carpetas if os.path.lexists(c)]
    enlaces = _enlaces_adentro(home, carpetas)
    borrar = carpetas + enlaces
    with _sin_despliegues(base, retiro=not mirar) as ocupados:
        causas = [
            *_causa_maquina(base, borrar),
            *_causa_base(base, borrar),
            *_causa_motor_viejo(home),
            *_causa_secretos(home, base),
            *_causa_supervisor(base, borrar, correr, ahora),
            *[f"hay un despliegue en curso en esta máquina ({n})" for n in ocupados],
        ]
        if _dentro(Path(__file__), borrar):
            causas.append(f"esta herramienta corre desde {Path(__file__).parent}, dentro de lo que se borraría")
        tarea = os.name == "nt" and correr(["schtasks", "/Query", "/TN", TAREA_PC]).returncode == 0
        plist = sys.platform == "darwin" and PLIST_VIEJO.exists()
        modo = " (solo mirar)" if mirar else ""
        lineas = [f"retiro del motor viejo en {socket.gethostname()}{modo}"]
        if causas:
            lineas += ["se niega a borrar:", *[f"  - {c}" for c in causas]]
        verbo = "borraría" if mirar or causas else "borrado"
        lineas += [f"{verbo}: {c}" for c in presentes]
        lineas += [f"{verbo} el enlace: {e} -> {_destino(e)}" for e in enlaces]
        if tarea:
            lineas.append(f"{verbo} la tarea programada {TAREA_PC}")
        if not (presentes or enlaces or tarea):
            lineas.append("no queda nada del motor viejo en esta máquina")
        if causas or mirar:
            if plist:
                lineas.append(f"después, a mano: {_comando_sudo()}")
            return (1 if causas else 0), lineas
        if tarea:
            r = correr(["schtasks", "/Delete", "/TN", TAREA_PC, "/F"])
            if r.returncode != 0:
                lineas.append(f"no pude dar de baja {TAREA_PC}: {(r.stderr or r.stdout).strip()[:300]}")
                return 1, lineas
        for enlace in enlaces:
            _quitar_enlace(enlace)
        for carpeta in presentes:
            _borrar(carpeta)
    if plist:
        lineas.append(f"falta borrar el servicio viejo; pide tu contraseña: {_comando_sudo()}")
    return 0, lineas


def _comando_sudo() -> str:
    return f"sudo launchctl bootout system/{SERVICIO_VIEJO}; sudo rm -f {PLIST_VIEJO}"


def main(argv: list[str] | None = None) -> int:
    if os.name == "nt" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m motor.encargos.retiro", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("accion", choices=("migrar", "retirar"))
    ap.add_argument("--base", type=Path, default=Path.home() / ".motor-encargos")
    ap.add_argument("--home", type=Path, default=Path.home(), help="donde viven las carpetas viejas")
    ap.add_argument("--mirar", action="store_true", help="solo dice lo que haría")
    args = ap.parse_args(argv)
    home, base = args.home.expanduser(), args.base.expanduser()
    try:
        if args.accion == "migrar":
            if args.mirar:
                ap.error("--mirar es solo para retirar")
            codigo, lineas = 0, migrar(home, base)
        else:
            print(f"buscando enlaces a las carpetas viejas en {home}; puede tardar unos minutos",
                  file=sys.stderr)
            codigo, lineas = retirar(home, base, args.mirar)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"{args.accion}: {exc}", file=sys.stderr)
        return 2
    print("\n".join(lineas))
    return codigo


if __name__ == "__main__":
    raise SystemExit(main())
