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

import json
import os
import subprocess
import sys
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


def _descendientes(raiz: int) -> list:
    """Pids bajo `raiz`, vistos desde el anfitrión, por `/proc/<pid>/stat`."""
    padres = {}
    for nombre in os.listdir("/proc"):
        if not nombre.isdigit():
            continue
        try:
            with open(f"/proc/{nombre}/stat", "rb") as f:
                resto = f.read().rsplit(b")", 1)[1].split()
            padres[int(nombre)] = int(resto[1])
        except (OSError, IndexError, ValueError):
            continue
    vistos, frontera = [], [raiz]
    while frontera:
        hijos = [p for p, padre in padres.items() if padre in frontera and p not in vistos]
        vistos += hijos
        frontera = hijos
    return vistos


def _vivo(pid: int) -> bool:
    """Un zombi ya murió: solo espera que su padre lo recoja."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            return f.read().rsplit(b")", 1)[1].split()[0] != b"Z"
    except (OSError, IndexError):
        return False


def _congelar(pid: int, vistos: dict) -> None:
    """SIGSTOP a `pid` y a todo lo que cuelga de él, hasta que un recorrido no encuentre nada
    nuevo. Un proceso detenido no puede hacer fork: sin esto, un hijo nacido entre el recorrido
    y el SIGKILL quedaría huérfano, fuera del árbol, y nadie lo vería."""
    import signal

    for _ in range(16):
        nuevos = [p for p in (pid, *_descendientes(pid)) if p not in vistos]
        if not nuevos:
            return
        for p in nuevos:
            vistos[p] = _inicio(p)
            try:
                os.kill(p, signal.SIGSTOP)
            except OSError:
                pass


def cerrar(pid: int, plazo: float = 5.0, inicio: str | None = None) -> bool:
    """Mata la jaula que lanzó el bwrap `pid` y espera a que no quede nada. True si no queda.

    No basta con matar al bwrap exterior: con el bubblewrap 0.9 de Ubuntu 24.04 la jaula
    sigue viva tras su SIGKILL (`--die-with-parent` no es una garantía; CI lo midió). Se
    congela el árbol entero, se anota la identidad (pid y hora de arranque) de cada proceso
    y se mata a todos. Que no queden descendientes de `pid` no prueba que esté cerrada: un
    proceso reparentado a otro padre ya no cuelga de él. Por eso lo que cuenta es que ninguno
    de los anotados siga vivo.

    Con `inicio` (la hora de arranque del bwrap que se lanzó), un `pid` que ya es otro proceso
    no se toca: el bwrap ya salió y su pid pudo reusarse."""
    import signal
    import time

    if inicio and _inicio(pid) != inicio:
        return True
    fin = time.monotonic() + plazo
    vistos: dict = {}
    while True:
        _congelar(pid, vistos)
        for p in vistos:
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        vivos = [p for p, inicio in vistos.items() if _inicio(p) == inicio and _vivo(p)]
        if not vivos and not _descendientes(pid):
            return True
        if time.monotonic() >= fin:
            return False
        time.sleep(0.02)


def disponible(bwrap: str = BWRAP) -> bool:
    """True si bubblewrap puede crear una jaula aquí; igual que `disponible` de las jaulas de
    Windows. `causa_no_disponible` dice por qué no."""
    return not causa_no_disponible(bwrap)


def causa_no_disponible(bwrap: str = BWRAP) -> str:
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


# --- Guardián dentro de la distro -------------------------------------------------------------
# El supervisor corre en Windows y lanza `wsl.exe --exec python3 -I -S -c <este archivo> guardian
# <spec>`. El guardián corre FUERA de la jaula, con el usuario real de la distro: lee la credencial
# de Claude de su `~/.claude`, arma la jaula, la lanza y la cierra entera si el padre desaparece,
# si le llega una señal de parada o si vence el plazo. Un Job de Windows no ve nada de esto.
# Salidas: 124 = parada, plazo o padre muerto; 125 = no pudo cerrar la jaula; 126 = no pudo
# prepararla (credencial); 127 = falta el ejecutable.

SALIDA_PARADA = 124
SALIDA_SIN_CERRAR = 125
SALIDA_SIN_PREPARAR = 126
SALIDA_SIN_EJECUTABLE = 127
ARCHIVO_CONTROL = "jaula-wsl.json"
CLAUDE_EN_EL_HOME = ".local/bin/claude"


def _inicio(pid: int) -> str:
    """Hora de arranque del proceso en ticks (campo 22 de `/proc/<pid>/stat`), o vacío.
    Distingue un pid reusado."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            return f.read().rsplit(b")", 1)[1].split()[19].decode()
    except (OSError, IndexError):
        return ""


def _publica(ruta: str, documento: dict) -> None:
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(documento, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ruta)


def ejecutable_de_claude(entorno: dict | None = None) -> str:
    """Donde está Claude Code en la distro: `MOTOR_WSL_CLAUDE`, `~/.local/bin/claude` o el PATH."""
    import shutil

    entorno = os.environ if entorno is None else entorno
    candidatos = [
        entorno.get("MOTOR_WSL_CLAUDE", ""),
        os.path.join(os.path.expanduser("~"), CLAUDE_EN_EL_HOME),
    ]
    for ruta in candidatos:
        if ruta and os.path.isfile(ruta) and os.access(ruta, os.X_OK):
            return ruta
    return shutil.which("claude") or ""


def _cuenta_de_claude(home: str) -> dict:
    """La suscripción de Claude de la distro. Levanta ValueError con la causa si no sirve."""
    ruta = os.path.join(home, ".claude", ".credentials.json")
    try:
        with open(ruta, encoding="utf-8") as f:
            cuenta = json.load(f).get("claudeAiOauth") or {}
    except (OSError, ValueError, AttributeError):
        raise ValueError(f"no se puede leer la suscripcion de Claude en {ruta}") from None
    if not cuenta.get("accessToken") or "user:inference" not in (cuenta.get("scopes") or []):
        raise ValueError("la suscripcion de Claude de la distro no da acceso de inferencia")
    return cuenta


def credencial_de_claude(ejecutable: str, home: str | None = None) -> dict:
    """Entorno que lleva la suscripción a la jaula: el token de acceso, sin el de renovación.
    Si el acceso venció se renueva con el cliente oficial, fuera de la jaula, como en Windows."""
    import time

    home = home or os.path.expanduser("~")
    cuenta = _cuenta_de_claude(home)
    expira = float(cuenta.get("expiresAt") or 0) / 1000
    if expira and expira <= time.time():
        try:
            subprocess.run(
                [ejecutable, "--print", "--safe-mode"], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise ValueError("no se pudo renovar la suscripcion de Claude en la distro") from None
        cuenta = _cuenta_de_claude(home)
        expira = float(cuenta.get("expiresAt") or 0) / 1000
        if expira and expira <= time.time():
            raise ValueError("Claude no pudo renovar el acceso de suscripcion en la distro")
    entorno = {
        "CLAUDE_CODE_OAUTH_TOKEN": cuenta["accessToken"],
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
    }
    if expira:
        entorno["MOTOR_CLAUDE_ACCESS_EXPIRES_AT"] = str(expira)
    return entorno


def _preparar_runtime(runtime: str, credencial: str, ejecutable: str) -> dict:
    for sub in ("tmp", ".config", ".local/share", ".claude"):
        os.makedirs(os.path.join(runtime, sub), exist_ok=True)
    entorno = {
        "CLAUDE_CODE_TMPDIR": runtime + "/tmp",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if credencial == "claude":
        entorno.update(credencial_de_claude(ejecutable))
        entorno["CLAUDE_CONFIG_DIR"] = runtime + "/.claude"
        _publica(
            runtime + "/.claude/settings.json", {"skipDangerousModePermissionPrompt": True}
        )
    return entorno


def _guardian(spec: dict) -> int:
    import ctypes
    import signal
    import time

    try:
        ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER
    except Exception:  # noqa: BLE001
        pass
    parado = []
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, lambda *_: parado.append(sig))
    padre = os.getppid()
    plazo = spec.get("plazo")
    fin = None if plazo is None else time.monotonic() + float(plazo)

    cmd = list(spec["cmd"])
    jaula_spec = spec["jaula"]
    ejecutables = list(jaula_spec.get("ejecutables") or ())
    ejecutable = ""
    if cmd[0] == "claude":
        ejecutable = ejecutable_de_claude()
        if not ejecutable:
            print("falta Claude Code en la distro (MOTOR_WSL_CLAUDE o ~/.local/bin/claude)",
                  file=sys.stderr)
            return SALIDA_SIN_EJECUTABLE
        cmd[0] = ejecutable
        ejecutables.append(ejecutable)
    try:
        extra = _preparar_runtime(jaula_spec["runtime"], spec.get("credencial", ""), ejecutable)
    except (OSError, ValueError) as exc:
        print(f"Cuenta de claude: {exc}", file=sys.stderr)
        return SALIDA_SIN_PREPARAR
    extra.update(spec.get("env") or {})
    jaula = Jaula(
        espacio=jaula_spec["espacio"], runtime=jaula_spec["runtime"],
        lectura=bool(jaula_spec.get("lectura")),
        lecturas=tuple(jaula_spec.get("lecturas") or ()),
        ejecutables=tuple(ejecutables), red=jaula_spec.get("red", RED_ABIERTA),
    )
    control = spec.get("control")
    argv = argv_jaula(jaula, cmd, cwd=spec.get("cwd"))
    try:
        proceso = subprocess.Popen(
            argv, env=entorno_jaula(jaula, extra), stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        print(f"no se pudo lanzar la jaula ({BWRAP}): {exc}", file=sys.stderr)
        return SALIDA_SIN_PREPARAR
    identidad = {
        "guardian": os.getpid(), "guardian_inicio": _inicio(os.getpid()),
        "bwrap": proceso.pid, "bwrap_inicio": _inicio(proceso.pid), "estado": "activo",
    }
    if control:
        _publica(os.path.join(control, ARCHIVO_CONTROL), identidad)
    codigo = None
    while codigo is None:
        codigo = proceso.poll()
        if codigo is not None:
            break
        if parado or (fin is not None and time.monotonic() >= fin) or (
            os.getppid() != padre or not os.path.exists(f"/proc/{padre}")
        ):
            codigo = SALIDA_PARADA
            break
        time.sleep(0.02)
    cerrada = cerrar(proceso.pid, inicio=identidad["bwrap_inicio"])
    if control:
        _publica(
            os.path.join(control, ARCHIVO_CONTROL),
            {**identidad, "estado": "cerrado" if cerrada else "incierto"},
        )
    return codigo if cerrada else SALIDA_SIN_CERRAR


def cerrar_identidad(identidad: dict, plazo: float = 5.0) -> bool:
    """Mata la jaula y al guardián que describe `identidad` (el archivo de control), si siguen
    siendo esos procesos. True si no queda nada vivo."""
    import signal
    import time

    pid = int(identidad.get("bwrap") or 0)
    if pid and _inicio(pid) == identidad.get("bwrap_inicio"):
        cerrar(pid)
    guardian = int(identidad.get("guardian") or 0)
    if guardian and _inicio(guardian) == identidad.get("guardian_inicio"):
        try:
            os.kill(guardian, signal.SIGKILL)
        except OSError:
            pass
    fin = time.monotonic() + plazo
    while vivo_identidad(identidad):
        if time.monotonic() >= fin:
            return False
        time.sleep(0.02)
    return True


def vivo_identidad(identidad: dict) -> bool:
    for clave in ("guardian", "bwrap"):
        pid = int(identidad.get(clave) or 0)
        if pid and _inicio(pid) == identidad.get(clave + "_inicio") and _vivo(pid):
            return True
    return False


def _causa_de_claude() -> str:
    ejecutable = ejecutable_de_claude()
    if not ejecutable:
        return "falta Claude Code en la distro (MOTOR_WSL_CLAUDE o ~/.local/bin/claude)"
    try:
        _cuenta_de_claude(os.path.expanduser("~"))
    except ValueError as exc:
        return str(exc)
    return ""


def _principal(argv: list) -> int:
    orden = argv[0] if argv else ""
    if orden == "guardian":
        return _guardian(json.loads(argv[1]))
    if orden in ("cerrar", "estado"):
        identidad = json.loads(argv[1])
        if orden == "cerrar":
            cerrar_identidad(identidad)
        print(json.dumps({"vivo": vivo_identidad(identidad)}))
        return 0
    if orden == "disponible":
        causa = causa_no_disponible()
        if not causa and argv[1:2] == ["claude"]:
            causa = _causa_de_claude()
        print(json.dumps({"causa": causa}))
        return 0
    print(f"orden desconocida: {orden!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_principal(sys.argv[1:]))
